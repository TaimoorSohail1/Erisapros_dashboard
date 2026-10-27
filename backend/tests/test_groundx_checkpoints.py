import asyncio
from unittest.mock import AsyncMock
import pytest

from app.models import FormType
from app.repositories import MemoryRepository
from app.services.groundx_checkpoints import binding_key, job_checkpoint_journal, source_binding


def test_restart_polls_the_saved_provider_process_without_ingesting_again():
    from unittest.mock import patch
    from app.config import Settings
    from app.services.extractor import ExtractionService
    async def run():
        repo = MemoryRepository()
        binding = source_binding(b'source text', 'source.txt', FormType.SCHEDULE_A, 42)
        entries, record = await job_checkpoint_journal(repo, 'filing', 'job', 'rules')
        await record(binding, 'ACCEPTED', 'existing-process')
        service = ExtractionService()
        service.groundx_resume_checkpoints, service.groundx_checkpoint_callback = entries, record
        service._ingest_groundx_file = AsyncMock(side_effect=AssertionError('Duplicate ingestion'))
        service._poll_groundx_process = AsyncMock(return_value={'ingest': {'processId': 'existing-process', 'status': 'complete'}})
        service._fetch_groundx_structured_extracts = AsyncMock(return_value=[])
        service._fetch_groundx_xray_payloads = AsyncMock(return_value=[])
        service._search_groundx_with_field_schema = AsyncMock(return_value=None)
        cfg = Settings(_env_file=None, groundx_api_key='offline', groundx_bucket_id=42)
        with patch('app.services.extractor.get_settings', return_value=cfg):
            result = await service._extract_with_groundx(b'source text', 'source.txt', FormType.SCHEDULE_A, 'Schedule A')
        service._ingest_groundx_file.assert_not_awaited()
        assert service._poll_groundx_process.await_args.args[3] == 'existing-process'
        assert result.raw['process']['ingest']['status'] == 'complete'
    asyncio.run(run())


def test_ambiguous_submission_cannot_ingest_again():
    import pytest
    from unittest.mock import patch
    from app.config import Settings
    from app.services.extractor import ExtractionService
    from app.services.groundx_checkpoints import GroundXSubmissionUncertain
    async def run():
        repo = MemoryRepository()
        binding = source_binding(b'source text', 'source.txt', FormType.SCHEDULE_A, 42)
        entries, record = await job_checkpoint_journal(repo, 'filing', 'job', 'rules')
        await record(binding, 'SUBMITTING')
        service = ExtractionService()
        service.groundx_resume_checkpoints, service.groundx_checkpoint_callback = entries, record
        service._ingest_groundx_file = AsyncMock(side_effect=AssertionError('Duplicate ingestion'))
        with patch('app.services.extractor.get_settings', return_value=Settings(_env_file=None, groundx_api_key='offline', groundx_bucket_id=42)):
            with pytest.raises(GroundXSubmissionUncertain):
                await service._extract_with_groundx(b'source text', 'source.txt', FormType.SCHEDULE_A, 'Schedule A')
        service._ingest_groundx_file.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['timeout', 'missing_process', 'accepted_checkpoint_failure'])
def test_submission_error_is_not_hidden_by_local_fallback_or_retried(failure):
    import pytest
    from unittest.mock import patch
    from app.config import Settings
    from app.services.extractor import ExtractionService
    from app.services.groundx_checkpoints import GroundXSubmissionUncertain
    async def run():
        repo = MemoryRepository()
        entries, record = await job_checkpoint_journal(repo, 'filing', 'job', 'rules')
        service = ExtractionService()
        async def recorder(binding, status, process_id=None):
            if failure == 'accepted_checkpoint_failure' and status == 'ACCEPTED':
                raise RuntimeError('checkpoint storage unavailable')
            await record(binding, status, process_id)
        service.groundx_resume_checkpoints, service.groundx_checkpoint_callback = entries, recorder
        service._ingest_groundx_file = (AsyncMock(side_effect=TimeoutError('submission response lost'))
            if failure == 'timeout' else AsyncMock(return_value={'ingest': {} if failure == 'missing_process'
                                                                else {'processId': 'accepted-process'}}))
        with patch('app.services.extractor.get_settings', return_value=Settings(_env_file=None, groundx_api_key='offline', groundx_bucket_id=42)):
            with pytest.raises(GroundXSubmissionUncertain):
                await service.extract_schedule_a(b'not a PDF', 'source.txt')
            with pytest.raises(GroundXSubmissionUncertain):
                await service.extract_schedule_a(b'not a PDF', 'source.txt')
        service._ingest_groundx_file.assert_awaited_once()
    asyncio.run(run())


def test_journal_reuses_only_this_job_source_bucket_and_rule_version():
    async def run():
        repo = MemoryRepository()
        binding = source_binding(b'original', 'source.pdf', FormType.SCHEDULE_A, 42)
        entries, record = await job_checkpoint_journal(repo, 'filing', 'job', 'rules-a')
        await record(binding, 'SUBMITTING')
        await record(binding, 'ACCEPTED', 'existing-process')
        restarted, _ = await job_checkpoint_journal(repo, 'filing', 'job', 'rules-a')
        assert restarted[binding_key(binding)]['process_id'] == 'existing-process'
        assert binding_key(source_binding(b'changed', 'source.pdf', FormType.SCHEDULE_A, 42)) not in restarted
        assert binding_key(source_binding(b'original', 'source.pdf', FormType.SCHEDULE_A, 43)) not in restarted
        assert (await job_checkpoint_journal(repo, 'filing', 'another-job', 'rules-a'))[0] == {}
        import pytest
        from app.services.groundx_checkpoints import GroundXSubmissionUncertain
        with pytest.raises(GroundXSubmissionUncertain):
            await job_checkpoint_journal(repo, 'filing', 'job', 'rules-b')
    asyncio.run(run())


def test_failed_journal_persistence_does_not_claim_a_durable_process():
    async def run():
        repo = MemoryRepository()
        binding = source_binding(b'original', 'source.pdf', FormType.SCHEDULE_A, 42)
        entries, record = await job_checkpoint_journal(repo, 'filing', 'job', 'rules-a')
        repo.add_audit = AsyncMock(side_effect=RuntimeError('storage unavailable'))
        try:
            await record(binding, 'ACCEPTED', 'process')
        except RuntimeError:
            pass
        else:
            raise AssertionError('Journal failure was hidden')
        assert not entries
    asyncio.run(run())
