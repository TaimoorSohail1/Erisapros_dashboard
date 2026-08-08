import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import get_settings


class ScheduledPollEndpointTests(unittest.TestCase):
    """The external scheduler (EventBridge) must be able to trigger a ShareFile
    poll with the shared webhook token, without a Cognito login - and nobody
    else may trigger it without that token."""

    def setUp(self):
        os.environ["SHAREFILE_WEBHOOK_TOKEN"] = "test-scheduler-token"
        os.environ["SHAREFILE_WORK_QUEUE_URL"] = "https://sqs.example.test/queue"
        os.environ["AUTH_ENABLED"] = "true"
        get_settings.cache_clear()
        from app.main import app

        self.client = TestClient(app)

    def tearDown(self):
        os.environ.pop("SHAREFILE_WEBHOOK_TOKEN", None)
        os.environ.pop("SHAREFILE_WORK_QUEUE_URL", None)
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
            "app.api.sharefile.enqueue_sharefile_work",
            return_value=True,
        ) as start_mock:
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
        start_mock.assert_awaited_once_with("poll")

    def test_returns_already_running_when_scan_process_is_active(self):
        with patch(
            "app.api.sharefile.enqueue_sharefile_work",
            return_value=False,
        ):
            response = self.client.post(
                "/api/sharefile/poll-scheduled",
                headers={"x-sharefile-webhook-token": "test-scheduler-token"},
            )
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["accepted"])
        self.assertFalse(response.json()["queued"])

    def test_regular_poll_still_requires_login(self):
        response = self.client.post("/api/sharefile/poll")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
