import json
import unittest
from unittest.mock import AsyncMock, patch

from app.sharefile_worker import dispatch_sharefile_work, process_sqs_message


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


if __name__ == "__main__":
    unittest.main()
