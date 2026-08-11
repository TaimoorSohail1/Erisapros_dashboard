import asyncio
import json
import logging
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Callable

from app.config import get_settings
from app.repositories import get_repository, reset_repository
from app.services.filing_pipeline import process_extraction_batch
from app.services.sharefile import ShareFileService
from app.services.sharefile_queue import ShareFileWorkQueue, get_sharefile_work_queue


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
STALE_POLL_SECONDS = 10 * 60
VISIBILITY_HEARTBEAT_SECONDS = 5 * 60
MAX_RECEIVE_MESSAGES = 10
BURST_DRAIN_WAIT_SECONDS = 2
MAX_BURST_WINDOW_SECONDS = 6


@dataclass
class ExtractionBatchCollector:
    """Collect ShareFile extraction tasks without starting them immediately."""

    packages: list[tuple[str, str, list[dict]]] = field(default_factory=list)

    def add_task(self, func: Callable, *args, **kwargs) -> None:
        if getattr(func, "__name__", "") != "process_extraction_batch" or not args:
            raise RuntimeError("Unexpected ShareFile worker background task.")
        packages = args[0]
        if not isinstance(packages, list):
            raise RuntimeError("ShareFile extraction batch must be a list.")
        self.packages.extend(packages)


@dataclass
class PendingWebhookExtraction:
    """Registered webhook work waiting for the single extraction consumer."""

    packages: list[tuple[str, str, list[dict]]]
    receipt_handles: list[str]
    heartbeats: list[asyncio.Task]


async def dispatch_sharefile_work(
    message: dict,
    service: ShareFileService | None = None,
    background_tasks=None,
) -> dict:
    service = service or ShareFileService()
    work_type = message.get("type")
    if work_type == "poll":
        return await service.poll_folder(None)
    if work_type == "deep_sync":
        return await service.sync_folder(None)
    if work_type == "auto_register":
        return await service.auto_register_relevant_webhooks()
    if work_type == "webhook":
        return await service.handle_webhook(message.get("payload") or {}, background_tasks)
    raise ValueError(f"Unsupported ShareFile work type: {work_type!r}")


async def process_sqs_message(queue: ShareFileWorkQueue, message: dict) -> None:
    body = json.loads(message.get("Body") or "{}")
    receipt_handle = message["ReceiptHandle"]
    sent_timestamp = (message.get("Attributes") or {}).get("SentTimestamp")
    if body.get("type") == "poll" and sent_timestamp:
        age_seconds = time.time() - (int(sent_timestamp) / 1000)
        if age_seconds > STALE_POLL_SECONDS:
            logger.info("Discarding stale ShareFile poll message (age %.0fs).", age_seconds)
            await queue.delete(receipt_handle)
            return

    heartbeat = asyncio.create_task(_visibility_heartbeat(queue, receipt_handle))
    try:
        await dispatch_sharefile_work(body)
        await queue.delete(receipt_handle)
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat


async def process_webhook_batch(
    queue: ShareFileWorkQueue,
    messages: list[dict],
    extraction_queue: asyncio.Queue[PendingWebhookExtraction] | None = None,
) -> None:
    """Register a burst of webhooks, then extract all queued packages together.

    SQS messages are acknowledged only after their associated extraction batch
    finishes. A failed registration or extraction therefore remains available
    for the queue retry/DLQ policy.
    """
    heartbeats = {
        message["ReceiptHandle"]: asyncio.create_task(
            _visibility_heartbeat(queue, message["ReceiptHandle"])
        )
        for message in messages
    }
    extraction_receipts: list[str] = []
    immediate_receipts: list[str] = []
    failed_receipts: list[str] = []
    packages: list[tuple[str, str, list[dict]]] = []
    queued_for_extraction = False

    try:
        for message in messages:
            receipt_handle = message["ReceiptHandle"]
            try:
                body = json.loads(message.get("Body") or "{}")
                collector = ExtractionBatchCollector()
                await dispatch_sharefile_work(body, background_tasks=collector)
                if collector.packages:
                    packages.extend(collector.packages)
                    extraction_receipts.append(receipt_handle)
                else:
                    immediate_receipts.append(receipt_handle)
            except Exception:
                failed_receipts.append(receipt_handle)
                reset_repository()
                logger.exception(
                    "ShareFile webhook registration failed; SQS will retry receipt %s.",
                    receipt_handle,
                )

        for receipt_handle in immediate_receipts:
            await queue.delete(receipt_handle)
            await _cancel_heartbeat(heartbeats[receipt_handle])

        for receipt_handle in failed_receipts:
            await _cancel_heartbeat(heartbeats[receipt_handle])

        if packages:
            pending = PendingWebhookExtraction(
                packages=packages,
                receipt_handles=extraction_receipts,
                heartbeats=[heartbeats[receipt] for receipt in extraction_receipts],
            )
            if extraction_queue is not None:
                await extraction_queue.put(pending)
                queued_for_extraction = True
            else:
                await _run_pending_extractions(queue, [pending])
    finally:
        if not queued_for_extraction:
            for heartbeat in heartbeats.values():
                await _cancel_heartbeat(heartbeat)


async def _cancel_heartbeat(heartbeat: asyncio.Task) -> None:
    if not heartbeat.done():
        heartbeat.cancel()
    with suppress(asyncio.CancelledError):
        await heartbeat


async def _run_pending_extractions(
    queue: ShareFileWorkQueue,
    batches: list[PendingWebhookExtraction],
) -> None:
    packages = [package for batch in batches for package in batch.packages]
    receipt_handles = [receipt for batch in batches for receipt in batch.receipt_handles]
    heartbeats = [heartbeat for batch in batches for heartbeat in batch.heartbeats]
    try:
        await process_extraction_batch(packages)
    except Exception:
        reset_repository()
        logger.exception("ShareFile webhook extraction batch failed; SQS will retry it.")
    else:
        for receipt_handle in receipt_handles:
            await queue.delete(receipt_handle)
    finally:
        for heartbeat in heartbeats:
            await _cancel_heartbeat(heartbeat)


async def process_next_extraction_batch(
    queue: ShareFileWorkQueue,
    extraction_queue: asyncio.Queue[PendingWebhookExtraction],
) -> None:
    """Process one queued extraction group while intake continues independently."""
    first = await extraction_queue.get()
    batches = [first]
    try:
        while True:
            try:
                batches.append(extraction_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        await _run_pending_extractions(queue, batches)
    finally:
        for _batch in batches:
            extraction_queue.task_done()


async def run_extraction_worker(
    queue: ShareFileWorkQueue,
    extraction_queue: asyncio.Queue[PendingWebhookExtraction],
) -> None:
    while True:
        await process_next_extraction_batch(queue, extraction_queue)


async def process_sqs_messages(
    queue: ShareFileWorkQueue,
    messages: list[dict],
    extraction_queue: asyncio.Queue[PendingWebhookExtraction] | None = None,
) -> None:
    webhook_messages: list[dict] = []
    other_messages: list[dict] = []
    for message in messages:
        try:
            body = json.loads(message.get("Body") or "{}")
        except (TypeError, ValueError):
            other_messages.append(message)
            continue
        if body.get("type") == "webhook":
            webhook_messages.append(message)
        else:
            other_messages.append(message)

    if webhook_messages:
        await process_webhook_batch(queue, webhook_messages, extraction_queue)

    for message in other_messages:
        try:
            await process_sqs_message(queue, message)
        except Exception:
            reset_repository()
            logger.exception("ShareFile work failed; SQS will retry it.")


async def receive_message_burst(queue: ShareFileWorkQueue) -> list[dict]:
    """Coalesce upload notifications that arrive within the same short burst."""
    messages = await queue.receive(max_messages=MAX_RECEIVE_MESSAGES)
    burst_started = time.monotonic()
    while (
        messages
        and len(messages) < MAX_RECEIVE_MESSAGES
        and time.monotonic() - burst_started < MAX_BURST_WINDOW_SECONDS
    ):
        more = await queue.receive(
            max_messages=MAX_RECEIVE_MESSAGES - len(messages),
            wait_time_seconds=BURST_DRAIN_WAIT_SECONDS,
        )
        if not more:
            break
        messages.extend(more)
    return messages


async def _visibility_heartbeat(queue: ShareFileWorkQueue, receipt_handle: str) -> None:
    while True:
        await asyncio.sleep(VISIBILITY_HEARTBEAT_SECONDS)
        await queue.change_visibility(receipt_handle)


async def run_worker() -> None:
    settings = get_settings()
    settings.validate_runtime()
    queue = get_sharefile_work_queue()
    if not queue.configured:
        raise RuntimeError("SHAREFILE_WORK_QUEUE_URL is required for the ShareFile worker.")
    await get_repository().ensure_indexes()
    logger.info("ShareFile worker started.")
    extraction_queue: asyncio.Queue[PendingWebhookExtraction] = asyncio.Queue()
    extraction_worker = asyncio.create_task(run_extraction_worker(queue, extraction_queue))
    try:
        while True:
            messages = await receive_message_burst(queue)
            if messages:
                await process_sqs_messages(queue, messages, extraction_queue)
    finally:
        extraction_worker.cancel()
        with suppress(asyncio.CancelledError):
            await extraction_worker


if __name__ == "__main__":
    asyncio.run(run_worker())
