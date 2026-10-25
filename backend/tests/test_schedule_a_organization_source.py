"""Provider recipient codes require their own source column, not name tokens."""
import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.models import DocumentType, FormType, NormalizedExtractionResult, ScheduleABrokerRow
from app.services import extractor
from app.services.field_rules import DEFAULT_FIELD_RULES
from app.services.mapping import map_extraction_to_rules
from app.services.xml_builder import schedule_a_broker_update_values
from app.services.schedule_a_organization_source import reconcile_source_name_tokens


def extract_provider_name_suffix(monkeypatch, *, suffix='LLC', source_code=None,
                                 provider_code=None, bound=True, provider_overrides=None, empty_local=False):
    name = 'Different Advisory (NY) ' + suffix
    clean = f'''INSURANCE DATA FOR SCHEDULE A (FORM 5500)
1. NAME OF CARRIER, SERVICE OR OTHER ORGANIZATION:
First Unum Life Insurance Company
TAX ID: 123456789 NAIC: 12345
2. CONTRACT NUMBER: 009876
3. APPROXIMATE NUMBER OF PERSONS COVERED AT END OF POLICY YEAR: 57
4. DATE FOR PERIOD: FROM 01-01-2025 TO 01-01-2026
5. INSURANCE FEES AND COMMISSION INFORMATION:
NAME AND ADDRESS OF SALES ADDITIONAL
EACH SOLICITING AGENT COMMISSION FEES COMPENSATION
OR BROKER RECEIVING PAID PAID PAID
COMPENSATION:
{name} 1,234.56 .00 .00
PO Box 2100
Mineola NY 11501
6. COVERAGE/BENEFITS PROVIDED: DBL
7. NON-PARTICIPATING CONTRACTS (PREMIUMS):
(A) TOTAL PREMIUM OR SUBSCRIPTION CHARGES PAID TO CARRIER $12,345.67
(B) PREMIUMS DUE AND UNPAID AT END OF THE PLAN YEAR $100.25
(C) IF THE CARRIER INCURRED SPECIFIC COSTS IN CONNECTION WITH THE CONTRACT
REPORT AMOUNT .00
'''
    if source_code is not None:
        clean = clean.replace('6. COVERAGE', f'Organization Code: {source_code}\n6. COVERAGE')
    # Reproduce the scanned document's local OCR failure without broadening
    # the parser to guess that an unreadable -00 additional amount means zero.
    local = clean.replace('1,234.56 .00 .00', '1,234.56 .00 -00').replace(
        '\nMineola NY 11501', '').replace('5. INSURANCE FEES', 'Mineola NY 11501\n5. INSURANCE FEES')
    data, filename = local.encode(), 'different-unum.txt'
    if empty_local:
        data = b''
    document = {'documentId': 'qa-document', 'bucketId': 123, 'fileName': filename, 'status': 'complete',
                'sourceUrl': 'https://qa.invalid/current-document'}
    receipt = {'ingest': {'processId': 'qa-process', 'status': 'complete',
                         'progress': {'complete': {'documents': [document]}}}}
    structured = {'broker_rows': [{'name': name, 'address_line_1': 'PO Box 2100',
        'city': 'Mineola', 'state': 'NY', 'zip_code': '11501',
        'commission_total': '1234.56', 'fee_total': '0',
        'organization_code': provider_code if provider_code is not None else suffix}]}
    structured['broker_rows'][0].update(provider_overrides or {})
    xray = {'fileName': filename, 'sourceUrl': document['sourceUrl'] if bound else 'https://qa.invalid/other-document',
            'chunks': [{'pageNumbers': [1], 'text': clean}]}

    class Provider:
        def __init__(self, **kwargs):
            pass

        def ingest(self, **kwargs):
            assert Path(kwargs['documents'][0].file_path).read_bytes() == data
            return SimpleNamespace(model_dump=lambda **kwargs: copy.deepcopy(receipt))

    def respond(request):
        path = request.url.path
        if '/document/extract/' in path:
            return httpx.Response(200, json=structured)
        if '/document/xray/' in path:
            return httpx.Response(200, json=xray)
        if '/ingest/documents/' in path:
            return httpx.Response(200, json={'documents': [document]})
        if path.endswith('/ingest/qa-process'):
            return httpx.Response(200, json=receipt)
        raise AssertionError('Unexpected provider request: ' + path)

    client = httpx.AsyncClient
    monkeypatch.setattr(extractor.httpx, 'AsyncClient', lambda *a, **kw:
                        client(*a, **{**kw, 'transport': httpx.MockTransport(respond)}))
    monkeypatch.setattr(extractor, 'GroundX', Provider)
    settings = Settings(_env_file=None, groundx_api_key='test-placeholder', groundx_bucket_id=123,
        eyelevel_api_key=None, eyelevel_extract_url=None, mongodb_uri=None, ftwlink_key_id=None,
        s3_bucket_name=None, schedule_a_canonical_validation_enabled=True,
        schedule_a_canonical_validation_shadow_enabled=False)
    monkeypatch.setattr(extractor, 'get_settings', lambda: settings)
    return asyncio.run(extractor.ExtractionService(DEFAULT_FIELD_RULES).extract_schedule_a(data, filename))


@pytest.mark.parametrize('suffix', ['LLC', 'LL', 'Inc'])
def test_current_document_name_suffix_is_not_a_printed_recipient_code(monkeypatch, suffix):
    result = extract_provider_name_suffix(monkeypatch, suffix=suffix)
    row = result.schedule_a_broker_rows[0]
    assert row.organization_code == '3' and row.organization_code_defaulted
    assert row.decision == 'AUTOMATIC'
    field = next(f for f in result.fields if f.field_name.startswith('3e.'))
    assert field.value == '3' and field.decision == 'AUTOMATIC'
    mapped = map_extraction_to_rules('qa', result.fields, rules=DEFAULT_FIELD_RULES,
        form_type=FormType.SCHEDULE_A, source_document_type=DocumentType.SCHEDULE_A)
    code = next(f for f in mapped['fields'] if f.source_field_name.startswith('3e.'))
    assert code.proposed_value == '3'
    assert schedule_a_broker_update_values(result.schedule_a_broker_rows)['Code1'] == '3'
    assert result.raw['structured_outputs'][0]['broker_rows'][0]['organization_code'] == suffix
    assert result.raw['source_organization_code_reconciliations'][0]['rejected_provider_candidate'] == suffix


@pytest.mark.parametrize('code', ['LLC', 'ABC', '3', '6', '03'])
def test_explicit_source_code_is_not_erased_when_it_matches_or_conflicts_with_a_name_token(monkeypatch, code):
    result = extract_provider_name_suffix(monkeypatch, source_code=code, provider_code=code)
    row = result.schedule_a_broker_rows[0]
    assert row.organization_code == code and not row.organization_code_defaulted
    assert not result.raw.get('source_organization_code_reconciliations')
    if code.isalpha():
        assert row.decision == 'REVIEW_REQUIRED'
        assert any(v.validator == 'source_organization_code_shape' and v.status == 'ERROR'
                   for v in row.validation_results)


def test_other_documents_source_text_cannot_clear_the_unsupported_code(monkeypatch):
    result = extract_provider_name_suffix(monkeypatch, bound=False)
    row = result.schedule_a_broker_rows[0]
    assert row.organization_code == 'LLC' and row.decision == 'REVIEW_REQUIRED'
    assert not result.raw.get('source_organization_code_reconciliations')


def test_unbound_semantic_fallback_cannot_supply_the_no_code_source_proof(monkeypatch):
    result = extract_provider_name_suffix(monkeypatch, bound=False, empty_local=True)
    # Other existing adapters may propose their own values; this recovery must
    # not claim unbound fallback text as proof for rejecting a source code.
    assert not result.raw.get('source_organization_code_reconciliations')


@pytest.mark.parametrize('overrides', [
    {'address_line_1': 'PO Box 9999'},
    {'commission_total': '1234.57'},
    {'name': 'Another Advisory (NY) LLC'},
    {'zip_code': '11502'},
])
def test_name_token_correction_requires_exact_recipient_and_payment_evidence(monkeypatch, overrides):
    result = extract_provider_name_suffix(monkeypatch, provider_overrides=overrides)
    assert not result.raw.get('source_organization_code_reconciliations')
    assert any(row.organization_code == 'LLC' and row.decision == 'REVIEW_REQUIRED'
               for row in result.schedule_a_broker_rows)


@pytest.mark.parametrize('provider_code,expected_defaulted', [('LLC', True), ('3', False), ('ABC', False), (None, False)])
def test_merged_default_provenance_requires_the_original_provider_name_token(provider_code, expected_defaulted):
    pages = [(1, '''INSURANCE DATA FOR SCHEDULE A (FORM 5500)
First Unum Life Insurance Company
NAME AND ADDRESS OF SALES ADDITIONAL
EACH SOLICITING AGENT COMMISSION FEES COMPENSATION
OR BROKER RECEIVING PAID PAID PAID
Different Advisory LLC 125.25 .00 .00
PO Box 2100
Mineola NY 11501
''')]
    provider = ScheduleABrokerRow(name='Different Advisory LLC', address_line_1='PO Box 2100',
        city='Mineola', state='NY', zip_code='11501', commission_total='125.25', fee_total='0',
        organization_code=provider_code, confidence=.99)
    # A later adapter has the right scalar code but lost its blank-source marker.
    current = provider.model_copy(deep=True, update={'organization_code': '3'})
    result = NormalizedExtractionResult(provider='GroundX structured extract', fields=[],
        schedule_a_broker_rows=[current], raw={'extraction_stages': [{
            'stage': 'structured_provider_normalized', 'broker_rows': [provider.model_dump(mode='json')]}]})
    reconcile_source_name_tokens(result, extractor.extract_first_unum_broker_rows(pages), pages)
    assert result.schedule_a_broker_rows[0].organization_code_defaulted is expected_defaulted
    if expected_defaulted:
        assert result.raw['source_organization_code_reconciliations'][0]['rejected_provider_candidate'] == 'LLC'
    else:
        assert not result.raw.get('source_organization_code_reconciliations')
