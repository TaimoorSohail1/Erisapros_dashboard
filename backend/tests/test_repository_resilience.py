import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bson import ObjectId

from pymongo.errors import NetworkTimeout
from pymongo.read_preferences import ReadPreference

from app import repositories
from app.models import FTWilliamsReview, ScheduleABrokerRow
from app.repositories import MongoRepository, dashboard_identity_values, retry_repository_read


class MongoRepositoryResilienceTests(unittest.TestCase):
    def test_sharefile_identity_projection_keeps_version_hash_and_size_for_deduplication(self):
        captured = {}

        class Cursor:
            async def to_list(self, length):
                captured["length"] = length
                return []

        def find(query, projection):
            captured["query"] = query
            captured["projection"] = projection
            return Cursor()

        repository = MongoRepository.__new__(MongoRepository)
        repository.db = SimpleNamespace(
            sharefile_file_index=SimpleNamespace(find=find)
        )

        asyncio.run(repository.list_sharefile_files_by_item_ids({"item-1"}))

        self.assertEqual(captured["query"], {"item_id": {"$in": ["item-1"]}})
        for field in ("file_size", "modified_at", "version", "hash"):
            self.assertEqual(
                captured["projection"].get(field),
                1,
                f"{field} is required to avoid re-extracting one ShareFile version",
            )

    def test_legacy_empty_client_errors_load_as_missing_errors(self):
        review = repositories.from_mongo(
            {
                "_id": ObjectId(),
                "filing_id": str(ObjectId()),
                "active_failure": True,
                "client_error": {},
                "active_failure_client_error": {},
            },
            FTWilliamsReview,
        )

        self.assertIsNone(review.client_error)
        self.assertIsNone(review.active_failure_client_error)

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
        client_path = "Community Legal Aid SoCal (Test) > Community Legal Aid SoCal (CLA SoCal)"
        for index, name in enumerate(["COMMUNITY LEGAL AID SOCAL", "Community Legal Aid SoCal (CLA SoCal)"]):
            documents[index].update({
                "intake_source": "SHAREFILE",
                "dashboard_client_name": name,
                "package_documents": [{
                    "client_name": "Community Legal Aid SoCal (CLA SoCal)",
                    "sharefile_path": ("Folders > ERISA Pros > " if index else "") + client_path + f" > 5500 Filing > {2024 + index} Filing > Schedule A's > policy.pdf",
                }],
            })

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
        self.assertEqual(filings[0].dashboard_client_name, "Community Legal Aid SoCal (CLA SoCal)")
        self.assertEqual(filings[0].dashboard_client_group_key, filings[1].dashboard_client_group_key)
        self.assertTrue(filings[0].dashboard_client_group_key)
        self.assertEqual(filings[0].package_documents, [])
        self.assertEqual(captured["read_preference"], ReadPreference.PRIMARY)
        self.assertEqual(captured["batch_size"], 1_000)
        self.assertIsNone(captured["to_list_length"])
        self.assertNotIn("proposed_xml", captured["projection"])
        self.assertNotIn("package_documents", captured["projection"])
        self.assertIn("dashboard_client_name", captured["projection"])
        self.assertIn("dashboard_ein", captured["projection"])
        self.assertIn("dashboard_plan_number", captured["projection"])
        self.assertIn("dashboard_plan_name", captured["projection"])

    def test_sharefile_company_identity_does_not_merge_different_client_paths_or_workspaces(self):
        def source(root="Test", workspace=None, year="2025"):
            return {"intake_source": "SHAREFILE", "workspace_id": workspace, "package_documents": [{
                "client_name": "Example Client", "sharefile_path": f"{root} > Example Client > 5500 Filing > {year} Filing > Schedule A's > carrier > policy.pdf",
            }]}
        base = repositories.dashboard_sharefile_company_identity(source())
        self.assertEqual(base, repositories.dashboard_sharefile_company_identity(source(year="2024")))
        self.assertNotEqual(base["dashboard_client_group_key"], repositories.dashboard_sharefile_company_identity(source(root="Production"))["dashboard_client_group_key"])
        self.assertNotEqual(base["dashboard_client_group_key"], repositories.dashboard_sharefile_company_identity(source(workspace="another-workspace"))["dashboard_client_group_key"])
        manual = source()
        manual["intake_source"] = "MANUAL"
        self.assertEqual(repositories.dashboard_sharefile_company_identity(manual), {})
        conflict = source()
        conflict["package_documents"].append({"client_name": "Different Client"})
        self.assertEqual(repositories.dashboard_sharefile_company_identity(conflict), {})
        # Source-derived sponsor XML is untouched by this presentation-only fix.
        self.assertEqual(base["dashboard_client_name"], "Example Client")

    def test_dashboard_groups_relative_and_discovery_root_paths_for_same_client(self):
        from app.models import Filing
        repository = repositories.MemoryRepository()
        for index, prefix in enumerate(["", "Folders > ERISA Pros > "]):
            repository.filings[str(index)] = Filing(
                id=str(index), file_name=f"policy-{index}.pdf", content_type="application/pdf",
                file_size=1, s3_key=f"policy-{index}.pdf", intake_source="SHAREFILE",
                package_documents=[{"client_name": "The Barry Robinson Center TEST",
                    "sharefile_path": prefix + "The Barry Robinson Center TEST > 5500 Filing > 2025 Filing > Schedule A's > policy.pdf"}],
            )
        rows = asyncio.run(repository.list_dashboard_filings())
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].dashboard_client_group_key)
        self.assertEqual(rows[0].dashboard_client_group_key, rows[1].dashboard_client_group_key)
        self.assertEqual(repository.filings["1"].package_documents[0]["sharefile_path"],
            "Folders > ERISA Pros > The Barry Robinson Center TEST > 5500 Filing > 2025 Filing > Schedule A's > policy.pdf")

    def test_discovery_root_normalization_preserves_nested_client_boundaries(self):
        def identity(path, folder_id=None):
            return repositories.dashboard_sharefile_company_identity({
                "intake_source": "SHAREFILE", "package_documents": [{
                    "client_name": "Example Client", "sharefile_path": path,
                    "sharefile_client_folder_id": folder_id,
                }],
            })["dashboard_client_group_key"]
        base = identity("Test > Example Client > 5500 Filing > 2025 Filing")
        self.assertEqual(base, identity(" folders > ERISA PROS > Test > Example Client > 5500 Filing > 2024 Filing"))
        self.assertNotEqual(base, identity("Folders > ERISA Pros > Production > Example Client > 5500 Filing"))
        self.assertNotEqual(base, identity("Folders > Another Account > Test > Example Client > 5500 Filing"))
        self.assertEqual(identity("Example Client", "client-123"), identity("Folders > ERISA Pros > Example Client", "client-123"))
        self.assertNotEqual(identity("Example Client", "client-123"), identity("Example Client", "client-456"))

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
                "ftw_workspace_plan_mappings",
                "ftw_client_workspaces",
                "field_rule_versions",
                "ftw_local_agent_pairing_codes",
                "ftw_local_agent_devices",
                "ftw_local_agent_jobs",
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
        self.assertIn("ftw_workspace_plan_mapping_identity_idx", indexes["ftw_workspace_plan_mappings"])
        self.assertIn("ftw_client_workspace_slug_idx", indexes["ftw_client_workspaces"])
        self.assertIn("field_rule_key_version_idx", indexes["field_rule_versions"])
        self.assertIn("ftw_local_agent_pairing_code_idx", indexes["ftw_local_agent_pairing_codes"])
        self.assertIn("ftw_local_agent_pairing_expiry_idx", indexes["ftw_local_agent_pairing_codes"])
        self.assertIn("ftw_local_agent_device_token_idx", indexes["ftw_local_agent_devices"])
        self.assertIn("ftw_local_agent_job_idempotency_idx", indexes["ftw_local_agent_jobs"])
        self.assertIn("ftw_local_agent_job_claim_idx", indexes["ftw_local_agent_jobs"])
        self.assertIn("ftw_local_agent_workspace_job_claim_idx", indexes["ftw_local_agent_jobs"])

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
