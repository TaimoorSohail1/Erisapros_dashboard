"""A slow ingest must reach a terminal state before its output is consumed."""
import asyncio
from types import SimpleNamespace

import httpx

from app.config import Settings
from app.services import extractor


def run_delayed_document(monkeypatch, *, completes_at=120, request_seconds=0, terminal_status='complete'):
    clock = [0.0]
    requests = []
    uploads = []
    document = {'documentId': 'current-document', 'fileName': 'slow.txt',
                'sourceUrl': 'https://qa.invalid/current', 'status': 'complete'}

    class Provider:
        def __init__(self, **kwargs):
            pass

        def ingest(self, **kwargs):
            uploads.append(kwargs)
            return SimpleNamespace(model_dump=lambda **kw: {'ingest': {'processId': 'current-process'}})

    def respond(request):
        requests.append((str(request.url), clock[0]))
        clock[0] += request_seconds
        if request.url.path.endswith('/ingest/current-process'):
            complete = clock[0] >= completes_at
            return httpx.Response(200, json={'ingest': {'processId': 'current-process',
                'status': terminal_status if complete else 'training',
                'progress': {'complete': {'documents': [document] if complete else []}}}})
        if '/document/extract/' in request.url.path:
            return httpx.Response(200, json={'schedule_a_contract': [{'insurance_carrier_name': 'Test Carrier'}]})
        if '/document/xray/' in request.url.path:
            return httpx.Response(200, json={'fileName': 'slow.txt', 'sourceUrl': document['sourceUrl'],
                'chunks': [{'pageNumbers': [1], 'text': 'Insurance Carrier: Test Carrier'}]})
        if '/ingest/documents/' in request.url.path:
            return httpx.Response(200, json={'documents': [document]})
        raise AssertionError(request.url)

    async def sleep(seconds):
        clock[0] += seconds

    client = httpx.AsyncClient
    monkeypatch.setattr(extractor.httpx, 'AsyncClient', lambda *a, **kw:
        client(*a, **{**kw, 'transport': httpx.MockTransport(respond)}))
    monkeypatch.setattr(extractor, 'GroundX', Provider)
    monkeypatch.setattr(extractor, 'monotonic', lambda: clock[0], raising=False)
    monkeypatch.setattr(extractor.asyncio, 'sleep', sleep)
    settings = Settings(_env_file=None, groundx_api_key='test-placeholder', groundx_bucket_id=123,
        groundx_poll_seconds=30, groundx_max_wait_seconds=90,
        groundx_operation_timeout_seconds=240, eyelevel_api_key=None, eyelevel_extract_url=None,
        mongodb_uri=None, ftwlink_key_id=None, s3_bucket_name=None)
    monkeypatch.setattr(extractor, 'get_settings', lambda: settings)
    result = asyncio.run(extractor.ExtractionService().extract_schedule_a(b'', 'slow.txt'))
    return result, requests, uploads, clock[0]


def test_late_completion_uses_remaining_operation_budget_without_second_ingest(monkeypatch):
    result, requests, uploads, elapsed = run_delayed_document(monkeypatch)
    assert result.raw['process']['ingest']['status'] == 'complete'
    assert len(uploads) == 1
    assert 120 <= elapsed < 240
    assert all(at >= 120 for url, at in requests if '/document/extract/' in url)


def test_unfinished_document_times_out_without_consuming_artifacts_or_reupload(monkeypatch):
    result, requests, uploads, elapsed = run_delayed_document(monkeypatch, completes_at=1000)
    assert 'manual review' in result.provider.lower()
    assert 'current-process' in result.raw['error']
    assert 'polling budget' in result.raw['error']
    assert len(uploads) == 1 and elapsed == 180
    assert all('/document/' not in url for url, _ in requests)


def test_request_latency_counts_toward_poll_budget(monkeypatch):
    result, requests, uploads, elapsed = run_delayed_document(
        monkeypatch, completes_at=1000, request_seconds=15)
    assert 'manual review' in result.provider.lower()
    assert elapsed == 180 and len(uploads) == 1
    assert len(requests) == 4


def test_immediately_completed_document_does_not_wait_longer(monkeypatch):
    result, requests, uploads, elapsed = run_delayed_document(monkeypatch, completes_at=0)
    assert result.raw['process']['ingest']['status'] == 'complete'
    assert elapsed == 0 and len(uploads) == 1


def test_terminal_provider_failure_does_not_consume_artifacts(monkeypatch):
    result, requests, uploads, elapsed = run_delayed_document(
        monkeypatch, completes_at=0, terminal_status='failed')
    assert 'manual review' in result.provider.lower()
    assert 'failed with status: failed' in result.raw['error']
    assert len(uploads) == 1 and elapsed == 0
    assert all('/document/' not in url for url, _ in requests)
