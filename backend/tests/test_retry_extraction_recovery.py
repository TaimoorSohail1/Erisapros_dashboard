import asyncio
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException
import pytest

from app.api.filings import retry_extraction
from app.config import Settings
from app.models import ExtractionJob, ExtractionJobStatus, Filing, FormType
from app.repositories import MemoryRepository
from app.services.field_rule_admin import FieldRuleService
from app.services.groundx_checkpoints import job_checkpoint_journal, source_binding


@pytest.mark.parametrize('scenario', ['active','legacy_failed','accepted','ambiguous','changed_source','changed_rules'])
def test_retry_cannot_bypass_a_prior_submission_journal(scenario):
    async def run():
        repo=MemoryRepository()
        filing=await repo.create_filing(Filing(file_name='schedule-a.pdf',content_type='application/pdf',
            file_size=6,s3_key='qa/source.pdf'))
        job=await repo.create_extraction_job(ExtractionJob(filing_id=filing.id,attempts=1,
            status=ExtractionJobStatus.EXTRACTING if scenario=='active' else ExtractionJobStatus.FAILED))
        rules=await FieldRuleService(repo).published_snapshot()
        if scenario not in {'active','legacy_failed'}:
            _,record=await job_checkpoint_journal(repo,filing.id,job.id,
                'old-rule-version' if scenario=='changed_rules' else rules.version)
            await record(source_binding(b'source','schedule-a.pdf',FormType.SCHEDULE_A,42),
                'SUBMITTING' if scenario=='ambiguous' else 'ACCEPTED',None if scenario=='ambiguous' else 'existing-process')
        tasks=BackgroundTasks()
        with patch('app.api.filings.get_repository',return_value=repo), \
             patch('app.api.filings.get_settings',return_value=Settings(_env_file=None,groundx_bucket_id=42)), \
             patch('app.api.filings.StorageService.load_pdf',return_value=b'changed' if scenario=='changed_source' else b'source'):
            if scenario=='accepted':
                result=await retry_extraction(filing.id,tasks)
                assert result['job_id']==job.id
                assert tasks.tasks[0].args[1]==job.id
                with pytest.raises(HTTPException) as repeated:
                    await retry_extraction(filing.id,BackgroundTasks())
                assert repeated.value.status_code==409
            else:
                with pytest.raises(HTTPException) as error:
                    await retry_extraction(filing.id,tasks)
                assert error.value.status_code==409
                assert not tasks.tasks
        assert len(await repo.list_extraction_jobs(filing.id))==1
    asyncio.run(run())


@pytest.mark.parametrize('completed', [False,True])
def test_fresh_or_explicit_completed_reextraction_keeps_its_existing_contract(completed):
    async def run():
        repo=MemoryRepository()
        filing=await repo.create_filing(Filing(file_name='schedule-a.pdf',content_type='application/pdf',file_size=6,s3_key='qa/source.pdf'))
        if completed:
            await repo.create_extraction_job(ExtractionJob(filing_id=filing.id,status=ExtractionJobStatus.COMPLETED,attempts=1))
        tasks=BackgroundTasks()
        with patch('app.api.filings.get_repository',return_value=repo),patch('app.api.filings.StorageService.load_pdf',return_value=b'source'):
            response=await retry_extraction(filing.id,tasks)
        assert set(response)=={'id','status','job_id'}
        assert len(tasks.tasks)==1
        assert len(await repo.list_extraction_jobs(filing.id))==(2 if completed else 1)
    asyncio.run(run())
