"""Provider transport encoding must not break strict current-document binding."""
from urllib.parse import quote, quote_plus
import pytest

from app.services.schedule_a_final_evidence import completed_document_source_pages


def payload(name, output_name):
    return {
        'ingest': {'ingest': {'processId': 'current-process'}},
        'process': {'ingest': {'processId': 'current-process', 'status': 'complete',
            'progress': {'complete': {'documents': [{'fileName': name, 'sourceUrl': 'https://qa.invalid/current'}]}}}},
        'outputs': [{'fileName': output_name, 'sourceUrl': 'https://qa.invalid/current',
            'chunks': [{'pageNumbers': [3], 'text': 'source table evidence'}]}],
    }


@pytest.mark.parametrize('name', ['4. Carrier (1).pdf', 'A+B policy.pdf', '100% literal.pdf'])
@pytest.mark.parametrize('encode', [str, quote, quote_plus])
def test_exact_encoded_filename_retains_current_process_and_source_binding(name, encode):
    assert completed_document_source_pages(payload(name, encode(name)), name) == [(3, 'source table evidence')]


@pytest.mark.parametrize('mismatch', ['name', 'document', 'url', 'process', 'status'])
def test_encoded_filename_cannot_bypass_other_document_checks(mismatch):
    name = '4. Carrier (1).pdf'
    raw = payload(name, quote_plus(name))
    if mismatch == 'name': raw['outputs'][0]['fileName'] = quote_plus('another.pdf')
    if mismatch == 'document': raw['process']['ingest']['progress']['complete']['documents'][0]['fileName'] = 'another.pdf'
    if mismatch == 'url': raw['outputs'][0]['sourceUrl'] = 'https://qa.invalid/another'
    if mismatch == 'process': raw['process']['ingest']['processId'] = 'another-process'
    if mismatch == 'status': raw['process']['ingest']['status'] = 'processing'
    assert completed_document_source_pages(raw, name) == []


@pytest.mark.parametrize('queued_host, xray_host', [
    ('upload.groundx.ai', 'upload.eyelevel.ai'),
    ('upload.eyelevel.ai', 'upload.groundx.ai'),
])
def test_provider_upload_aliases_bind_the_same_source_object(queued_host, xray_host):
    name = '4. Carrier (1).pdf'
    raw = payload(name, quote_plus(name))
    path = '/prod/file/ssp/12345678-1234-1234-1234-123456789abc.pdf'
    document = raw['process']['ingest']['progress']['complete']['documents'][0]
    document['sourceUrl'] = f'https://{queued_host}{path}'
    raw['outputs'][0]['sourceUrl'] = f'https://{xray_host}{path}'
    assert completed_document_source_pages(raw, name) == [(3, 'source table evidence')]


@pytest.mark.parametrize('alias_url', [
    'https://upload.eyelevel.ai/prod/file/ssp/12345678-1234-1234-1234-123456789def.pdf',
    'http://upload.eyelevel.ai/prod/file/ssp/12345678-1234-1234-1234-123456789abc.pdf',
    'https://upload.eyelevel.ai:443/prod/file/ssp/12345678-1234-1234-1234-123456789abc.pdf',
    'https://upload.eyelevel.ai/prod/file/ssp/12345678-1234-1234-1234-123456789abc.pdf?other=1',
    'https://upload.eyelevel.ai.example/prod/file/ssp/12345678-1234-1234-1234-123456789abc.pdf',
])
def test_provider_upload_aliases_reject_other_objects_and_origins(alias_url):
    name = 'Carrier.pdf'
    raw = payload(name, name)
    document = raw['process']['ingest']['progress']['complete']['documents'][0]
    document['sourceUrl'] = 'https://upload.groundx.ai/prod/file/ssp/12345678-1234-1234-1234-123456789abc.pdf'
    raw['outputs'][0]['sourceUrl'] = alias_url
    assert completed_document_source_pages(raw, name) == []
