"""Exercise the installed SDK's real POST retry behavior, offline."""
from unittest.mock import patch

import httpx
import pytest
from groundx import GroundX
from groundx.types.ingest_remote_document import IngestRemoteDocument

from app.config import Settings
from app.services.extractor import ExtractionService


@pytest.mark.parametrize('failure', [500, 502, 429, 'protocol'])
def test_provider_submission_error_does_not_trigger_hidden_sdk_post_retries(failure):
    calls = []

    def respond(request):
        calls.append(request)
        if failure == 'protocol':
            raise httpx.RemoteProtocolError('submission response lost')
        return httpx.Response(failure, json={'message': 'submission outcome unknown'})

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        sdk = GroundX(api_key='offline', httpx_client=transport)
        # Replace only the file-hosting boundary; the real SDK builds and sends
        # the ingestion POST through its actual retry implementation.
        sdk._process_local = lambda *args: ([IngestRemoteDocument(
            bucket_id=42, file_name='source.txt', file_type='txt', source_url='https://qa.invalid/source')], 0)
        with patch('app.services.extractor.GroundX', return_value=sdk), \
             patch('app.services.extractor.get_settings', return_value=Settings(_env_file=None,
                   groundx_api_key='offline', groundx_bucket_id=42)), \
             patch('groundx.core.http_client.time.sleep'):
            with pytest.raises(Exception):
                ExtractionService()._ingest_groundx_file_with_sdk(b'source', 'source.txt')
    assert len(calls) == 1, f'The SDK repeated the provider submission {len(calls)} times'
    assert calls[0].method == 'POST'
