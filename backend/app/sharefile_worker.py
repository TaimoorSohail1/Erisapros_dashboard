import asyncio
import json
import logging

from app.config import get_settings
from app.repositories import reset_repository
from app.services.sharefile import ShareFileService
from app.services.sharefile_queue import ShareFileWorkQueue, get_sharefile_work_queue


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def dispatch_sharefile_work(message: dict, service: ShareFileService | None = None) -> dict:
    service = service or ShareFileService()
    work_type = message.get("type")
    if work_type == "poll":
        return await service.poll_folder(None)
    if work_type == "deep_sync":
        return await service.sync_folder(None)
    if work_type == "auto_register":
        return await service.auto_register_relevant_webhooks()
    if work_type == "webhook":
        return await service.handle_webhook(message.get("payload") or {}, None)
    raise ValueError(f"Unsupported ShareFile work type: {work_type!r}")


async def process_sqs_message(queue: ShareFileWorkQueue, message: dict) -> None:
    body = json.loads(message.get("Body") or "{}")
    await dispatch_sharefile_work(body)
    await queue.delete(message["ReceiptHandle"])


async def run_worker() -> None:
    settings = get_settings()
    settings.validate_runtime()
    queue = get_sharefile_work_queue()
    if not queue.configured:
        raise RuntimeError("SHAREFILE_WORK_QUEUE_URL is required for the ShareFile worker.")
    logger.info("ShareFile worker started.")
    while True:
        messages = await queue.receive()
        for message in messages:
            try:
                await process_sqs_message(queue, message)
            except Exception:
                # Discard a potentially stale Mongo pool before SQS retries
                # the message. The message remains visible for retry/DLQ.
                reset_repository()
                logger.exception("ShareFile work failed; SQS will retry it.")


if __name__ == "__main__":
    asyncio.run(run_worker())
