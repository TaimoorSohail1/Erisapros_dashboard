import json
import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from app.services.filing_pipeline import process_extraction_batch as pipeline_extraction_batch
from app.sharefile_worker import (
    _visibility_heartbeat,
    dispatch_sharefile_work,
    process_sqs_message,
    process_sqs_messages,
    process_next_extraction_batch,
    receive_message_burst,
    run_worker,
    run_webhook_registration_loop,
)


class ShareFileWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_webhook_registration_loop_checks_immediately(self):
        service = AsyncMock()
        service.auto_register_relevant_webhooks.return_value = {
            "webhook_roots": 2,
            "registered": 1,
            "failed": 0,
        }
        with (
            patch("app.sharefile_worker.ShareFileService", return_value=service),
            patch("app.sharefile_worker.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)) as sleep,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await run_webhook_registration_loop(3600)

        service.auto_register_relevant_webhooks.assert_awaited_once_with()
        sleep.assert_awaited_once_with(3600)

    async def test_worker_registers_webhooks_on_startup(self):
        queue = AsyncMock()
        queue.configured = True
        registration_started = asyncio.Event()

        async def registration_loop(_interval_seconds):
            registration_started.set()
            await asyncio.Event().wait()

        async def stop_after_registration(_queue):
            await asyncio.wait_for(registration_started.wait(), timeout=1)
            raise asyncio.CancelledError

        with (
            patch("app.sharefile_worker.get_settings") as settings,
            patch("app.sharefile_worker.get_sharefile_work_queue", return_value=queue),
            patch("app.sharefile_worker.get_repository") as repository,
            patch("app.sharefile_worker.run_webhook_registration_loop", side_effect=registration_loop, create=True) as register,
            patch("app.sharefile_worker.receive_message_burst", side_effect=stop_after_registration),
        ):
            repository.return_value.ensure_indexes = AsyncMock()
            settings.return_value.sharefile_webhook_auto_register_enabled = True
            settings.return_value.sharefile_webhook_discovery_interval_seconds = 3600
            with self.assertRaises(asyncio.CancelledError):
                await run_worker()

        settings.return_value.validate_runtime.assert_called_once_with()
        register.assert_called_once_with(3600)
        queue.enqueue.assert_not_awaited()

    async def test_slow_webhook_reconciliation_does_not_block_upload_intake(self):
        queue = AsyncMock()
        queue.configured = True
        registration_started = asyncio.Event()
        upload_processed = asyncio.Event()
        upload = {"Body": json.dumps({"type": "webhook", "payload": {"ItemId": "new-pdf"}}), "ReceiptHandle": "upload"}
        calls = 0

        async def slow_registration(_interval_seconds):
            registration_started.set()
            await asyncio.Event().wait()

        async def receive(_queue):
            nonlocal calls
            await asyncio.wait_for(registration_started.wait(), timeout=1)
            calls += 1
            if calls == 1:
                return [upload]
            await asyncio.wait_for(upload_processed.wait(), timeout=1)
            raise asyncio.CancelledError

        async def process(_queue, messages, _extraction_queue):
            self.assertEqual(messages, [upload])
            upload_processed.set()

        with (
            patch("app.sharefile_worker.get_settings") as settings,
            patch("app.sharefile_worker.get_sharefile_work_queue", return_value=queue),
            patch("app.sharefile_worker.get_repository") as repository,
            patch("app.sharefile_worker.run_webhook_registration_loop", side_effect=slow_registration),
            patch("app.sharefile_worker.receive_message_burst", side_effect=receive),
            patch("app.sharefile_worker.process_sqs_messages", side_effect=process),
        ):
            settings.return_value.sharefile_webhook_auto_register_enabled = True
            settings.return_value.sharefile_webhook_discovery_interval_seconds = 3600
            repository.return_value.ensure_indexes = AsyncMock()
            with self.assertRaises(asyncio.CancelledError):
                await run_worker()

        self.assertTrue(upload_processed.is_set())

    async def test_dispatches_each_supported_work_type(self):
        service = AsyncMock()
        cases = {
            "poll": "poll_folder",
            "deep_sync": "sync_folder",
            "auto_register": "auto_register_relevant_webhooks",
        }
        for work_type, method_name in cases.items():
            service.reset_mock()
            await dispatch_sharefile_work({"type": work_type}, service=service)
            getattr(service, method_name).assert_awaited_once_with(None) if method_name != "auto_register_relevant_webhooks" else getattr(service, method_name).assert_awaited_once_with()

    async def test_dispatches_webhook_payload(self):
        service = AsyncMock()
        payload = {"EventType": "FileUploaded", "ItemId": "item-1"}
        await dispatch_sharefile_work({"type": "webhook", "payload": payload}, service=service)
        service.handle_webhook.assert_awaited_once_with(payload, None)

    async def test_webhook_burst_registers_every_message_before_one_extraction_batch(self):
        queue = AsyncMock()
        messages = [
            {
                "Body": json.dumps({"type": "webhook", "payload": {"ItemId": f"item-{index}"}}),
                "ReceiptHandle": f"receipt-{index}",
            }
            for index in range(10)
        ]
        events = []

        async def register(message, service=None, background_tasks=None):
            index = message["payload"]["ItemId"].split("-")[-1]
            events.append(f"register-{index}")
            background_tasks.add_task(
                pipeline_extraction_batch,
                [(f"filing-{index}", f"job-{index}", [])],
            )
            return {"queued": 1}

        async def extract(packages):
            events.append(f"extract-{len(packages)}")

        with (
            patch("app.sharefile_worker.dispatch_sharefile_work", new=AsyncMock(side_effect=register)),
            patch("app.sharefile_worker.process_extraction_batch", new=AsyncMock(side_effect=extract)) as batch,
        ):
            await process_sqs_messages(queue, messages)

        self.assertEqual(events[:10], [f"register-{index}" for index in range(10)])
        self.assertEqual(events[10], "extract-10")
        self.assertEqual(len(batch.await_args.args[0]), 10)
        self.assertEqual(queue.delete.await_count, 10)

    async def test_failed_webhook_registration_does_not_block_other_messages(self):
        queue = AsyncMock()
        messages = [
            {"Body": json.dumps({"type": "webhook", "payload": {"ItemId": "bad"}}), "ReceiptHandle": "bad"},
            {"Body": json.dumps({"type": "webhook", "payload": {"ItemId": "good"}}), "ReceiptHandle": "good"},
        ]

        async def register(message, service=None, background_tasks=None):
            if message["payload"]["ItemId"] == "bad":
                raise RuntimeError("registration failed")
            return {"queued": 0}

        with patch("app.sharefile_worker.dispatch_sharefile_work", new=AsyncMock(side_effect=register)):
            await process_sqs_messages(queue, messages)

        queue.delete.assert_awaited_once_with("good")

    async def test_failed_combined_extraction_leaves_messages_for_sqs_retry(self):
        queue = AsyncMock()
        message = {
            "Body": json.dumps({"type": "webhook", "payload": {"ItemId": "item-1"}}),
            "ReceiptHandle": "receipt-1",
        }

        async def register(_message, service=None, background_tasks=None):
            background_tasks.add_task(
                pipeline_extraction_batch,
                [("filing-1", "job-1", [])],
            )
            return {"queued": 1}

        with (
            patch("app.sharefile_worker.dispatch_sharefile_work", new=AsyncMock(side_effect=register)),
            patch(
                "app.sharefile_worker.process_extraction_batch",
                new=AsyncMock(side_effect=RuntimeError("batch failed")),
            ),
        ):
            await process_sqs_messages(queue, [message])

        queue.delete.assert_not_awaited()

    async def test_receive_message_burst_drains_until_empty_or_ten(self):
        queue = AsyncMock()
        queue.receive.side_effect = [
            [{"ReceiptHandle": "one"}],
            [{"ReceiptHandle": "two"}, {"ReceiptHandle": "three"}],
            [],
        ]

        messages = await receive_message_burst(queue)

        self.assertEqual([item["ReceiptHandle"] for item in messages], ["one", "two", "three"])
        self.assertEqual(queue.receive.await_count, 3)
        self.assertEqual(queue.receive.await_args_list[0].kwargs, {"max_messages": 10})
        self.assertEqual(
            queue.receive.await_args_list[1].kwargs,
            {"max_messages": 9, "wait_time_seconds": 2},
        )

    async def test_late_webhooks_register_while_prior_extraction_is_running(self):
        """A slow extraction must not stop the worker from registering later uploads."""
        queue = AsyncMock()
        extraction_queue = asyncio.Queue()
        first = {
            "Body": json.dumps({"type": "webhook", "payload": {"ItemId": "item-1"}}),
            "ReceiptHandle": "receipt-1",
        }
        second = {
            "Body": json.dumps({"type": "webhook", "payload": {"ItemId": "item-2"}}),
            "ReceiptHandle": "receipt-2",
        }
        extraction_started = asyncio.Event()
        release_extraction = asyncio.Event()
        registered = []

        async def register(message, service=None, background_tasks=None):
            item_id = message["payload"]["ItemId"]
            registered.append(item_id)
            background_tasks.add_task(
                pipeline_extraction_batch,
                [(f"filing-{item_id}", f"job-{item_id}", [])],
            )
            return {"queued": 1}

        async def extract(_packages):
            extraction_started.set()
            await release_extraction.wait()

        with (
            patch("app.sharefile_worker.dispatch_sharefile_work", new=AsyncMock(side_effect=register)),
            patch("app.sharefile_worker.process_extraction_batch", new=AsyncMock(side_effect=extract)),
            patch("app.sharefile_worker._visibility_heartbeat", new=AsyncMock()),
        ):
            await process_sqs_messages(queue, [first], extraction_queue=extraction_queue)
            extraction_task = asyncio.create_task(
                process_next_extraction_batch(queue, extraction_queue)
            )
            await asyncio.wait_for(extraction_started.wait(), timeout=1)

            # This is the production failure mode: a second upload arrives while
            # the first document is still inside GroundX/FT Williams processing.
            await asyncio.wait_for(
                process_sqs_messages(queue, [second], extraction_queue=extraction_queue),
                timeout=1,
            )

            self.assertEqual(registered, ["item-1", "item-2"])
            self.assertEqual(extraction_queue.qsize(), 1)
            release_extraction.set()
            await extraction_task
            await process_next_extraction_batch(queue, extraction_queue)

        self.assertEqual(queue.delete.await_count, 2)

    async def test_message_is_deleted_only_after_success(self):
        queue = AsyncMock()
        message = {"Body": json.dumps({"type": "poll"}), "ReceiptHandle": "receipt-1"}
        with patch("app.sharefile_worker.dispatch_sharefile_work", new=AsyncMock()) as dispatch:
            await process_sqs_message(queue, message)
        dispatch.assert_awaited_once()
        queue.delete.assert_awaited_once_with("receipt-1")

    async def test_webhook_registration_failure_is_logged_before_acknowledgement(self):
        queue = AsyncMock()
        message = {"Body": json.dumps({"type": "auto_register"}), "ReceiptHandle": "receipt-registration"}
        with patch(
            "app.sharefile_worker.dispatch_sharefile_work",
            new=AsyncMock(return_value={"webhook_roots": 4, "registered": 2, "skipped": 1, "failed": 1}),
        ):
            with self.assertLogs("app.sharefile_worker", level="WARNING") as logs:
                await process_sqs_message(queue, message)

        self.assertTrue(any("registration incomplete" in line for line in logs.output))
        queue.delete.assert_awaited_once_with("receipt-registration")

    async def test_failed_message_is_left_for_sqs_retry(self):
        queue = AsyncMock()
        message = {"Body": json.dumps({"type": "poll"}), "ReceiptHandle": "receipt-1"}
        with patch(
            "app.sharefile_worker.dispatch_sharefile_work",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            with self.assertRaises(RuntimeError):
                await process_sqs_message(queue, message)
        queue.delete.assert_not_awaited()

    async def test_stale_scheduled_poll_is_deleted_without_running(self):
        queue = AsyncMock()
        message = {
            "Body": json.dumps({"type": "poll"}),
            "ReceiptHandle": "receipt-stale",
            "Attributes": {"SentTimestamp": str(int((time.time() - 900) * 1000))},
        }
        with patch("app.sharefile_worker.dispatch_sharefile_work", new=AsyncMock()) as dispatch:
            await process_sqs_message(queue, message)

        dispatch.assert_not_awaited()
        queue.delete.assert_awaited_once_with("receipt-stale")

    async def test_old_webhook_is_never_discarded_as_a_stale_poll(self):
        queue = AsyncMock()
        payload = {"EventType": "FileUploaded", "ItemId": "item-1"}
        message = {
            "Body": json.dumps({"type": "webhook", "payload": payload}),
            "ReceiptHandle": "receipt-webhook",
            "Attributes": {"SentTimestamp": str(int((time.time() - 3600) * 1000))},
        }
        with patch("app.sharefile_worker.dispatch_sharefile_work", new=AsyncMock()) as dispatch:
            await process_sqs_message(queue, message)

        dispatch.assert_awaited_once()
        queue.delete.assert_awaited_once_with("receipt-webhook")

    async def test_visibility_heartbeat_extends_long_running_work(self):
        queue = AsyncMock()
        queue.change_visibility.side_effect = RuntimeError("stop after first heartbeat")
        with patch("app.sharefile_worker.asyncio.sleep", new=AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, "stop after first heartbeat"):
                await _visibility_heartbeat(queue, "receipt-long")
        queue.change_visibility.assert_awaited_once_with("receipt-long")


if __name__ == "__main__":
    unittest.main()
