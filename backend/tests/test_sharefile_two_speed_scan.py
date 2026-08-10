"""Frequent polls must be cheap, and a brand-new client folder must still be
found within one poll.

Before this, every scheduled poll walked every folder of every client - on the
production account that is ~7,400 files and takes about an hour, so a new
client folder could sit unnoticed for a full sweep while the account was kept
permanently busy.

The two-speed scan walks only the filing structure on each poll, deep-scans
whatever is brand new, and runs the exhaustive sweep on a slower cadence.
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks

from app.config import get_settings
from app.models import ShareFileOAuthToken
from app.repositories import MemoryRepository
from app.services.sharefile import SHAREFILE_INCREMENTAL_STATE_KEY, ShareFileService


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


class TwoSpeedScanTests(unittest.IsolatedAsyncioTestCase):
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

        # Two existing clients, each with a filing tree that is deliberately
        # deeper than the quick scan looks.
        self.tree = {
            "allshared": [_folder("f_a", "Client A (Test)"), _folder("f_b", "Client B (Test)")],
            # Clients also hold plenty of folders that have nothing to do with
            # 5500 filings. Walking those is what makes the deep sweep slow.
            "f_a": [_folder("f_a_5500", "5500 Filing"), _folder("f_a_junk", "Payroll")],
            "f_a_junk": [_folder("f_a_junk_2024", "2024 Payroll Records")],
            "f_a_junk_2024": [_folder("f_a_junk_q1", "Q1")],
            "f_a_junk_q1": [_pdf("d_junk", "payroll_register.pdf")],
            "f_a_5500": [_folder("f_a_2024", "2024 Filing")],
            "f_a_2024": [
                _folder("f_a_sa", "Schedule A's"),
                _pdf("d_a_ws", "5500 Plan Worksheet - Client A 5500 - PY24 (501).docx"),
            ],
            "f_a_sa": [_pdf("d_a_sa", "ClientA_Schedule_A.pdf")],
            "f_b": [_folder("f_b_5500", "5500 Filing")],
            "f_b_5500": [_folder("f_b_2024", "2024 Filing")],
            "f_b_2024": [_folder("f_b_sa", "Schedule A's")],
            "f_b_sa": [],
        }

        self.listed = []
        outer = self

        async def fake_list_folder(_self, client, token, folder_id):
            outer.listed.append(folder_id)
            return list(outer.tree.get(folder_id) or [])

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
        get_settings.cache_clear()

    def _add_new_client(self):
        """A brand-new client folder with a complete filing package."""
        self.tree["allshared"].append(_folder("f_new", "Brand New Client (Test)"))
        self.tree["f_new"] = [_folder("f_new_5500", "5500 Filing")]
        self.tree["f_new_5500"] = [_folder("f_new_2025", "2025 Filing")]
        self.tree["f_new_2025"] = [
            _folder("f_new_sa", "Schedule A's"),
            _pdf("d_new_ws", "5500 Plan Worksheet - Brand New Client 5500 - PY25 (501).docx"),
        ]
        self.tree["f_new_sa"] = [_pdf("d_new_sa", "BrandNew_Schedule_A.pdf")]

    async def _baseline(self, service):
        result = await service.sync_changes(BackgroundTasks(), process_new_files=True)
        self.assertTrue(result.get("baseline"))
        self.assertEqual(result.get("scan_mode"), "deep")
        return result

    async def test_quick_poll_finds_a_brand_new_client_folder(self):
        service = ShareFileService()
        await self._baseline(service)

        self._add_new_client()
        self.listed.clear()
        result = await service.sync_changes(BackgroundTasks(), process_new_files=True)

        self.assertEqual(result.get("scan_mode"), "quick")
        self.assertGreaterEqual(result.get("new_folders"), 1)
        # The new client's Schedule A package was picked up by the quick poll.
        self.assertEqual(result.get("synced"), 1)
        filings = await self.repo.list_filings()
        names = " ".join(f.file_name or "" for f in filings)
        self.assertIn("BrandNew_Schedule_A", names)

        # ...without walking down the folders that have nothing to do with
        # filings.
        self.assertNotIn("f_a_junk_q1", self.listed)

    async def test_new_upload_does_not_require_a_full_index_read(self):
        service = ShareFileService()
        await self._baseline(service)

        self._add_new_client()
        with patch.object(
            self.repo,
            "list_sharefile_files",
            new=AsyncMock(side_effect=AssertionError("full ShareFile index read is forbidden")),
        ):
            result = await service.sync_changes(BackgroundTasks(), process_new_files=True)

        self.assertEqual(result.get("synced"), 1)
        filings = await self.repo.list_filings()
        names = " ".join(f.file_name or "" for f in filings)
        self.assertIn("BrandNew_Schedule_A", names)

    async def test_waiting_package_repair_does_not_require_a_full_index_read(self):
        service = ShareFileService()
        await self._baseline(service)

        self.tree["f_b_2024"].append(
            _pdf("d_b_ws", "5500 Plan Worksheet - Client B 5500 - PY24 (501).docx")
        )
        await service.sync_changes(BackgroundTasks(), process_new_files=True)

        self.tree["f_b_sa"].append(_pdf("d_b_sa", "ClientB_Schedule_A.pdf"))
        with patch.object(
            self.repo,
            "list_sharefile_files",
            new=AsyncMock(side_effect=AssertionError("full ShareFile index read is forbidden")),
        ):
            result = await service.sync_changes(BackgroundTasks(), process_new_files=True)

        self.assertGreaterEqual(result.get("synced", 0), 1)
        filings = await self.repo.list_filings()
        names = " ".join(f.file_name or "" for f in filings)
        self.assertIn("ClientB_Schedule_A", names)

    async def test_unchanged_poll_uses_bulk_repository_access(self):
        service = ShareFileService()
        await self._baseline(service)

        original_bulk_upsert = self.repo.upsert_sharefile_files
        bulk_upsert = AsyncMock(wraps=original_bulk_upsert)

        with (
            patch.object(
                self.repo,
                "get_sharefile_file",
                new=AsyncMock(side_effect=AssertionError("per-file index reads are forbidden")),
            ),
            patch.object(
                self.repo,
                "get_filing_by_sharefile_item_id",
                new=AsyncMock(side_effect=AssertionError("per-file filing reads are forbidden")),
            ),
            patch.object(
                self.repo,
                "upsert_sharefile_file",
                new=AsyncMock(side_effect=AssertionError("per-file index writes are forbidden")),
            ),
            patch.object(self.repo, "upsert_sharefile_files", new=bulk_upsert),
        ):
            result = await service.sync_changes(BackgroundTasks(), process_new_files=False)

        self.assertEqual(result.get("scan_mode"), "quick")
        self.assertEqual(result.get("updated"), 0)
        bulk_upsert.assert_awaited_once_with({})

    async def test_quick_poll_walks_the_filing_structure_but_not_the_rest(self):
        service = ShareFileService()
        await self._baseline(service)

        self.listed.clear()
        result = await service.sync_changes(BackgroundTasks(), process_new_files=True)

        self.assertEqual(result.get("scan_mode"), "quick")
        self.assertEqual(result.get("new_folders"), 0)
        self.assertEqual(result.get("scanned_targets"), 0)
        # The filing structure is still checked, so a document dropped into an
        # existing Schedule A's folder is never missed...
        self.assertIn("f_a_sa", self.listed)
        # ...but the client's unrelated folders are left alone.
        self.assertNotIn("f_a_junk_q1", self.listed)

    async def test_deep_sweep_walks_everything_the_quick_poll_skips(self):
        service = ShareFileService()
        await self._baseline(service)
        self.listed.clear()
        await service.sync_folder(BackgroundTasks())
        self.assertIn("f_a_junk_q1", self.listed)

    async def test_deep_sweep_deletion_reconciliation_uses_lightweight_query(self):
        service = ShareFileService()
        await self._baseline(service)

        with patch.object(
            self.repo,
            "list_sharefile_files",
            new=AsyncMock(side_effect=AssertionError("full ShareFile index read is forbidden")),
        ):
            result = await service.sync_folder(BackgroundTasks())

        self.assertEqual(result.get("scan_mode"), "deep")
        self.assertEqual(result.get("deleted"), 0)

    async def test_quick_poll_picks_up_a_new_file_in_an_existing_folder(self):
        service = ShareFileService()
        await self._baseline(service)

        self.tree["f_b_sa"].append(_pdf("d_b_sa", "ClientB_Schedule_A.pdf"))
        result = await service.sync_changes(BackgroundTasks(), process_new_files=True)

        self.assertEqual(result.get("scan_mode"), "quick")
        self.assertEqual(result.get("new_folders"), 0)
        filings = await self.repo.list_filings()
        names = " ".join(f.file_name or "" for f in filings)
        self.assertIn("ClientB_Schedule_A", names)

    async def test_quick_poll_finds_a_new_year_folder_inside_an_existing_client(self):
        service = ShareFileService()
        await self._baseline(service)

        self.tree["f_a_5500"].append(_folder("f_a_2025", "2025 Filing"))
        self.tree["f_a_2025"] = [
            _folder("f_a_2025_sa", "Schedule A's"),
            _pdf("d_a25_ws", "5500 Plan Worksheet - Client A 5500 - PY25 (501).docx"),
        ]
        self.tree["f_a_2025_sa"] = [_pdf("d_a25_sa", "ClientA_2025_Schedule_A.pdf")]

        result = await service.sync_changes(BackgroundTasks(), process_new_files=True)

        self.assertEqual(result.get("scan_mode"), "quick")
        self.assertEqual(result.get("synced"), 1)
        filings = await self.repo.list_filings()
        names = " ".join(f.file_name or "" for f in filings)
        self.assertIn("ClientA_2025_Schedule_A", names)

    async def test_a_new_client_tree_is_scanned_once_not_per_subfolder(self):
        service = ShareFileService()
        await self._baseline(service)

        self._add_new_client()
        result = await service.sync_changes(BackgroundTasks(), process_new_files=True)
        # The client folder, its 5500 folder and its year folder are all new,
        # but walking the client folder already covers them.
        self.assertEqual(result.get("scanned_targets"), 1)

    async def test_deep_sweep_runs_when_due_and_not_before(self):
        service = ShareFileService()
        await self._baseline(service)

        # Straight after the baseline, a deep sweep is not due.
        result = await service.sync_changes(BackgroundTasks(), process_new_files=True)
        self.assertEqual(result.get("scan_mode"), "quick")

        # Age the last deep sweep past the interval.
        interval = get_settings().sharefile_deep_scan_interval_hours
        await self.repo.upsert_sharefile_state(
            SHAREFILE_INCREMENTAL_STATE_KEY,
            {"last_deep_scan_at": datetime.utcnow() - timedelta(hours=interval + 1)},
        )
        result = await service.sync_changes(BackgroundTasks(), process_new_files=True)
        self.assertEqual(result.get("scan_mode"), "deep")
        self.assertIn("f_a_sa", self.listed)

    async def test_manual_sync_button_always_runs_a_deep_sweep(self):
        service = ShareFileService()
        await self._baseline(service)

        self.listed.clear()
        result = await service.sync_folder(BackgroundTasks())
        self.assertEqual(result.get("scan_mode"), "deep")
        self.assertIn("f_a_sa", self.listed)

    async def test_quick_poll_never_marks_unvisited_files_deleted(self):
        service = ShareFileService()
        await self._baseline(service)
        # Client A's Schedule A is on record from the baseline but a quick
        # poll does not visit it - it must not be treated as removed.
        result = await service.sync_changes(BackgroundTasks(), process_new_files=True)
        self.assertEqual(result.get("scan_mode"), "quick")
        self.assertEqual(result.get("deleted"), 0)
        record = await self.repo.get_sharefile_file("d_a_sa")
        self.assertIsNotNone(record)
        self.assertNotEqual((record or {}).get("status"), "DELETED")

    async def test_a_failed_listing_does_not_make_folders_look_new_forever(self):
        import httpx

        service = ShareFileService()
        await self._baseline(service)

        state = await self.repo.get_sharefile_state(SHAREFILE_INCREMENTAL_STATE_KEY) or {}
        known_before = set(state.get("known_folder_ids") or [])
        self.assertIn("f_a_5500", known_before)

        original = self.tree.pop("f_a")

        async def flaky_list_folder(_self, client, token, folder_id):
            self.listed.append(folder_id)
            if folder_id == "f_a":
                raise httpx.ReadTimeout("simulated ShareFile hiccup")
            return list(self.tree.get(folder_id) or [])

        with patch.object(ShareFileService, "_list_folder", flaky_list_folder):
            await service.sync_changes(BackgroundTasks(), process_new_files=True)

        self.tree["f_a"] = original
        state = await self.repo.get_sharefile_state(SHAREFILE_INCREMENTAL_STATE_KEY) or {}
        known_after = set(state.get("known_folder_ids") or [])
        # The folders under the folder that failed to list are still known,
        # so the next poll does not redundantly deep-scan them.
        self.assertTrue(known_before.issubset(known_after))

        result = await service.sync_changes(BackgroundTasks(), process_new_files=True)
        self.assertEqual(result.get("scan_mode"), "quick")
        self.assertEqual(result.get("scanned_targets"), 0)

    async def test_scan_status_reports_the_mode_and_last_deep_sweep(self):
        service = ShareFileService()
        await self._baseline(service)
        await service.sync_changes(BackgroundTasks(), process_new_files=True)

        status = await service.scan_status()
        self.assertEqual(status["last_scan_mode"], "quick")
        self.assertIsNotNone(status["last_deep_scan_at"])
        self.assertIsNotNone(status["last_quick_scan_at"])
        self.assertGreater(status["known_folder_count"], 0)
        self.assertFalse(status["scan_running"])


if __name__ == "__main__":
    unittest.main()
