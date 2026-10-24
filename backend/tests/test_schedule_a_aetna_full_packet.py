"""End-to-end extraction checks with changed source values, no provider calls."""
import asyncio
from io import BytesIO
import pytest

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from app.config import get_settings
from app.services.extractor import ExtractionService


def _statement(lines):
    stream=BytesIO()
    pdf=PdfWriter()
    page=pdf.add_blank_page(width=850,height=850)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Courier')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):pdf._add_object(font)})})
    commands=['BT /F1 8 Tf 30 800 Td 10 TL']
    for line in lines.splitlines():
        literal=line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
        commands.append(f'({literal}) Tj T*')
    commands.append('ET')
    content=DecodedStreamObject()
    content.set_data('\n'.join(commands).encode('ascii'))
    page[NameObject('/Contents')]=pdf._add_object(content)
    pdf.write(stream)
    return stream.getvalue()


def test_complete_contract_suffix_survives_the_public_extraction_pipeline(monkeypatch):
    for key in ('GROUNDX_API_KEY','EYELEVEL_API_KEY','EYELEVEL_EXTRACT_URL'):
        monkeypatch.setenv(key,'')
    monkeypatch.setenv('GROUNDX_BUCKET_ID','0')
    monkeypatch.setenv('SCHEDULE_A_CANONICAL_VALIDATION_ENABLED','true')
    monkeypatch.setenv('SCHEDULE_A_CANONICAL_VALIDATION_SHADOW_ENABLED','false')
    get_settings.cache_clear()
    document=_statement('''AETNA LIFE INSURANCE COMPANY AND AFFILIATES
INSURANCE INFORMATION
For Fiscal Plan Year beginning 01/01/2024 and ending 12/31/2024
C. Name of the Plan Sponsor: DIFFERENT EXAMPLE SPONSOR
PART I Information Concerning Insurance Contract Coverage, Fees, and Commissions.
(a) Name of Insurance Carrier: (d) Contract Number (e) Approximate Number of
Aetna Life Insurance Co. or Identification: persons covered at the end Policy or contract Year
(b) EIN: 06-6033492 0042000 - Vision of policy or contract year: (f) From: (g) To:
(c) NAIC Code: See Attached Listing 17 01/01/2024 12/31/2024
2. Insurance Fees and commissions paid to agents and brokers:
Contract or (a) Name and address of the agents or brokers (b) Amount of (c) & (d) Fees Paid
Identification to whom commissions or fees were paid. commissions paid
Amount Purpose
0042000 - Vision DIFFERENT BENEFITS LLC 42 $ 125.25
RESEARCH ROAD BOSTON, MA 02110
TOTAL $ 125.25
Part III Welfare Benefit Contract Information
9. Non experience rated contracts:
(a) Total premiums or subscription charges paid to carrier $ 1250.50
''')
    try:
        result=asyncio.run(ExtractionService().extract_schedule_a(document,'different-policy.pdf'))
        contracts=[f.value for f in result.fields if f.field_name.startswith('1d.')]
        assert contracts==['0042000-Vision']
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize('carrier_suffix', ['.', ',', ''])
def test_labelled_aetna_carrier_handles_ocr_punctuation_without_reading_adjacent_instructions(carrier_suffix):
    from app.services.extractor import extract_litera_aetna_schedule_a_fields
    pages=[(3,f'''AETNA LIFE INSURANCE COMPANY completing Schedule A of Form 5500.
AND AFFILIATES
For Fiscal Plan Year beginning 01/01/2024 and ending 12/31/2024
PART I Information Concerning Insurance Contract Coverage, Fees, and Commissions.
(a) Name of Insurance Carrier: (d) Contract Number | (e) Approximate Number of
Aetna Life Insurance Co{carrier_suffix} or Identification: persons covered at the end Policy or contract Year
(b) EIN: 06-6033492 0042000-Vision of policy or contract year: (f) From: (g) To:
(c) NAIC Code: See Attached Listing 17 01/01/2024 12/31/2024
2. Insurance Fees and commissions paid to agents and brokers:
Part III Welfare Benefit Contract Information
''')]
    fields={f.field_name.split('.')[0]:f for f in extract_litera_aetna_schedule_a_fields(pages)}
    assert fields['1a'].value=='Aetna Life Insurance Co.'
    assert fields['1d'].value=='0042000-Vision'
    assert fields['1e'].value=='17'


def test_instructions_cannot_be_accepted_as_a_carrier_when_no_other_candidate_is_available():
    from app.models import NormalizedExtractionField, NormalizedExtractionResult
    from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result
    value='completing Schedule A of Form 5500.'
    result=resolve_schedule_a_result(NormalizedExtractionResult(provider='test',fields=[
        NormalizedExtractionField(field_name='1a. Name of Insurance Company',value=value,
            confidence=.99,page=3,source_text=value)]))
    field=next(f for f in result.fields if f.field_name.startswith('1a.'))
    assert field.decision=='REVIEW_REQUIRED'
    assert any(v.validator=='carrier_name' and v.status=='ERROR' for v in field.validation_results)


def test_aetna_carrier_repair_survives_the_public_pipeline_with_a_wrong_local_candidate(monkeypatch):
    from pypdf import PdfReader
    from app.models import NormalizedExtractionField, NormalizedExtractionResult
    from app.services import extractor as module
    for key in ('GROUNDX_API_KEY','EYELEVEL_API_KEY','EYELEVEL_EXTRACT_URL'):
        monkeypatch.setenv(key,'')
    monkeypatch.setenv('GROUNDX_BUCKET_ID','0')
    monkeypatch.setenv('SCHEDULE_A_CANONICAL_VALIDATION_ENABLED','true')
    monkeypatch.setenv('SCHEDULE_A_CANONICAL_VALIDATION_SHADOW_ENABLED','false')
    get_settings.cache_clear()
    source='''AETNA LIFE INSURANCE COMPANY completing Schedule A of Form 5500.
AND AFFILIATES
For Fiscal Plan Year beginning 01/01/2024 and ending 12/31/2024
(a) Name of Insurance Carrier: (d) Contract Number | (e) Approximate Number of
Aetna Life Insurance Co, or Identification: persons covered at the end Policy or contract Year
(b) EIN: 06-6033492 0042000-Vision of policy or contract year: (f) From: (g) To:
(c) NAIC Code: See Attached Listing 17 01/01/2024 12/31/2024
2. Insurance Fees and commissions paid to agents and brokers:
Part III Welfare Benefit Contract Information
'''
    document=PdfWriter()
    document.add_blank_page(width=850,height=850)
    document.add_blank_page(width=850,height=850)
    document.add_page(PdfReader(BytesIO(_statement(source))).pages[0])
    stream=BytesIO();document.write(stream)
    async def recorded_boundary(self,*args):
        return NormalizedExtractionResult(provider='Recorded provider/local boundary',fields=[
            NormalizedExtractionField(field_name='1a. Name of Insurance Company',
                value='completing Schedule A of Form 5500.',confidence=.99,page=3,source_text=source,
                candidate_values=['Aetna Life Insurance Co.','completing Schedule A of Form 5500.'])],
            raw={'local_ocr_pages':[{'page':3,'text':source}]})
    monkeypatch.setattr(module.ExtractionService,'_extract_schedule_a_unresolved',recorded_boundary)
    try:
        result=asyncio.run(ExtractionService().extract_schedule_a(stream.getvalue(),'different-ocr-carrier.pdf'))
        field=next(f for f in result.fields if f.field_name.startswith('1a.'))
        assert field.value=='Aetna Life Insurance Co.'
        assert not any(v.validator=='candidate_conflict' and v.status=='ERROR' for v in field.validation_results)
    finally:
        get_settings.cache_clear()


def test_wrapped_aetna_rows_keep_separate_commission_and_fee_columns():
    from app.services.extractor import extract_litera_aetna_schedule_a_broker_rows
    pages=[(3,'''AETNA LIFE INSURANCE COMPANY AND AFFILIATES
For Fiscal Plan Year beginning 01/01/2024 and ending 12/31/2024
2. Insurance Fees and commissions paid to agents and brokers:
(a) Name and address of the agents or brokers (b) Amount of commissions paid (c) & (d) Fees Paid
Amount Purpose
0042000 - Vision DIFFERENT BENEFITS LLC 42 $ 125.25
RESEARCH ROAD BOSTON , MA 02110
0042000 - Vision DIFFERENT BENEFITS LLC 42 $ 25.50 Annual Service
RESEARCH ROAD BOSTON , MA 02110 ++ Payment
TOTAL $ 125.25 $ 25.50
Part III Welfare Benefit Contract Information
''')]
    rows=extract_litera_aetna_schedule_a_broker_rows(pages)
    assert len(rows)==1
    assert rows[0].name=='DIFFERENT BENEFITS LLC'
    assert rows[0].address_line_1=='42 RESEARCH ROAD'
    assert rows[0].city=='BOSTON'
    assert rows[0].commission_total=='125.25'
    assert rows[0].fee_total=='25.50'
    assert rows[0].fee_rows[0].purpose=='Annual Service Payment'


def test_aetna_reconciler_counts_payment_components_not_the_total_or_merged_recipients():
    from app.models import NormalizedExtractionResult, ScheduleABrokerRow
    from app.services.extractor import reconcile_aetna_part_i_compensation
    source = '''AETNA LIFE INSURANCE COMPANY AND AFFILIATES
For Fiscal Plan Year beginning 01/01/2024 and ending 12/31/2024
2. Insurance Fees and commissions paid to agents and brokers:
Contract or (a) Name and address of the agents or brokers (b) Amount of (c) & (d) Fees Paid
Identification to whom commissions or fees were paid . commissions paid
    0042000 - Vision DIFFERENT BENEFITS LLC 42 $ 1,125.25
RESEARCH ROAD BOSTON , MA 02110
    0042000 - Vision DIFFERENT BENEFITS LLC 42 $ 25.50 Annual Service
RESEARCH ROAD BOSTON , MA 02110 ++ Payment
TOTAL $ 1,125.25 $ 25.50
Part III Welfare Benefit Contract Information
'''
    result = NormalizedExtractionResult(provider='External', fields=[], schedule_a_broker_rows=[
        ScheduleABrokerRow(name='DIFFERENT BENEFITS LLC', address_line_1='RESEARCH ROAD', organization_code='42')])
    reconcile_aetna_part_i_compensation(result, [(3, source)])
    assert result.schedule_a_broker_rows[0].address_line_1 == '42 RESEARCH ROAD'
    assert result.raw['aetna_compensation_reconciliation']['complete_paid_rows'] == 2


@pytest.mark.parametrize('carrier', ['aetna', 'hartford'])
def test_incomplete_carrier_table_cannot_erase_external_provider_recipient(monkeypatch, carrier):
    """Only the provider SDK/HTTP boundary is replaced, not our pipeline."""
    import httpx
    from types import SimpleNamespace
    import app.services.extractor as module
    monkeypatch.setenv('GROUNDX_API_KEY', 'test-no-network')
    monkeypatch.setenv('GROUNDX_BUCKET_ID', '1')
    monkeypatch.setenv('GROUNDX_MAX_WAIT_SECONDS', '1')
    monkeypatch.setenv('GROUNDX_POLL_SECONDS', '1')
    monkeypatch.setenv('SCHEDULE_A_CANONICAL_VALIDATION_ENABLED', 'true')
    monkeypatch.setenv('SCHEDULE_A_CANONICAL_VALIDATION_SHADOW_ENABLED', 'false')
    get_settings.cache_clear()
    process = {'ingest': {'processId': 'test-process', 'status': 'complete',
               'progress': {'complete': {'documents': [{'documentId': 'test-doc', 'fileName': 'partial.pdf', 'extracted': True}]}}}}
    class Provider:
        def __init__(self, **kwargs): pass
        def ingest(self, **kwargs):
            return SimpleNamespace(model_dump=lambda **kw: process)
    payload = {
        'schedule_a_policy': [{'schedule_a_part_i_1a_name_of_insurance_company': 'Aetna Life Insurance Co.',
                              'schedule_a_part_i_1b_insurance_carrier_ein': '06-6033492',
                              'schedule_a_part_i_1d_contract_policy_number': '0042000-Vision'}],
        'broker_rows': [{'name': 'DIFFERENT BENEFITS LLC', 'address_line_1': '42 RESEARCH ROAD',
                         'city': 'BOSTON', 'state': 'MA', 'zip_code': '02110', 'commission_total': '125.25'}],
    }
    def respond(request):
        if '/ingest/documents/' in request.url.path:
            return httpx.Response(200, json={'documents': [dict(process['ingest']['progress']['complete']['documents'][0], status='complete')]})
        if '/ingest/document/extract/' in request.url.path:
            return httpx.Response(200, json=payload)
        if '/ingest/document/xray/' in request.url.path:
            return httpx.Response(200, json={'chunks': [], 'documentPages': []})
        if '/ingest/' in request.url.path:
            return httpx.Response(200, json=process)
        raise AssertionError('Unexpected provider request: '+request.url.path)
    original_client = httpx.AsyncClient
    def client(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(respond)
        return original_client(*args, **kwargs)
    monkeypatch.setattr(module, 'GroundX', Provider)
    monkeypatch.setattr(module.httpx, 'AsyncClient', client)
    source = '''AETNA LIFE INSURANCE COMPANY AND AFFILIATES
For Fiscal Plan Year beginning 01/01/2024 and ending 12/31/2024
Name of Insurance Carrier: Aetna Life Insurance Co.
(b) EIN: 06-6033492 0042000 - Vision of policy or contract year:
2. Insurance Fees and commissions paid to agents and brokers:
(a) Name and address of the agents or brokers
(b) Amount of commissions paid (c) & (d) Fees Paid
0042000 - Vision unreadable OCR row $125.25
TOTAL $125.25
Part III Welfare Benefit Contract Information
'''
    if carrier == 'hartford':
        payload['schedule_a_policy'][0]['schedule_a_part_i_1a_name_of_insurance_company'] = 'HARTFORD LIFE AND ACCIDENT'
        source = '''The Hartford Annual Statement of Premiums and Producer Compensation
Name of Insurance Carrier EIN NAIC Code Policy Number
HARTFORD LIFE AND ACCIDENT 06-0838648 70815 0042000G
Plan/Policy Year - 01/01/2024 to 12/31/2024
Premium was applied as follows during the Plan/Policy Year -
0042000G LIFE-BTRM $ 1250.50 17
Total $ 1250.50
Producer and Address unreadable row
'''
    document = _statement(source)
    try:
        result = asyncio.run(ExtractionService().extract_schedule_a(document, 'partial.pdf'))
        assert any(row.name == 'DIFFERENT BENEFITS LLC' and row.commission_total == '125.25'
                   for row in result.schedule_a_broker_rows)
        if carrier == 'hartford':
            recipient = next(row for row in result.schedule_a_broker_rows if row.name == 'DIFFERENT BENEFITS LLC')
            assert recipient.decision == 'REVIEW_REQUIRED'
            assert any(value.validator == 'incomplete_source_compensation_table' for value in recipient.validation_results)
    finally:
        get_settings.cache_clear()
