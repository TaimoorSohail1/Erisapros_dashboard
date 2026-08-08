import unittest
import asyncio
from unittest.mock import AsyncMock, patch

from pymongo.errors import NetworkTimeout
from pymongo.read_preferences import ReadPreference

from app import repositories
from app.repositories import MongoRepository, retry_repository_read


class MongoRepositoryResilienceTests(unittest.TestCase):
    def test_client_has_bounded_network_and_pool_wait_timeouts(self):
        with patch("app.repositories.AsyncIOMotorClient") as client:
            MongoRepository("mongodb://example.test/erisapros")
        client.assert_called_once_with(
            "mongodb://example.test/erisapros",
            serverSelectionTimeoutMS=5_000,
            connectTimeoutMS=5_000,
            socketTimeoutMS=15_000,
            waitQueueTimeoutMS=5_000,
            timeoutMS=18_000,
            read_preference=ReadPreference.SECONDARY_PREFERRED,
        )

    def test_safe_repository_read_replaces_pool_and_retries_once(self):
        stale = AsyncMock()
        healthy = AsyncMock()
        stale.list_dashboard_filings.side_effect = NetworkTimeout("stale pool")
        healthy.list_dashboard_filings.return_value = []
        with (
            patch("app.repositories.get_repository", side_effect=[stale, healthy]),
            patch("app.repositories.reset_repository") as reset,
        ):
            result = asyncio.run(retry_repository_read(lambda repo: repo.list_dashboard_filings()))

        self.assertEqual(result, [])
        stale.list_dashboard_filings.assert_awaited_once_with()
        healthy.list_dashboard_filings.assert_awaited_once_with()
        reset.assert_called_once_with()

    def test_reset_does_not_close_pool_used_by_other_inflight_requests(self):
        with patch("app.repositories.AsyncIOMotorClient") as client:
            repository = MongoRepository("mongodb://example.test/erisapros")
        repositories._repository = repository
        try:
            repositories.reset_repository()
            self.assertIsNone(repositories._repository)
            client.return_value.close.assert_not_called()
        finally:
            repositories._repository = None


if __name__ == "__main__":
    unittest.main()
