import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.repositories as repositories
from app.models import DocumentType, ExtractionJob, Filing, FilingStatus, ShareFileOAuthToken
from app.services.extractor import (
    SCHEDULE_A_EXPERIENCE_RATED_FIELDS,
    parse_schedule_a_text,
)
from app.services.sharefile import ShareFileService


def run_async(coro):
    return asyncio.run(coro)


def sharefile_file(
    item_id: str,
    name: str,
    path_parts: list[str],
    document_type: DocumentType | None,
    modified_at: str = "2026-01-01T00:00:00Z",
):
    return {
        "id": item_id,
        "name": name,
        "path": " > ".join(path_parts),
        "path_parts": path_parts,
        "root_folder_id": "root",
        "parent_id": f"parent-{item_id}",
        "folder_ids_by_depth": {"0": "client", "1": "5500", "2": "year", "3": "schedule"},
        "document_type": document_type,
        "size": 1024,
        "modified_at": modified_at,
        "created_at": "2026-01-01T00:00:00Z",
        "raw": {},
    }


class DummyBackgroundTasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, *args, **kwargs):
        self.tasks.append((args, kwargs))


class ShareFileRegressionTests(unittest.TestCase):
    def setUp(self):
        repositories._repository = repositories.MemoryRepository()
        self.service = ShareFileService()

    def tearDown(self):
        repositories._repository = None

    def stub_package_creation(self):
        async def fake_create_filing_package(client, token, package_key, package_files):
            repo = repositories.get_repository()
            package_documents = [
                self.service._metadata_package_document(file_item, package_key)
                for file_item in sorted(package_files, key=self.service._document_sort_key)
            ]
            primary_file = next(
                (item for item in package_files if item.get("document_type") == DocumentType.SCHEDULE_A),
                package_files[0],
            )
            filing = Filing(
                file_name=self.service._filing_name_for_package(package_key, package_files),
                content_type="application/vnd.erisapros.filing-package",
                file_size=sum(int(item.get("size") or 0) for item in package_files),
                document_type=primary_file["document_type"],
                package_document_count=len(package_documents),
                status=FilingStatus.QUEUED,
                s3_key=f"sharefile-package/{package_key}",
                package_documents=package_documents,
                intake_source="SHAREFILE",
                sharefile_item_id=primary_file["id"],
                sharefile_parent_id=primary_file["parent_id"],
            )
            filing = await repo.create_filing(filing)
            job = await repo.create_extraction_job(ExtractionJob(filing_id=filing.id))
            processing_documents = [
                {
                    "file_bytes": b"test",
                    "file_name": item["name"],
                    "file_size": int(item.get("size") or 0),
                    "content_type": "application/pdf",
                    "document_type": item["document_type"],
                    "sharefile_item_id": item["id"],
                    "sharefile_path": item["path"],
                }
                for item in package_files
            ]
            return filing, job, processing_documents

        self.service._create_filing_package = fake_create_filing_package

    def test_explicit_schedule_a_filename_wins_over_worksheet_context(self):
        document_type = self.service._classify_sharefile_document(
            "Schedule A After Worksheet.pdf",
            [
                "Client (Test)",
                "5500 Filing",
                "2024 Filing Worksheet First",
                "Schedule A After Worksheet.pdf",
            ],
        )

        self.assertEqual(document_type, DocumentType.SCHEDULE_A)

    def test_content_sniff_skips_unreadable_pdf_instead_of_aborting_scan(self):
        async def download_unreadable_pdf(client, token, item_id):
            return b"\r\n\r\n\rnot-a-real-pdf"

        self.service._download_item = download_unreadable_pdf

        with patch("app.services.sharefile.extract_pdf_text_pages", side_effect=RuntimeError("unreadable PDF pages")):
            document_type = run_async(
                self.service._classify_sharefile_document_by_content(
                    client=None,
                    token=None,
                    item_id="bad-pdf",
                    file_name="Bad Upload.pdf",
                )
            )

        self.assertIsNone(document_type)

    def test_sharefile_request_refreshes_early_revoked_access_token_after_401(self):
        token = ShareFileOAuthToken(
            subdomain="example",
            access_token="revoked-access-token",
            refresh_token="valid-refresh-token",
            expires_at=datetime.utcnow() + timedelta(hours=6),
        )
        client = AsyncMock()
        request = httpx.Request("GET", "https://example.sf-api.com/sf/v3/Items(file-1)")
        client.request.side_effect = [
            httpx.Response(401, request=request),
            httpx.Response(200, request=request, json={"Id": "file-1", "Name": "Schedule A.pdf"}),
        ]
        refresh_request = httpx.Request("POST", "https://example.sharefile.com/oauth/token")
        client.post.return_value = httpx.Response(
            200,
            request=refresh_request,
            json={
                "access_token": "fresh-access-token",
                "refresh_token": "rotated-refresh-token",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

        item = run_async(self.service._get_item(client, token, "file-1"))

        self.assertEqual(item["Id"], "file-1")
        self.assertEqual(token.access_token, "fresh-access-token")
        self.assertEqual(token.refresh_token, "rotated-refresh-token")
        self.assertEqual(client.request.await_count, 2)
        first_headers = client.request.await_args_list[0].kwargs["headers"]
        second_headers = client.request.await_args_list[1].kwargs["headers"]
        self.assertEqual(first_headers["Authorization"], "Bearer revoked-access-token")
        self.assertEqual(second_headers["Authorization"], "Bearer fresh-access-token")

    def test_schedule_a_child_folder_has_same_root_but_separate_package_from_worksheet(self):
        worksheet = sharefile_file(
            "worksheet",
            "5500 Plan Worksheet.docx",
            ["Housing Counseling Services", "5500", "2025 Filing SPY", "5500 Plan Worksheet.docx"],
            DocumentType.PLAN_WORKSHEET,
        )
        schedule_a = sharefile_file(
            "schedule-a",
            "Schedule A.pdf",
            ["Housing Counseling Services", "5500", "2025 Filing SPY", "Schedule A's", "Schedule A.pdf"],
            DocumentType.SCHEDULE_A,
        )

        worksheet_root = self.service._package_root_key(worksheet)
        schedule_root = self.service._package_root_key(schedule_a)
        worksheet_key = self.service._package_key(worksheet)
        schedule_key = self.service._package_key(schedule_a)

        self.assertEqual(worksheet_root, "Housing Counseling Services > 5500 > 2025 Filing SPY")
        self.assertEqual(schedule_root, worksheet_root)
        self.assertEqual(worksheet_key, f"{worksheet_root} > Waiting for Schedule A")
        self.assertEqual(schedule_key, f"{schedule_root} > Schedule A::schedule-a")
        self.assertEqual(self.service._client_name_for(schedule_a), "Housing Counseling Services")
        self.assertEqual(self.service._filing_year_for(schedule_a), "2025")

    def test_nested_package_uses_deepest_filing_year_and_client_folder(self):
        schedule_a = sharefile_file(
            "nested-schedule-a",
            "QA Schedule A.pdf",
            [
                "HighlandTech AI Test Folder",
                "5500 Filing",
                "2025 Filing",
                "Schedule A's",
                "ERISAPros E2E Harold 20260616-095048",
                "2024 Filing SPY",
                "Schedule A's",
                "QA Schedule A.pdf",
            ],
            DocumentType.SCHEDULE_A,
        )

        self.assertEqual(self.service._filing_year_for(schedule_a), "2024")
        self.assertEqual(
            self.service._client_name_for(schedule_a),
            "ERISAPros E2E Harold 20260616-095048",
        )

    def test_relevant_webhook_discovery_only_walks_filing_folder_levels(self):
        def folder(item_id: str, name: str) -> dict:
            return {"Id": item_id, "Name": name, "ItemType": "Folder"}

        tree = {
            "client-root": [
                folder("unrelated", "Employee Docs"),
                folder("filing-root", "5500 Filing"),
            ],
            "filing-root": [
                folder("year-2024", "2024 Filing"),
                folder("not-year", "Templates"),
            ],
            "year-2024": [
                folder("schedule-a", "Schedule A"),
                folder("final-product", "Final Product"),
            ],
            "schedule-a": [
                folder("cigna", "Cigna"),
            ],
            "cigna": [
                folder("dental", "Dental"),
            ],
        }
        calls = []

        async def fake_list_folder(client, token, folder_id):
            calls.append(folder_id)
            return tree.get(folder_id, [])

        self.service._list_folder = fake_list_folder

        roots = run_async(
            self.service._discover_relevant_webhook_roots(
                client=None,
                token=None,
                scan_roots=[
                    self.service._scan_root("client-root", "TEST", ["Client"]),
                ],
            )
        )

        self.assertEqual(
            {root["id"] for root in roots},
            {"client-root", "filing-root", "year-2024", "schedule-a", "cigna", "dental"},
        )
        self.assertEqual(calls, ["client-root", "filing-root", "year-2024", "schedule-a", "cigna", "dental"])

    def test_baseline_scan_indexes_metadata_without_creating_filings(self):
        files = [
            sharefile_file(
                "worksheet",
                "5500 Plan Worksheet.docx",
                ["Client", "5500", "2025 Filing", "5500 Plan Worksheet.docx"],
                DocumentType.PLAN_WORKSHEET,
            ),
            sharefile_file(
                "schedule-a",
                "Schedule A.pdf",
                ["Client", "5500", "2025 Filing", "Schedule A's", "Schedule A.pdf"],
                DocumentType.SCHEDULE_A,
            ),
            sharefile_file(
                "ack",
                "Acknowledgement.pdf",
                ["Client", "5500", "2025 Filing", "Acknowledgement.pdf"],
                None,
            ),
        ]

        result = run_async(
            self.service._process_changed_sharefile_files(
                client=None,
                token=None,
                scanned_files=files,
                background_tasks=None,
                first_scan=True,
                process_new_files=True,
                source="TEST_BASELINE",
            )
        )
        repo = repositories.get_repository()
        indexed = run_async(repo.list_sharefile_files())
        filings = run_async(repo.list_filings())

        self.assertTrue(result["baseline"])
        self.assertEqual(result["synced"], 0)
        self.assertEqual(result["skipped"], 2)
        self.assertEqual({item["status"] for item in indexed}, {"INDEXED", "IGNORED"})
        self.assertEqual(filings, [])

    def test_changed_schedule_a_selects_latest_unchanged_worksheet_sibling(self):
        changed_schedule = sharefile_file(
            "schedule-a-new",
            "Schedule A.pdf",
            ["Client", "5500", "2025 Filing", "Schedule A's", "Schedule A.pdf"],
            DocumentType.SCHEDULE_A,
            "2026-02-01T00:00:00Z",
        )
        changed_schedule["change_type"] = "NEW"
        old_worksheet = sharefile_file(
            "worksheet-old",
            "5500 Plan Worksheet old.docx",
            ["Client", "5500", "2025 Filing", "5500 Plan Worksheet old.docx"],
            DocumentType.PLAN_WORKSHEET,
            "2026-01-01T00:00:00Z",
        )
        old_worksheet["change_type"] = "UNCHANGED"
        latest_worksheet = sharefile_file(
            "worksheet-latest",
            "5500 Plan Worksheet.docx",
            ["Client", "5500", "2025 Filing", "5500 Plan Worksheet.docx"],
            DocumentType.PLAN_WORKSHEET,
            "2026-01-15T00:00:00Z",
        )
        latest_worksheet["change_type"] = "UNCHANGED"

        selected = self.service._select_package_files_for_processing(
            [changed_schedule, old_worksheet, latest_worksheet],
            prefer_changed=True,
        )

        self.assertEqual(
            {item["id"] for item in selected},
            {"schedule-a-new", "worksheet-latest"},
        )

    def test_changed_schedule_a_without_worksheet_creates_waiting_row(self):
        changed_schedule = sharefile_file(
            "schedule-a-new",
            "Housing Life Schedule A.pdf",
            ["Client", "5500", "2025 Filing", "Schedule A's", "Housing Life Schedule A.pdf"],
            DocumentType.SCHEDULE_A,
            "2026-02-01T00:00:00Z",
        )

        async def no_root_siblings(client, token, package_root):
            return []

        self.service._scan_package_root = no_root_siblings

        result = run_async(
            self.service._process_changed_sharefile_files(
                client=None,
                token=None,
                scanned_files=[changed_schedule],
                background_tasks=None,
                first_scan=False,
                process_new_files=True,
                source="TEST_SCHEDULE_ONLY",
            )
        )
        repo = repositories.get_repository()
        filings = run_async(repo.list_filings())

        self.assertEqual(result["synced"], 0)
        self.assertTrue(
            any(item.get("reason") == FilingStatus.WAITING_FOR_WORKSHEET.value for item in result["skipped_files"])
        )
        self.assertEqual(len(filings), 1)
        self.assertEqual(filings[0].status, FilingStatus.WAITING_FOR_WORKSHEET)
        self.assertEqual(filings[0].package_document_count, 1)
        self.assertIn("Housing Life Schedule A.pdf", filings[0].file_name)

    def test_schedule_a_then_worksheet_leaves_one_active_complete_package(self):
        schedule_a = sharefile_file(
            "schedule-a-life",
            "Housing Life Schedule A.pdf",
            ["Client", "5500", "2025 Filing", "Schedule A's", "Housing Life Schedule A.pdf"],
            DocumentType.SCHEDULE_A,
            "2026-02-01T00:00:00Z",
        )
        worksheet = sharefile_file(
            "worksheet",
            "5500 Plan Worksheet.docx",
            ["Client", "5500", "2025 Filing", "5500 Plan Worksheet.docx"],
            DocumentType.PLAN_WORKSHEET,
            "2026-02-02T00:00:00Z",
        )

        async def no_root_siblings(client, token, package_root):
            return []

        self.service._scan_package_root = no_root_siblings
        run_async(
            self.service._process_changed_sharefile_files(
                client=None,
                token=None,
                scanned_files=[schedule_a],
                background_tasks=DummyBackgroundTasks(),
                first_scan=False,
                process_new_files=True,
                source="TEST_SCHEDULE_FIRST",
            )
        )

        async def root_has_schedule_and_worksheet(client, token, package_root):
            return [schedule_a, worksheet]

        self.stub_package_creation()
        self.service._scan_package_root = root_has_schedule_and_worksheet
        result = run_async(
            self.service._process_changed_sharefile_files(
                client=None,
                token=None,
                scanned_files=[worksheet],
                background_tasks=DummyBackgroundTasks(),
                first_scan=False,
                process_new_files=True,
                source="TEST_WORKSHEET_SECOND",
            )
        )
        repo = repositories.get_repository()
        filings = run_async(repo.list_filings())
        active = [filing for filing in filings if filing.status not in {FilingStatus.SUPERSEDED, FilingStatus.DELETED}]

        self.assertEqual(result["synced"], 1)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].status, FilingStatus.QUEUED)
        self.assertEqual(active[0].package_document_count, 2)
        self.assertIn("Housing Life Schedule A.pdf", active[0].file_name)
        self.assertFalse(any(filing.status == FilingStatus.WAITING_FOR_SCHEDULE_A for filing in active))

    def test_worksheet_then_schedule_supersedes_temporary_worksheet_row(self):
        worksheet = sharefile_file(
            "worksheet",
            "5500 Plan Worksheet.docx",
            ["Client", "5500", "2025 Filing", "5500 Plan Worksheet.docx"],
            DocumentType.PLAN_WORKSHEET,
            "2026-02-01T00:00:00Z",
        )
        schedule_a = sharefile_file(
            "schedule-a-life",
            "Housing Life Schedule A.pdf",
            ["Client", "5500", "2025 Filing", "Schedule A's", "Housing Life Schedule A.pdf"],
            DocumentType.SCHEDULE_A,
            "2026-02-02T00:00:00Z",
        )

        async def no_root_siblings(client, token, package_root):
            return []

        self.service._scan_package_root = no_root_siblings
        run_async(
            self.service._process_changed_sharefile_files(
                client=None,
                token=None,
                scanned_files=[worksheet],
                background_tasks=DummyBackgroundTasks(),
                first_scan=False,
                process_new_files=True,
                source="TEST_WORKSHEET_FIRST",
            )
        )

        repo = repositories.get_repository()
        initial_active = [
            filing
            for filing in run_async(repo.list_filings())
            if filing.status not in {FilingStatus.SUPERSEDED, FilingStatus.DELETED}
        ]
        self.assertEqual(len(initial_active), 1)
        self.assertEqual(initial_active[0].status, FilingStatus.WAITING_FOR_SCHEDULE_A)

        async def root_has_schedule_and_worksheet(client, token, package_root):
            return [schedule_a, worksheet]

        self.stub_package_creation()
        self.service._scan_package_root = root_has_schedule_and_worksheet
        result = run_async(
            self.service._process_changed_sharefile_files(
                client=None,
                token=None,
                scanned_files=[schedule_a],
                background_tasks=DummyBackgroundTasks(),
                first_scan=False,
                process_new_files=True,
                source="TEST_SCHEDULE_SECOND",
            )
        )
        filings = run_async(repo.list_filings())
        active = [filing for filing in filings if filing.status not in {FilingStatus.SUPERSEDED, FilingStatus.DELETED}]

        self.assertEqual(result["synced"], 1)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].status, FilingStatus.QUEUED)
        self.assertEqual(active[0].package_document_count, 2)
        self.assertIn("Housing Life Schedule A.pdf", active[0].file_name)
        self.assertTrue(any(filing.status == FilingStatus.SUPERSEDED for filing in filings))

    def test_same_scan_schedule_and_worksheet_creates_one_complete_package(self):
        schedule_a = sharefile_file(
            "schedule-a-life",
            "Housing Life Schedule A.pdf",
            ["Client", "5500", "2025 Filing", "Schedule A's", "Housing Life Schedule A.pdf"],
            DocumentType.SCHEDULE_A,
            "2026-02-01T00:00:00Z",
        )
        worksheet = sharefile_file(
            "worksheet",
            "5500 Plan Worksheet.docx",
            ["Client", "5500", "2025 Filing", "5500 Plan Worksheet.docx"],
            DocumentType.PLAN_WORKSHEET,
            "2026-02-01T00:01:00Z",
        )

        async def root_has_schedule_and_worksheet(client, token, package_root):
            return [
                dict(schedule_a, change_type=None),
                dict(worksheet, change_type=None),
            ]

        self.stub_package_creation()
        self.service._scan_package_root = root_has_schedule_and_worksheet
        result = run_async(
            self.service._process_changed_sharefile_files(
                client=None,
                token=None,
                scanned_files=[schedule_a, worksheet],
                background_tasks=DummyBackgroundTasks(),
                first_scan=False,
                process_new_files=True,
                source="TEST_SAME_SCAN",
            )
        )
        repo = repositories.get_repository()
        filings = run_async(repo.list_filings())
        active = [filing for filing in filings if filing.status not in {FilingStatus.SUPERSEDED, FilingStatus.DELETED}]

        self.assertEqual(result["synced"], 1)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].status, FilingStatus.QUEUED)
        self.assertEqual(active[0].package_document_count, 2)
        self.assertIn("Housing Life Schedule A.pdf", active[0].file_name)
        self.assertFalse(any(filing.status == FilingStatus.WAITING_FOR_SCHEDULE_A for filing in active))

    def test_deleted_sharefile_items_stay_suppressed_but_new_items_can_import(self):
        deleted_schedule = sharefile_file(
            "deleted-schedule",
            "Deleted Schedule A.pdf",
            ["Client", "5500", "2025 Filing", "Schedule A's", "Deleted Schedule A.pdf"],
            DocumentType.SCHEDULE_A,
        )
        deleted_worksheet = sharefile_file(
            "deleted-worksheet",
            "Deleted Plan Worksheet.docx",
            ["Client", "5500", "2025 Filing", "Deleted Plan Worksheet.docx"],
            DocumentType.PLAN_WORKSHEET,
        )
        new_schedule = sharefile_file(
            "new-schedule",
            "New Schedule A.pdf",
            ["Other Client", "5500", "2025 Filing", "Schedule A's", "New Schedule A.pdf"],
            DocumentType.SCHEDULE_A,
        )
        new_worksheet = sharefile_file(
            "new-worksheet",
            "New Plan Worksheet.docx",
            ["Other Client", "5500", "2025 Filing", "New Plan Worksheet.docx"],
            DocumentType.PLAN_WORKSHEET,
        )

        async def scenario():
            repo = repositories.get_repository()
            await repo.upsert_sharefile_suppression("deleted-schedule", {"reason": "DASHBOARD_DELETE"})
            await repo.upsert_sharefile_suppression("deleted-worksheet", {"reason": "DASHBOARD_DELETE"})
            self.stub_package_creation()
            return await self.service._queue_sharefile_packages(
                client=None,
                token=ShareFileOAuthToken(subdomain="example", access_token="token"),
                packages={
                    "deleted-package": [deleted_schedule, deleted_worksheet],
                    "new-package": [new_schedule, new_worksheet],
                },
                background_tasks=DummyBackgroundTasks(),
                source="TEST_SUPPRESSION",
                prefer_changed=False,
            )

        synced, skipped, failed = run_async(scenario())
        active = [
            filing
            for filing in run_async(repositories.get_repository().list_filings())
            if filing.status not in {FilingStatus.SUPERSEDED, FilingStatus.DELETED}
        ]
        self.assertEqual(failed, [])
        self.assertEqual(len(synced), 1)
        self.assertEqual(active[0].sharefile_item_id, "new-schedule")
        self.assertTrue(any(item["reason"] == "DASHBOARD_DELETE_SUPPRESSED" for item in skipped))

    def test_new_schedule_can_reuse_legacy_suppressed_shared_worksheet(self):
        new_schedule = sharefile_file(
            "new-schedule",
            "New Schedule A.pdf",
            ["Client", "5500", "2025 Filing", "Schedule A's", "New Schedule A.pdf"],
            DocumentType.SCHEDULE_A,
        )
        shared_worksheet = sharefile_file(
            "shared-worksheet",
            "5500 Plan Worksheet.docx",
            ["Client", "5500", "2025 Filing", "5500 Plan Worksheet.docx"],
            DocumentType.PLAN_WORKSHEET,
        )

        async def scenario():
            repo = repositories.get_repository()
            await repo.upsert_sharefile_suppression(
                "shared-worksheet",
                {"reason": "DASHBOARD_DELETE", "filing_id": "legacy-deleted-filing"},
            )
            self.stub_package_creation()
            return await self.service._queue_sharefile_packages(
                client=None,
                token=ShareFileOAuthToken(subdomain="example", access_token="token"),
                packages={"new-package": [new_schedule, shared_worksheet]},
                background_tasks=DummyBackgroundTasks(),
                source="TEST_SHARED_WORKSHEET",
                prefer_changed=False,
            )

        synced, skipped, failed = run_async(scenario())
        active = [
            filing
            for filing in run_async(repositories.get_repository().list_filings())
            if filing.status not in {FilingStatus.SUPERSEDED, FilingStatus.DELETED}
        ]
        self.assertEqual(failed, [])
        self.assertEqual(len(synced), 1)
        self.assertFalse(any(item["reason"] == "DASHBOARD_DELETE_SUPPRESSED" for item in skipped))
        self.assertEqual(active[0].status, FilingStatus.QUEUED)
        self.assertEqual(active[0].package_document_count, 2)

    def test_cleanup_supersedes_redundant_worksheet_waiting_row(self):
        worksheet = sharefile_file(
            "worksheet",
            "5500 Plan Worksheet.docx",
            ["Client", "5500", "2025 Filing", "5500 Plan Worksheet.docx"],
            DocumentType.PLAN_WORKSHEET,
            "2026-02-01T00:00:00Z",
        )
        schedule_a = sharefile_file(
            "schedule-a-life",
            "Housing Life Schedule A.pdf",
            ["Client", "5500", "2025 Filing", "Schedule A's", "Housing Life Schedule A.pdf"],
            DocumentType.SCHEDULE_A,
            "2026-02-02T00:00:00Z",
        )

        run_async(
            self.service._upsert_waiting_filing_package(
                self.service._package_key(worksheet),
                [worksheet],
                FilingStatus.WAITING_FOR_SCHEDULE_A,
                "5500 Plan Worksheet was received. Waiting for matching Schedule A PDF.",
            )
        )
        self.stub_package_creation()
        run_async(
            self.service._create_filing_package(
                client=None,
                token=None,
                package_key=self.service._package_key(schedule_a),
                package_files=[schedule_a, worksheet],
            )
        )

        cleaned = run_async(self.service._cleanup_redundant_waiting_for_schedule_rows("TEST_CLEANUP"))
        repo = repositories.get_repository()
        filings = run_async(repo.list_filings())
        active = [filing for filing in filings if filing.status not in {FilingStatus.SUPERSEDED, FilingStatus.DELETED}]

        self.assertEqual(cleaned, 1)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].status, FilingStatus.QUEUED)
        self.assertEqual(active[0].package_document_count, 2)
        self.assertTrue(any(filing.status == FilingStatus.SUPERSEDED for filing in filings))

    def test_changed_schedule_a_expands_to_only_that_schedule_and_latest_worksheet(self):
        changed_schedule = sharefile_file(
            "schedule-a-medical",
            "Medical Schedule A.pdf",
            ["Client", "5500", "2025 Filing", "Schedule A's", "Medical Schedule A.pdf"],
            DocumentType.SCHEDULE_A,
            "2026-02-01T00:00:00Z",
        )
        changed_schedule["change_type"] = "NEW"
        other_schedule = sharefile_file(
            "schedule-a-dental",
            "Dental Schedule A.pdf",
            ["Client", "5500", "2025 Filing", "Schedule A's", "Dental Schedule A.pdf"],
            DocumentType.SCHEDULE_A,
            "2026-01-20T00:00:00Z",
        )
        other_schedule["change_type"] = "UNCHANGED"
        worksheet = sharefile_file(
            "worksheet",
            "5500 Plan Worksheet.docx",
            ["Client", "5500", "2025 Filing", "5500 Plan Worksheet.docx"],
            DocumentType.PLAN_WORKSHEET,
            "2026-01-15T00:00:00Z",
        )
        worksheet["change_type"] = "UNCHANGED"

        expanded = self.service._expand_schedule_a_packages(
            self.service._package_key(changed_schedule),
            [changed_schedule, other_schedule, worksheet],
        )

        self.assertEqual(list(expanded.keys()), [self.service._package_key(changed_schedule)])
        self.assertEqual({item["id"] for item in next(iter(expanded.values()))}, {"schedule-a-medical", "worksheet"})

    def test_changed_worksheet_expands_to_each_schedule_a_package(self):
        worksheet = sharefile_file(
            "worksheet",
            "5500 Plan Worksheet.docx",
            ["Client", "5500", "2025 Filing", "5500 Plan Worksheet.docx"],
            DocumentType.PLAN_WORKSHEET,
            "2026-02-01T00:00:00Z",
        )
        worksheet["change_type"] = "UPDATED"
        medical = sharefile_file(
            "schedule-a-medical",
            "Medical Schedule A.pdf",
            ["Client", "5500", "2025 Filing", "Schedule A's", "Medical Schedule A.pdf"],
            DocumentType.SCHEDULE_A,
            "2026-01-20T00:00:00Z",
        )
        medical["change_type"] = "UNCHANGED"
        medical["active_schedule_filing"] = True
        dental = sharefile_file(
            "schedule-a-dental",
            "Dental Schedule A.pdf",
            ["Client", "5500", "2025 Filing", "Schedule A's", "Dental Schedule A.pdf"],
            DocumentType.SCHEDULE_A,
            "2026-01-21T00:00:00Z",
        )
        dental["change_type"] = "UNCHANGED"
        dental["active_schedule_filing"] = True

        expanded = self.service._expand_schedule_a_packages(
            self.service._package_key(worksheet),
            [worksheet, medical, dental],
        )

        self.assertEqual(set(expanded.keys()), {self.service._package_key(medical), self.service._package_key(dental)})
        self.assertEqual(
            {tuple(sorted(item["id"] for item in files)) for files in expanded.values()},
            {("schedule-a-dental", "worksheet"), ("schedule-a-medical", "worksheet")},
        )

    def test_changed_worksheet_does_not_expand_to_untracked_old_schedule_a_files(self):
        worksheet = sharefile_file(
            "worksheet",
            "5500 Plan Worksheet.docx",
            ["Client", "5500", "2025 Filing", "5500 Plan Worksheet.docx"],
            DocumentType.PLAN_WORKSHEET,
            "2026-02-01T00:00:00Z",
        )
        worksheet["change_type"] = "UPDATED"
        old_schedule = sharefile_file(
            "old-schedule-a",
            "Old Schedule A.pdf",
            ["Client", "5500", "2025 Filing", "Schedule A's", "Old Schedule A.pdf"],
            DocumentType.SCHEDULE_A,
            "2026-01-20T00:00:00Z",
        )
        old_schedule["change_type"] = "UNCHANGED"
        old_schedule["indexed_status"] = "INDEXED"

        expanded = self.service._expand_schedule_a_packages(
            self.service._package_key(worksheet),
            [worksheet, old_schedule],
        )

        self.assertEqual(list(expanded.keys()), [self.service._package_key(worksheet)])
        self.assertEqual({item["id"] for item in next(iter(expanded.values()))}, {"worksheet"})

    def test_changed_worksheet_ignores_stale_filing_id_without_active_filing(self):
        worksheet = sharefile_file(
            "worksheet",
            "5500 Plan Worksheet.docx",
            ["Client", "5500", "2025 Filing", "5500 Plan Worksheet.docx"],
            DocumentType.PLAN_WORKSHEET,
            "2026-02-01T00:00:00Z",
        )
        worksheet["change_type"] = "UPDATED"
        stale_schedule = sharefile_file(
            "stale-schedule-a",
            "Stale Schedule A.pdf",
            ["Client", "5500", "2025 Filing", "Schedule A's", "Stale Schedule A.pdf"],
            DocumentType.SCHEDULE_A,
            "2026-01-20T00:00:00Z",
        )
        stale_schedule["change_type"] = "UNCHANGED"
        stale_schedule["indexed_status"] = "EXTRACTED"
        stale_schedule["indexed_filing_id"] = "superseded-filing"

        expanded = self.service._expand_schedule_a_packages(
            self.service._package_key(worksheet),
            [worksheet, stale_schedule],
        )

        self.assertEqual(list(expanded.keys()), [self.service._package_key(worksheet)])
        self.assertEqual({item["id"] for item in next(iter(expanded.values()))}, {"worksheet"})


class ScheduleAExtractionRegressionTests(unittest.TestCase):
    def field_values(self, fields, field_name):
        return [field.value for field in fields if field.field_name == field_name]

    def test_carrier_name_does_not_accept_ein_value(self):
        fields = parse_schedule_a_text(
            """
            Name of insurance carrier
            12-3456789
            (b) EIN 12-3456789
            """
        )

        self.assertEqual(self.field_values(fields, "1a. Name of Insurance Company"), [])

    def test_broker_name_commission_and_line_10_premium_are_extracted(self):
        fields = parse_schedule_a_text(
            """
            Name: ACME BROKERAGE LLC Address: 100 Main Street
            Total amount of commissions paid
            $1,200

            Nonexperience-rated contracts
            Total premiums or subscription charges paid to carrier
            125,000
            """
        )

        self.assertIn("ACME BROKERAGE LLC", self.field_values(fields, "3a. Name of Agent/Broker/Person"))
        self.assertIn("1,200", self.field_values(fields, "3b. Amount of Commissions"))
        self.assertIn(
            "125,000",
            self.field_values(fields, "10a. Total premiums or subscription charges paid to carrier"),
        )

    def test_group_name_is_not_used_as_broker_name(self):
        fields = parse_schedule_a_text(
            """
            Group Number: 011335
            Group Name: Harold Brothers Mechanical Contractors, Inc.
            EQUITABLE
            Schedule A (Form 5500) Worksheet
            """
        )

        self.assertNotIn(
            "Harold Brothers Mechanical Contractors, Inc.",
            self.field_values(fields, "3a. Name of Agent/Broker/Person"),
        )

    def test_experience_rated_na_only_marks_line_9_fields(self):
        fields = parse_schedule_a_text(
            """
            Experience-rated contracts N/A

            Nonexperience-rated contracts
            Total premiums or subscription charges paid to carrier
            125,000
            """
        )
        na_fields = {field.field_name for field in fields if field.value == "N/A"}

        self.assertTrue(na_fields)
        self.assertTrue(na_fields.issubset(set(SCHEDULE_A_EXPERIENCE_RATED_FIELDS)))
        self.assertNotIn("10a. Total premiums or subscription charges paid to carrier", na_fields)


if __name__ == "__main__":
    unittest.main()
