"""A slow or broken folder must never abort the whole ShareFile scan.

Regression test for the production bug where one network timeout among ~80
client folder trees killed the entire sync, so brand-new client folders were
never discovered (while webhook-driven single-folder updates kept working).
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import BackgroundTasks

from app.models import ShareFileOAuthToken
from app.repositories import MemoryRepository
from app.services.sharefile import ShareFileService


def _folder(id_, name):
    return {"Id": id_, "Name": name, "odata.type": "ShareFile.Api.Models.Folder"}


def _pdf(id_, name):
    return {
        "Id": id_,
        "Name": name,
        "odata.type": "ShareFile.Api.Models.File",
        "FileSizeBytes": 1000,
        "ClientModifiedDate": "2026-08-07T10:00:00Z",
        "CreationDate": "2026-08-07T10:00:00Z",
    }


class ScanResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = MemoryRepository()
        self.repo_patch = patch("app.services.sharefile.get_repository", return_value=self.repo)
        self.repo_patch.start()
        await self.repo.upsert_sharefile_token(
            ShareFileOAuthToken(
                access_token="fake",
                refresh_token="fake",
                subdomain="erisapros",
                apicp="sf-api.com",
                appcp="sharefile.com",
                expires_at=datetime.utcnow() + timedelta(hours=8),
            )
        )
        # 3 client trees: client A times out, client B errors with HTTP 500,
        # client C contains a complete new filing package.
        self.tree = {
            "allshared": [
                _folder("f_a", "Client A (Test)"),
                _folder("f_b", "Client B (Test)"),
                _folder("f_c", "Client C (Test)"),
            ],
            "f_c": [_folder("f_c_5500", "5500 Filing")],
            "f_c_5500": [_folder("f_c_2025", "2025 New Client Filing")],
            "f_c_2025": [
                _folder("f_c_sa", "Schedule A's"),
                _pdf("d_ws", "5500 Plan Worksheet - Client C 5500 - PY25 (501).docx"),
            ],
            "f_c_sa": [_pdf("d_sa", "ClientC_Schedule_A.pdf")],
        }

        async def fake_list_folder(_self, client, token, folder_id):
            if folder_id == "f_a":
                raise httpx.ReadTimeout("simulated slow ShareFile folder")
            if folder_id == "f_b":
                request = httpx.Request("GET", "https://x")
                response = httpx.Response(500, request=request, text="boom")
                raise httpx.HTTPStatusError("boom", request=request, response=response)
            return list(self.tree.get(folder_id) or [])

        async def fake_download(_self, client, token, item_id):
            return b"%PDF-1.4 fake " + item_id.encode()

        self.patches = [
            patch.object(ShareFileService, "_list_folder", fake_list_folder),
            patch.object(ShareFileService, "_download_item", fake_download),
            patch.object(ShareFileService, "_should_content_sniff", lambda _self, name: False),
            patch.object(
                ShareFileService,
                "_ensure_access_token",
                new=AsyncMock(side_effect=lambda c, t: t),
            ),
        ]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        self.repo_patch.stop()

    async def test_one_broken_folder_does_not_abort_the_scan(self):
        service = ShareFileService()

        # First scan establishes the baseline (indexes without processing).
        first = await service.sync_changes(BackgroundTasks(), process_new_files=True)
        self.assertTrue(first.get("baseline"))

        # Add the new client folder package AFTER baseline.
        # (Client C tree above is present from the start; move it to "new" by
        # rescanning - the files were baselined, so instead we add a brand-new
        # package under client C now.)
        self.tree["f_c_5500"].append(_folder("f_c_new", "2025 Brand New Filing"))
        self.tree["f_c_new"] = [
            _folder("f_c_new_sa", "Schedule A's"),
            _pdf("d_new_ws", "5500 Plan Worksheet - Client C 5500 - PY25 (501).docx"),
        ]
        self.tree["f_c_new_sa"] = [_pdf("d_new_sa", "ClientC_New_Schedule_A.pdf")]

        result = await service.sync_changes(BackgroundTasks(), process_new_files=True)

        # The scan survived the timeout and the HTTP 500...
        self.assertFalse(result.get("baseline"))
        self.assertGreaterEqual(len(result.get("scan_errors") or []), 2)
        # ...and still discovered and queued the brand-new folder package.
        self.assertEqual(result.get("synced"), 1)
        filings = await self.repo.list_filings()
        names = " ".join(f.file_name or "" for f in filings)
        self.assertIn("ClientC_New_Schedule_A", names)

    async def test_scan_errors_prevent_deletion_reconciliation(self):
        service = ShareFileService()
        await service.sync_changes(BackgroundTasks(), process_new_files=True)  # baseline
        result = await service.sync_changes(BackgroundTasks(), process_new_files=True)
        # With scan errors present, nothing may be marked deleted.
        self.assertEqual(result.get("deleted"), 0)


if __name__ == "__main__":
    unittest.main()


class DeferredSniffAndSpeedTests(unittest.IsolatedAsyncioTestCase):
    """Content sniffing must happen after the walk, only for new/changed files -
    a steady-state rescan must not download anything."""

    async def asyncSetUp(self):
        self.repo = MemoryRepository()
        self.repo_patch = patch("app.services.sharefile.get_repository", return_value=self.repo)
        self.repo_patch.start()
        await self.repo.upsert_sharefile_token(
            ShareFileOAuthToken(
                access_token="fake",
                refresh_token="fake",
                subdomain="erisapros",
                apicp="sf-api.com",
                appcp="sharefile.com",
                expires_at=datetime.utcnow() + timedelta(hours=8),
            )
        )
        self.download_calls = []
        self.tree = {
            "allshared": [_folder("f_c", "Client C (Test)")],
            "f_c": [_folder("f_c_2025", "2025 New Client Filing")],
            "f_c_2025": [
                _folder("f_c_sa", "Schedule A's"),
                _pdf("d_ws", "5500 Plan Worksheet - Client C 5500 - PY25 (501).docx"),
            ],
            "f_c_sa": [],
        }

        outer = self

        async def fake_list_folder(_self, client, token, folder_id):
            return list(outer.tree.get(folder_id) or [])

        async def fake_download(_self, client, token, item_id):
            outer.download_calls.append(item_id)
            return b"%PDF-1.4 SCHEDULE A Insurance Information carrier premium commissions"

        self.patches = [
            patch.object(ShareFileService, "_list_folder", fake_list_folder),
            patch.object(ShareFileService, "_download_item", fake_download),
            patch.object(
                ShareFileService,
                "_ensure_access_token",
                new=AsyncMock(side_effect=lambda c, t: t),
            ),
        ]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        self.repo_patch.stop()

    async def test_steady_state_rescan_downloads_nothing(self):
        service = ShareFileService()
        await service.sync_changes(BackgroundTasks(), process_new_files=True)  # baseline

        # A new file arrives whose name gives no classification hint - it can
        # only be classified by reading its content (deferred sniff).
        self.tree["f_c_sa"].append(_pdf("d_mystery", "carrier_report_2025.pdf"))
        await service.sync_changes(BackgroundTasks(), process_new_files=True)
        self.assertGreater(len(self.download_calls), 0)  # sniff + package download ran

        # Steady state: nothing changed -> rescan must not download anything.
        self.download_calls.clear()
        await service.sync_changes(BackgroundTasks(), process_new_files=True)
        self.assertEqual(self.download_calls, [])

    async def test_scan_status_reports_completion_and_duration(self):
        service = ShareFileService()
        await service.sync_changes(BackgroundTasks(), process_new_files=True)
        status = await service.scan_status()
        self.assertFalse(status["scan_running"])
        self.assertIsNotNone(status["last_scan_completed_at"])
        self.assertIsNotNone(status["last_scan_duration_seconds"])
        self.assertTrue(status["baseline_completed"])

    async def test_discovery_failure_does_not_abort_sync(self):
        import httpx as _httpx

        async def broken_allshared(_self, client, token, folder_id):
            if folder_id == "allshared":
                raise _httpx.ReadTimeout("allshared listing timed out")
            return list(self.tree.get(folder_id) or [])

        with patch.object(ShareFileService, "_list_folder", broken_allshared):
            service = ShareFileService()
            result = await service.sync_changes(BackgroundTasks(), process_new_files=True)
        # No crash; falls back to configured folder IDs (may or may not resolve
        # in this fixture, but the sync must return a result dict).
        self.assertIsInstance(result, dict)
