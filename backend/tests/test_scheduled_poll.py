import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config import get_settings


class ScheduledPollEndpointTests(unittest.TestCase):
    """The external scheduler (EventBridge) must be able to trigger a ShareFile
    poll with the shared webhook token, without a Cognito login - and nobody
    else may trigger it without that token."""

    def setUp(self):
        os.environ["SHAREFILE_WEBHOOK_TOKEN"] = "test-scheduler-token"
        os.environ["AUTH_ENABLED"] = "true"
        get_settings.cache_clear()
        from app.main import app

        self.client = TestClient(app)

    def tearDown(self):
        os.environ.pop("SHAREFILE_WEBHOOK_TOKEN", None)
        os.environ.pop("AUTH_ENABLED", None)
        get_settings.cache_clear()

    def test_rejects_missing_token(self):
        response = self.client.post("/api/sharefile/poll-scheduled")
        self.assertEqual(response.status_code, 401)

    def test_rejects_wrong_token(self):
        response = self.client.post(
            "/api/sharefile/poll-scheduled",
            headers={"x-sharefile-webhook-token": "wrong-token"},
        )
        self.assertEqual(response.status_code, 401)

    def test_accepts_valid_token_and_triggers_poll(self):
        with patch(
            "app.api.sharefile.ShareFileService.poll_folder",
            new=AsyncMock(return_value={"status": "queued"}),
        ) as poll_mock:
            response = self.client.post(
                "/api/sharefile/poll-scheduled",
                headers={"x-sharefile-webhook-token": "test-scheduler-token"},
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.json(),
            {
                "accepted": True,
                "queued": True,
                "message": "ShareFile scan accepted for background processing.",
            },
        )
        poll_mock.assert_awaited()

    def test_handler_queues_poll_without_waiting_for_scan(self):
        from app.api.sharefile import poll_sharefile_folder_scheduled

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/sharefile/poll-scheduled",
                "headers": [(b"x-sharefile-webhook-token", b"test-scheduler-token")],
                "query_string": b"",
            }
        )
        background_tasks = BackgroundTasks()
        with patch(
            "app.api.sharefile.ShareFileService.poll_folder",
            new=AsyncMock(return_value={"status": "queued"}),
        ) as poll_mock:
            response = asyncio.run(poll_sharefile_folder_scheduled(request, background_tasks))

        self.assertTrue(response["accepted"])
        self.assertTrue(response["queued"])
        self.assertEqual(len(background_tasks.tasks), 1)
        poll_mock.assert_not_awaited()

    def test_regular_poll_still_requires_login(self):
        response = self.client.post("/api/sharefile/poll")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
