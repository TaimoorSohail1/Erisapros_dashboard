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
