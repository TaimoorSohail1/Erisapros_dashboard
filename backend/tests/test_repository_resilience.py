import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from pymongo.errors import NetworkTimeout

from app.repositories import MongoRepository


class MongoRepositoryResilienceTests(unittest.TestCase):
    def test_client_has_bounded_network_and_pool_wait_timeouts(self):
        with patch("app.repositories.AsyncIOMotorClient") as client:
            MongoRepository("mongodb://example.test/erisapros")
        client.assert_called_once_with(
            "mongodb://example.test/erisapros",
            serverSelectionTimeoutMS=5_000,
            connectTimeoutMS=5_000,
            socketTimeoutMS=10_000,
            waitQueueTimeoutMS=5_000,
            timeoutMS=12_000,
        )

    def test_api_resets_stale_pool_and_returns_service_unavailable(self):
        from app.main import app

        repository = AsyncMock()
        repository.list_dashboard_filings.side_effect = NetworkTimeout("stale pool")
        with (
            patch("app.api.filings.get_repository", return_value=repository),
            patch("app.main.reset_repository") as reset,
        ):
            response = TestClient(app).get("/api/filings")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Database connection is recovering. Please retry.")
        reset.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
