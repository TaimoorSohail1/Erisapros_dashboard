import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models import DocumentType, ExtractionJob, ExtractionJobStatus, Filing, NormalizedExtractionResult
from app.repositories import MemoryRepository
from app.services.filing_pipeline import process_package_extraction_job


@pytest.mark.parametrize('changed', [False, True])
def test_stored_source_is_loaded_and_hash_checked_before_extraction(changed):
    data = b'%PDF-original-source'
    repo = MemoryRepository()
    extract = AsyncMock(return_value=NormalizedExtractionResult(provider='qa', fields=[]))
    query = AsyncMock()

    async def run():
        filing = await repo.create_filing(Filing(file_name='schedule-a.pdf', content_type='application/pdf',
                                               file_size=len(data), s3_key='qa/source.pdf'))
        job = await repo.create_extraction_job(ExtractionJob(filing_id=filing.id, max_attempts=1))
        await process_package_extraction_job(filing.id, job.id, [{
            'file_name': 'schedule-a.pdf', 'document_type': DocumentType.SCHEDULE_A,
            's3_key': 'qa/source.pdf', 's3_bucket': 'private-qa',
            'source_sha256': hashlib.sha256(data).hexdigest(),
        }])
        return (await repo.list_extraction_jobs(filing.id))[0]

    with patch('app.services.filing_pipeline.get_repository', return_value=repo), \
         patch('app.services.filing_pipeline.StorageService.load_pdf', return_value=b'changed' if changed else data), \
         patch('app.services.filing_pipeline.ExtractionService', return_value=SimpleNamespace(extract_document=extract)), \
         patch('app.services.filing_pipeline.auto_query_ftw_current', new=query):
        job = asyncio.run(run())
    if changed:
        assert job.status == ExtractionJobStatus.FAILED
        assert 'source differs' in job.last_error
        extract.assert_not_awaited()
        query.assert_not_awaited()
    else:
        assert job.status == ExtractionJobStatus.COMPLETED
        extract.assert_awaited_once_with(data, 'schedule-a.pdf', DocumentType.SCHEDULE_A)
        query.assert_awaited_once()
