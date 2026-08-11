import json
import time
import unittest
from unittest.mock import AsyncMock, patch

from app.services.filing_pipeline import process_extraction_batch as pipeline_extraction_batch
from app.sharefile_worker import (
    _visibility_heartbeat,
    dispatch_sharefile_work,
    process_sqs_message,
    process_sqs_messages,
    receive_message_burst,
)


class ShareFileWorkerTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_message_is_deleted_only_after_success(self):
        queue = AsyncMock()
        message = {"Body": json.dumps({"type": "poll"}), "ReceiptHandle": "receipt-1"}
        with patch("app.sharefile_worker.dispatch_sharefile_work", new=AsyncMock()) as dispatch:
            await process_sqs_message(queue, message)
        dispatch.assert_awaited_once()
        queue.delete.assert_awaited_once_with("receipt-1")

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
