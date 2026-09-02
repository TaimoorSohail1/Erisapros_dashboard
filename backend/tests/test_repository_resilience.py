import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bson import ObjectId

from pymongo.errors import NetworkTimeout
from pymongo.read_preferences import ReadPreference

from app import repositories
from app.models import ScheduleABrokerRow
from app.repositories import MongoRepository, dashboard_identity_values, retry_repository_read


class MongoRepositoryResilienceTests(unittest.TestCase):
    def test_update_filing_serializes_nested_pydantic_models_for_mongo(self):
        async def scenario():
            filing_id = str(ObjectId())
            collection = SimpleNamespace(find_one_and_update=AsyncMock(return_value=None))
            repository = MongoRepository.__new__(MongoRepository)
            repository.db = SimpleNamespace(filings=collection)

            await repository.update_filing(
                filing_id,
                {"schedule_a_broker_rows": [ScheduleABrokerRow(name="EOI SERVICE COMPANY INC", organization_code="3")]},
            )
            return collection.find_one_and_update.await_args.args[1]["$set"]

        values = asyncio.run(scenario())

        self.assertIsInstance(values["schedule_a_broker_rows"][0], dict)
        self.assertEqual(values["schedule_a_broker_rows"][0]["organization_code"], "3")

    def test_dashboard_identity_is_denormalized_from_xml_and_package_metadata(self):
        values = dashboard_identity_values(
            {
                "proposed_xml": (
                    "<Root><SponsorName>Example &amp; Co.</SponsorName>"
                    "<SponsDfeEIN>12-3456789</SponsDfeEIN>"
                    "<SponsDfePlanNum>501</SponsDfePlanNum>"
                    "<PlanName>Example Benefit Plan</PlanName></Root>"
                ),
                "package_documents": [{"client_name": "Folder fallback"}],
            }
        )

        self.assertEqual(values["dashboard_client_name"], "Example & Co.")
        self.assertEqual(values["dashboard_ein"], "12-3456789")
        self.assertEqual(values["dashboard_plan_number"], "501")
        self.assertEqual(values["dashboard_plan_name"], "Example Benefit Plan")

    def test_dashboard_query_returns_every_active_filing_instead_of_latest_hundred(self):
        captured = {}

        class Cursor:
            def __init__(self, documents):
                self.documents = documents
                self.index = 0

            def sort(self, *_args):
                return self

            def batch_size(self, value):
                captured["batch_size"] = value
                return self

            async def to_list(self, length):
                captured["to_list_length"] = length
                return self.documents

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.index >= len(self.documents):
                    raise StopAsyncIteration
                document = self.documents[self.index]
                self.index += 1
                return document

        documents = [
            {
                "_id": ObjectId(),
                "file_name": f"filing-{index}.pdf",
                "content_type": "application/pdf",
                "file_size": index + 1,
                "s3_key": f"filings/{index}.pdf",
            }
            for index in range(125)
        ]

        def find(query, projection):
            captured["query"] = query
            captured["projection"] = projection
            return Cursor(documents)

        def with_options(**kwargs):
            captured["read_preference"] = kwargs["read_preference"]
            return SimpleNamespace(find=find)

        repository = MongoRepository.__new__(MongoRepository)
        repository.db = SimpleNamespace(
            filings=SimpleNamespace(with_options=with_options)
        )

        filings = asyncio.run(repository.list_dashboard_filings())

        self.assertEqual(len(filings), 125)
        self.assertEqual(captured["read_preference"], ReadPreference.PRIMARY)
        self.assertEqual(captured["batch_size"], 1_000)
        self.assertIsNone(captured["to_list_length"])
        self.assertNotIn("proposed_xml", captured["projection"])
        self.assertNotIn("package_documents", captured["projection"])
        self.assertIn("dashboard_client_name", captured["projection"])
        self.assertIn("dashboard_ein", captured["projection"])
        self.assertIn("dashboard_plan_number", captured["projection"])
        self.assertIn("dashboard_plan_name", captured["projection"])

    def test_performance_indexes_cover_review_and_history_queries(self):
        async def scenario():
            repository = MongoRepository.__new__(MongoRepository)
            collection_names = [
                "ftwilliams_schemas",
                "sharefile_file_index",
                "sharefile_suppressions",
                "filings",
                "extracted_fields",
                "review_events",
                "audit_logs",
                "extraction_jobs",
                "ftwilliams_reviews",
                "ftwilliams_plan_mappings",
                "field_rule_versions",
            ]
            collections = {
                name: SimpleNamespace(create_index=AsyncMock())
                for name in collection_names
            }
            repository.db = SimpleNamespace(**collections)
            await repository.ensure_indexes()
            return {
                name: {call.kwargs.get("name") for call in collection.create_index.await_args_list}
                for name, collection in collections.items()
            }

        indexes = asyncio.run(scenario())

        self.assertIn("filing_status_created_idx", indexes["filings"])
        self.assertIn("filing_created_idx", indexes["filings"])
        self.assertIn("field_filing_label_idx", indexes["extracted_fields"])
        self.assertIn("review_event_filing_created_idx", indexes["review_events"])
        self.assertIn("audit_ftw_event_created_idx", indexes["audit_logs"])
        self.assertIn("audit_filing_created_idx", indexes["audit_logs"])
        self.assertIn("audit_failure_queue_idx", indexes["audit_logs"])
        self.assertIn("job_filing_created_idx", indexes["extraction_jobs"])
        self.assertIn("ftw_review_filing_idx", indexes["ftwilliams_reviews"])
        self.assertIn("ftw_review_status_updated_idx", indexes["ftwilliams_reviews"])
        self.assertIn("ftw_review_failure_date_filing_idx", indexes["ftwilliams_reviews"])
        self.assertIn("ftw_review_failure_type_date_idx", indexes["ftwilliams_reviews"])
        self.assertIn("ftwilliams_schema_cache_key_idx", indexes["ftwilliams_schemas"])
        self.assertIn("ftw_plan_mapping_identity_idx", indexes["ftwilliams_plan_mappings"])
        self.assertIn("field_rule_key_version_idx", indexes["field_rule_versions"])

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
