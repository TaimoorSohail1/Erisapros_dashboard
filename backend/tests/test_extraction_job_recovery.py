import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models import DocumentType, ExtractionJob, ExtractionJobStatus, Filing, NormalizedExtractionResult
from app.repositories import MemoryRepository
from app.services.filing_pipeline import process_package_extraction_job


@pytest.mark.parametrize('stage', ['extraction', 'after_mapping', 'legacy_interrupted', 'already_completed'])
def test_interruption_and_redelivery_preserve_completion_and_avoid_unsafe_retries(stage):
    repo = MemoryRepository()
    extract = AsyncMock(return_value=NormalizedExtractionResult(provider='qa', fields=[]))
    query = AsyncMock()
    if stage == 'extraction': extract.side_effect = asyncio.CancelledError()
    if stage == 'after_mapping': query.side_effect = asyncio.CancelledError()

    async def run():
        filing = await repo.create_filing(Filing(file_name='schedule-a.txt', content_type='text/plain',
                                               file_size=6, s3_key='qa/source'))
        initial = ExtractionJobStatus.COMPLETED if stage == 'already_completed' else (
            ExtractionJobStatus.EXTRACTING if stage == 'legacy_interrupted' else ExtractionJobStatus.QUEUED)
        job = await repo.create_extraction_job(ExtractionJob(filing_id=filing.id,
            status=initial, attempts=1 if stage == 'legacy_interrupted' else 0, max_attempts=1))
        try:
            await process_package_extraction_job(filing.id, job.id, [{
                'file_name':'schedule-a.txt','file_bytes':b'source','document_type':DocumentType.SCHEDULE_A}])
        except asyncio.CancelledError:
            assert stage in {'extraction','after_mapping'}
        return (await repo.list_extraction_jobs(filing.id))[0]

    with patch('app.services.filing_pipeline.get_repository', return_value=repo), \
         patch('app.services.filing_pipeline.ExtractionService', return_value=SimpleNamespace(extract_document=extract)), \
         patch('app.services.filing_pipeline.auto_query_ftw_current', new=query):
        job = asyncio.run(run())
    if stage in {'after_mapping', 'already_completed'}:
        assert job.status == ExtractionJobStatus.COMPLETED
    else:
        assert job.status == ExtractionJobStatus.FAILED
    if stage in {'legacy_interrupted', 'already_completed'}:
        extract.assert_not_awaited()
        query.assert_not_awaited()
    if stage == 'legacy_interrupted':
        assert 'no upload was repeated' in job.last_error
