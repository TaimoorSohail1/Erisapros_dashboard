"""Recipient/payment reconciliation across image-only report pages."""
import asyncio
import hashlib
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import httpx
import pytest
from pypdf import PdfWriter

from app.config import get_settings
from app.services.extractor import ExtractionService


@pytest.mark.parametrize("scenario", ["complete", "roman_ocr_heading", "one_unreadable_header", "wrong_source", "missing_page", "conflicting_reads", "conflicting_header", "unreadable_but_conflicting_header", "unassigned_payment", "unassigned_bare_payment"])
def test_independent_scan_roster_uses_complete_source_bound_labeled_ocr(scenario):
    from app.services.extractor import extract_schedule_a_broker_rows_from_document

    pdf = PdfWriter()
    pdf.add_blank_page(width=612, height=792)
    pdf.add_blank_page(width=612, height=792)
    stream = BytesIO()
    pdf.write(stream)
    content = stream.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    packet = {
        1: "THE LINCOLN NATIONAL LIFE INSURANCE COMPANY\nSCHEDULE A REPORTING INFORMATION\n"
           "This report covers the preceding policy year and provides the information used by "
           "the plan administrator to complete the annual filing. Please keep these pages with "
           "the plan records and contact the carrier for questions regarding policy compensation.",
        2: """Part I - Information Concerning Insurance Contract Coverage, Fees, and Commissions
2. Insurance fee and commission information.
(a) Total amount of commissions paid (b) Total amount of fees paid
$ 125.25 $ 0.00
3. Insurance fees and commissions paid to agents, brokers, and other persons:
Name and address to whom Amount of sales and Fees and other Org.
payments were paid (a) base commissions (b) Amount (c) Purpose (d) Code (e)
SYNTHETIC BENEFITS LLC $ 125.25 6
77 INVENTED CIR
BOSTON, MA 02110
Totals: $ 125.25 $ 0.00
Part III - Welfare Benefit Contract Information
10. Nonexperience-rated contracts: Total premiums paid $1,234.56
""",
    }
    reads = [
        {"page": page, "text": text, "psm": mode, "source_sha256": digest}
        for mode in (4, 6) for page, text in packet.items()
    ]
    if scenario == "wrong_source":
        for row in reads:
            row["source_sha256"] = "0" * 64
    elif scenario == "missing_page":
        reads = [row for row in reads if row["page"] == 2]
    elif scenario == "conflicting_reads":
        reads[-1]["text"] = reads[-1]["text"].replace("INVENTED CIR", "DIFFERENT RD")
    elif scenario in {"unassigned_payment","unassigned_bare_payment"}:
        for read in reads:
            read['text']=read['text'].replace('Totals:', ('Unassigned fee $25.00' if scenario=='unassigned_payment' else 'Unassigned fee 25.00')+'\nTotals:')
    elif scenario == "roman_ocr_heading":
        for read in reads:
            read['text']=read['text'].replace('Part III','Part Ill')
    elif scenario in {'one_unreadable_header','conflicting_header','unreadable_but_conflicting_header'}:
        reads[-1]['text']=reads[-1]['text'].replace('$ 125.25 $ 0.00',
            'S125.25 unreadable' if scenario=='one_unreadable_header' else 'S999.00 unreadable' if scenario=='unreadable_but_conflicting_header' else '$ 999.00 $ 0.00',1)
    coverage = {}

    rows = extract_schedule_a_broker_rows_from_document(
        content, "image-only.pdf", page_coverage=coverage, retained_ocr_source_reads=reads,
    )

    if scenario not in {"complete","roman_ocr_heading","one_unreadable_header"}:
        assert not rows
        assert not any(page["readable"] for page in coverage["pages"])
        return
    assert coverage["source_sha256"] == digest
    assert [page["page"] for page in coverage["pages"] if page["readable"]] == [1, 2]
    assert len(rows) == 1
    assert rows[0].source_page == 2
    assert rows[0].commission_total == "125.25"
    assert rows[0].fee_total == "0"
    assert any(v.validator=='native_printed_broker_section_totals' and v.status=='PASS' for v in rows[0].validation_results)
    from app.models import NormalizedExtractionResult,NormalizedExtractionField
    from app.services.schedule_a_broker_source_repair import repair_source_rows
    result=NormalizedExtractionResult(provider='test',fields=[NormalizedExtractionField(field_name='3c. Amount of Fees',value='125.25')],
        schedule_a_broker_rows=rows,raw={'extraction_stages':[{'stage':'structured_provider_normalized',
        'broker_rows':[r.model_dump(mode='json') for r in rows]}],'independent_source_page_coverage':coverage})
    repair_source_rows(result,rows)
    assert next(f.value for f in result.fields if f.field_name.startswith('3c.'))=='0'


def _extract_packet(monkeypatch, table_state, broker_name):
    import app.services.extractor as module

    monkeypatch.setenv('GROUNDX_API_KEY', 'test-no-network')
    monkeypatch.setenv('GROUNDX_BUCKET_ID', '1')
    monkeypatch.setenv('GROUNDX_MAX_WAIT_SECONDS', '1')
    monkeypatch.setenv('GROUNDX_POLL_SECONDS', '1')
    monkeypatch.setenv('SCHEDULE_A_CANONICAL_VALIDATION_ENABLED', 'true')
    monkeypatch.setenv('SCHEDULE_A_CANONICAL_VALIDATION_SHADOW_ENABLED', 'false')
    get_settings.cache_clear()
    process = {'ingest': {'processId': 'test-process', 'status': 'complete',
        'progress': {'complete': {'documents': [{'documentId': 'test-doc', 'fileName': 'changed.pdf', 'extracted': True}]}}}}

    class Provider:
        def __init__(self, **kwargs): pass
        def ingest(self, **kwargs):
            return SimpleNamespace(model_dump=lambda **kw: process)

    payload = {'schedule_a_policy': [{
        'schedule_a_part_i_1a_name_of_insurance_company': 'The Lincoln National Life Insurance Company',
        'schedule_a_part_i_1b_insurance_carrier_ein': '35-0472300',
        'schedule_a_part_i_1c_naic_code': '65676',
        'schedule_a_part_i_1d_contract_policy_number': '00984512 00000',
        'schedule_a_part_i_1e_persons_covered_end_of_policy_year': 53,
        'schedule_a_part_i_1f_policy_year_beginning_date': '01/01/2024',
        'schedule_a_part_i_1g_policy_year_ending_date': '12/31/2024',
    }], 'broker_rows': [
        {'name': broker_name, 'address_line_1': '77 INVENTED CIR', 'city': 'BOSTON',
         'state': 'MA', 'zip_code': '02110', 'organization_code': '6',
         'commission_total': '125.25', 'purpose': 'Annual Service Payment'},
        {'name': broker_name, 'address_line_1': '77 INVENTED CIRCLE', 'city': 'BOSTON',
         'state': 'MA', 'zip_code': '02110', 'organization_code': '6',
         'fee_total': '25.50', 'purpose': 'Annual Service Payment'},
    ]}
    pages = [(1, '''Cover letter
THE LINCOLN NATIONAL LIFE INSURANCE COMPANY
SCHEDULE A REPORTING INFORMATION
Name of Plan: SYNTHETIC SPONSOR
'''), (2, '''Part I - Information Concerning Insurance Contract Coverage, Fees, and Commissions
1. Coverage:
(a) Name of insurance carrier : The Lincoln National Life Insurance Company
(b) EIN : 35-0472300
(c) NAIC code : 65676
(d) Contract or identification number : 00984512 00000
Benefits & Contract Type Number of Persons Reporting Period From To
AD & D 53 01/01/2024 12/31/2024
Life 53 01/01/2024 12/31/2024
2. Insurance fee and commission information.
(a) Total amount of commissions paid (b) Total amount of fees paid
$ 125.25 $ 25.50
3. Insurance fees and commissions paid to agents, brokers, and other persons:
Name and address to whom Amount of sales and Fees and other Org.
payments were paid (a) base commissions (b) Amount (c) Purpose (d) Code (e)
SYNTHETIC BENEFITS LLC $ 125.25 6
77 INVENTED CIR
BOSTON , MA 02110
Totals : $ 125.25 $ 0.00
SYNTHETIC BENEFITS LLC $ 25.50 Annual Service Payment 6
77 INVENTED CIRCLE
BOSTON , MA 02110
Totals : $ 0.00 $ 25.50
Part III - Welfare Benefit Contract Information
10. Nonexperience - rated contracts:
(a) Total premiums or subscription charges paid to carrier ... $ 1250.50
''')]
    if table_state=='unreadable_fee':
        pages[1]=(2,pages[1][1].replace('SYNTHETIC BENEFITS LLC $ 25.50 Annual Service Payment 6',
                                      'Unreadable compensation row'))
    elif table_state=='conflicting_total':
        pages[1]=(2,pages[1][1].replace('LLC $ 25.50','LLC $ 15.50').replace('Totals : $ 0.00 $ 25.50','Totals : $ 0.00 $ 15.50'))
    pages=[(page,text.replace('SYNTHETIC BENEFITS LLC',broker_name)) for page,text in pages]
    xray = {'chunks': [{'pageNumbers': [page], 'text': text} for page, text in pages]}

    def respond(request):
        if '/ingest/documents/' in request.url.path:
            return httpx.Response(200, json={'documents': process['ingest']['progress']['complete']['documents']})
        if '/ingest/document/extract/' in request.url.path:
            return httpx.Response(200, json=payload)
        if '/ingest/document/xray/' in request.url.path:
            return httpx.Response(200, json=xray)
        if '/ingest/' in request.url.path:
            return httpx.Response(200, json=process)
        raise AssertionError('Unexpected provider request: '+request.url.path)

    original_client = httpx.AsyncClient
    def client(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(respond)
        return original_client(*args, **kwargs)
    monkeypatch.setattr(module, 'GroundX', Provider)
    monkeypatch.setattr(module.httpx, 'AsyncClient', client)
    pdf = PdfWriter()
    pdf.add_blank_page(width=612, height=792)
    pdf.add_blank_page(width=612, height=792)
    stream = BytesIO()
    pdf.write(stream)
    try:
        result = asyncio.run(ExtractionService().extract_schedule_a(stream.getvalue(), 'changed.pdf'))
        rows = result.schedule_a_broker_rows
        assert len(rows) == 2, (result.provider, [(s.get('stage'),len(s.get('broker_rows',[]))) for s in result.raw.get('extraction_stages',[])])
        assert all(row.name==broker_name for row in rows)
        commission = next(row for row in rows if row.address_line_1 == '77 INVENTED CIR')
        fee = next(row for row in rows if row.address_line_1 == '77 INVENTED CIRCLE')
        assert commission.purpose in (None, 'Commission'), 'Never copy another recipient\'s printed fee purpose.'
        if commission.purpose == 'Commission':
            from app.services.schedule_a_customer_rules import broker_value_was_defaulted
            assert broker_value_was_defaulted(commission, 'purpose'), 'A derived purpose must retain its default provenance.'
        assert Decimal(commission.commission_total) == Decimal('125.25')
        assert Decimal(fee.fee_total) == Decimal('15.50' if table_state=='conflicting_total' else '25.50')
        assert fee.purpose == 'Annual Service Payment'
        assert all(row.organization_code == '6' for row in rows)
        assert all(row.decision == 'REVIEW_REQUIRED' for row in rows), 'Do not silently merge possible street aliases.'
        if table_state!='complete':
            assert any(v.validator == 'incomplete_source_compensation_table' for v in fee.validation_results)
        return result
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize('table_state', ['complete', 'unreadable_fee', 'conflicting_total'])
@pytest.mark.parametrize('broker_name', ['SYNTHETIC BENEFITS LLC','Jordan A. Smith'])
def test_split_report_header_and_spaced_currency_keep_purpose_on_its_own_recipient(monkeypatch, table_state, broker_name):
    _extract_packet(monkeypatch,table_state,broker_name)


def test_local_ocr_name_conflict_cannot_replace_a_provider_recipient_automatically(monkeypatch):
    original_which=shutil.which
    monkeypatch.setattr(shutil,'which',lambda name: name if name in {'pdftoppm','tesseract'} else original_which(name))
    local_pages={1:'''THE LINCOLN NATIONAL LIFE INSURANCE COMPANY
SCHEDULE A REPORTING INFORMATION
Cover letter for SYNTHETIC SPONSOR
''',2:'''THE LINCOLN NATIONAL LIFE INSURANCE COMPANY
SCHEDULE A REPORTING INFORMATION
Part I - Information Concerning Insurance Contract Coverage, Fees, and Commissions
1. Coverage:
(a) Name of insurance carrier: The Lincoln National Life Insurance Company
(b) EIN: 35-0472300
(c) NAIC code: 65676
(d) Contract or identification number: 00984512 00000
Benefits & Contract Type Number of Persons Reporting Period From To
AD & D 53 01/01/2024 12/31/2024
Life 53 01/01/2024 12/31/2024
2. Insurance fee and commission information.
(a) Total amount of commissions paid (b) Total amount of fees paid
$ 125.25 $ 25.50
3. Insurance fees and commissions paid to agents, brokers, and other persons:
Name and address to whom Amount of sales and Fees and other Org.
payments were paid (a) base commissions (b) Amount (c) Purpose (d) Code (e)
SYNTHETIC BENEFTTS LLC $ 125.25 6
77 INVENTED CIR
BOSTON, MA 02110
Totals: $ 125.25 $ 0.00
SYNTHETIC BENEFITS LLC $ 25.50 Annual Service Payment 6
77 INVENTED CIRCLE
BOSTON, MA 02110
Totals: $ 0.00 $ 25.50
Part III - Welfare Benefit Contract Information
10. Nonexperience-rated contracts:
(a) Total premiums or subscription charges paid to carrier ... $ 1250.50
'''}
    def run(command,**kwargs):
        if command[0]=='pdftoppm':
            if '-jpeg' in command:
                for page in (1,2):Path(command[-1]+f'-{page}.jpg').write_bytes(b'ocr-boundary-fixture')
            else:
                from PIL import Image
                Image.new('RGB',(100,100),'white').save(command[-1]+'.png')
            return subprocess.CompletedProcess(command,0,stdout='')
        assert command[0]=='tesseract'
        if command[-1]=='tsv':
            text='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
        else:
            page=int(Path(command[1]).stem.split('-')[-1])
            text=local_pages[page]
        return subprocess.CompletedProcess(command,0,stdout=text)
    monkeypatch.setattr(subprocess,'run',run)
    result=_extract_packet(monkeypatch,'complete','SYNTHETIC BENEFITS LLC')
    row=next(r for r in result.schedule_a_broker_rows if r.address_line_1=='77 INVENTED CIR')
    assert any(v.validator=='ocr_recipient_identity_conflict' for v in row.validation_results)
    assert result.raw['local_ocr_pages'][1]['text'].find('BENEFTTS')>=0
