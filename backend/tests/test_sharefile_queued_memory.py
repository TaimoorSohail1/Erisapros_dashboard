"""Bulk intake must not retain all downloaded file contents until extraction."""
import asyncio
import gc
import tracemalloc
from unittest.mock import patch

from app.models import DocumentType
from app.repositories import MemoryRepository
from app.services.sharefile import ShareFileService


def test_queued_package_memory_does_not_grow_with_downloaded_pdf_contents():
    repo = MemoryRepository()
    service = ShareFileService()

    async def download(*args):
        return b'%PDF-1.4\n' + bytes(1024 * 1024)

    def save(_self, name, content_type, data):
        # External storage boundary; do not retain mock call arguments/bytes.
        return {'key': 'qa/' + name, 'bucket': 'private-qa', 'id': name}

    service._download_item = download

    async def intake():
        pending = []
        for index in range(24):
            name = f'Carrier Schedule A {index}.pdf'
            item = {'id': f'fi{index}', 'name': name, 'parent_id': 'qa-folder',
                    'document_type': DocumentType.SCHEDULE_A,
                    'path': 'TEST > 5500 Filing > 2025 > Schedule A > ' + name,
                    'path_parts': ['TEST', '5500 Filing', '2025', 'Schedule A', name]}
            pending.append(await service._create_filing_package(None, None, 'qa-package-' + str(index), [item]))
        gc.collect()
        return pending, tracemalloc.get_traced_memory()[0]

    tracemalloc.start()
    try:
        with patch('app.services.sharefile.get_repository', return_value=repo), \
             patch('app.services.sharefile.StorageService.save_file', new=save):
            pending, retained = asyncio.run(intake())
        assert len(pending) == 24
        assert retained < 6 * 1024 * 1024, f'Queued intake retained {retained} bytes for 24 one-MiB files'
    finally:
        tracemalloc.stop()
