"""Exercise the real resolver across cached poll and fresh webhook ancestry."""
import asyncio
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.repositories as repositories
from app.models import DocumentType
from app.services.sharefile import ShareFileService


def run(coro):
    return asyncio.run(coro)


def file(item_id, name, *, prefix=False, folder='qa-2025', kind=None, modified='2026-10-20T00:00:00Z'):
    parts = (['Folders', 'ERISA Pros'] if prefix else []) + ['Example Client', '5500 Filing', '2025 Filing QA', name]
    return dict(id=item_id, name=name, path_parts=parts, path=' > '.join(parts),
        parent_id=folder, root_folder_id='client-root', folder_ids_by_depth={str(len(parts)-2): folder},
        document_type=kind, needs_content_sniff=kind is None, size=100,
        modified_at=modified, created_at='2026-10-01T00:00:00Z', raw={})


class PhysicalPackageIdentityTests(unittest.TestCase):
    def setUp(self):
        repositories._repository = repositories.MemoryRepository()
        self.repo = repositories.get_repository()
        self.service = ShareFileService()
        self.service._classify_sharefile_document_by_content = AsyncMock(return_value=DocumentType.PLAN_WORKSHEET)

    def tearDown(self):
        repositories._repository = None

    def index(self, item, *, legacy=False):
        record = self.service._sharefile_index_record(item, status='EXTRACTED')
        if legacy:
            record.pop('package_folder_id', None)
        run(self.repo.upsert_sharefile_file(item['id'], record))

    def resolve(self, scanned):
        schedule = file('new-sa', 'Vision Schedule A.pdf', prefix=True, kind=DocumentType.SCHEDULE_A)
        schedule['change_type'] = 'NEW'
        self.service._scan_folder = AsyncMock(return_value=deepcopy(scanned))
        packages = run(self.service._resolve_package_candidates(None, None, [schedule]))
        matching = [items for items in packages.values() if any(i['id']=='new-sa' for i in items)]
        self.assertEqual(len(matching), 1)
        return matching[0]

    def test_legacy_poll_pws_reused_by_item_version_after_prefixed_webhook(self):
        worksheet = file('pws', '5500 PWS - Example - PY25.docx', kind=DocumentType.PLAN_WORKSHEET)
        self.index(worksheet, legacy=True)
        scanned = file('pws', worksheet['name'], prefix=True)
        result = self.resolve([scanned])
        self.assertEqual({i['id'] for i in result}, {'new-sa', 'pws'})
        self.service._classify_sharefile_document_by_content.assert_not_awaited()

    def test_new_pws_is_content_classified_before_sibling_filter(self):
        result = self.resolve([file('new-pws', '5500 PWS.docx', prefix=True)])
        self.assertEqual({i['id'] for i in result}, {'new-sa', 'new-pws'})
        self.service._classify_sharefile_document_by_content.assert_awaited_once()

    def test_changed_pws_does_not_reuse_cached_document_type(self):
        worksheet = file('pws', '5500 PWS.docx', kind=DocumentType.PLAN_WORKSHEET)
        self.index(worksheet, legacy=True)
        self.service._classify_sharefile_document_by_content.return_value = DocumentType.UNKNOWN
        result = self.resolve([file('pws', worksheet['name'], prefix=True, modified='2026-10-21T00:00:00Z')])
        self.assertEqual([i['id'] for i in result], ['new-sa'])
        self.service._classify_sharefile_document_by_content.assert_awaited_once()

    def test_same_display_path_different_physical_folder_is_not_paired(self):
        worksheet = file('wrong', '5500 Plan Worksheet.docx', prefix=True, folder='other-client-folder', kind=DocumentType.PLAN_WORKSHEET)
        self.index(worksheet)
        result = self.resolve([])
        self.assertEqual([i['id'] for i in result], ['new-sa'])

    def test_physical_folder_lookup_reuses_index_despite_display_ancestry(self):
        worksheet = file('indexed', '5500 Plan Worksheet.docx', kind=DocumentType.PLAN_WORKSHEET)
        self.index(worksheet)
        result = self.resolve([])
        self.assertEqual({i['id'] for i in result}, {'new-sa', 'indexed'})

    def test_excluded_cover_is_not_downloaded_for_content_sniff(self):
        result = self.resolve([file('cover', 'cover.docx', prefix=True)])
        self.assertEqual([i['id'] for i in result], ['new-sa'])
        self.service._classify_sharefile_document_by_content.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
