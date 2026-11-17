import pytest
from app.models import NormalizedExtractionResult, ScheduleABrokerRow
import app.services.extractor as extractor
from app.services.schedule_a_source_observations import collect_schedule_a_source_observations

SOURCE='''THE LINCOLN NATIONAL LIFE INSURANCE COMPANY
SCHEDULE A REPORTING INFORMATION
(a) Name of insurance carrier: The Lincoln National Life Insurance Company
(b) EIN: 35-1234567
(c) NAIC code: 12345
(d) Contract or identification number: 000012345678 00000
Benefits & Contract Type
(Part III,#8) (e) (f) (g)
Weekly Income 17 01/01/2025 12/31/2025
2. Insurance fee and commission information.
(a) Total amount of commissions paid (b) Total amount of fees paid
$77.44 $12.34
3. Insurance fees and commissions paid to agents, brokers, and other persons:
EXAMPLE BENEFITS LLC $77.44 3
42 RESEARCH RD
BOSTON, MA 02110
Totals: $77.44 $0.00
EXAMPLE BENEFITS LLC $12.34 Broker Bonus 3
42 RESEARCH RD
BOSTON, MA 02110
Totals: $0.00 $12.34
Part III Welfare Benefit Contract Information
10. Nonexperience-rated contracts:
(a) Total premiums or subscription charges paid to carrier... $1,234.56
'''
DAMAGED=SOURCE.replace('$77.44 3','$77.44 ie').replace('$1,234.56','87 2, 345 «el').replace('01/01/2025','0170172025')


def bound(text):
    return {'ingest':{'ingest':{'processId':'current'}},
        'process':{'ingest':{'processId':'current','status':'complete','progress':{'complete':{'documents':[
            {'fileName':'source.pdf','sourceUrl':'https://qa.invalid/current'}]}}}},
        'outputs':[{'fileName':'source.pdf','sourceUrl':'https://qa.invalid/current',
                    'chunks':[{'pageNumbers':[2],'text':text}]}]}


@pytest.mark.parametrize("address", ["PO BOX 12345", "P.O. BOX 12345"])
def test_lincoln_compensation_accepts_complete_post_office_box_address(address):
    source = SOURCE.replace("42 RESEARCH RD", address)
    pages = [(2, source)]
    rows = extractor.extract_litera_lincoln_schedule_a_broker_rows(pages)
    result = NormalizedExtractionResult(provider="fixture", fields=[])
    assert extractor.reconcile_lincoln_part_i_compensation(result, rows, pages)
    assert len(result.schedule_a_broker_rows) == 1
    broker = result.schedule_a_broker_rows[0]
    assert broker.address_line_1 == address
    assert broker.commission_total == "77.44"
    assert broker.fee_total == "12.34"


def test_lincoln_ocr_compensation_uses_surviving_broker_header_and_spaced_subtotal():
    source = (SOURCE.replace("Insurance fees and commissions paid to agents",
                             "Insurance fees and corrmxssions paid to agents")
              .replace("Totals: $77.44 $0.00", "Totals : $77 . 44 $0.00")
              .replace("42 RESEARCH RD", "PO BOX 12345"))
    pages = [(2, source)]
    rows = extractor.extract_litera_lincoln_schedule_a_broker_rows(pages)
    assert extractor.reconcile_lincoln_part_i_compensation(
        NormalizedExtractionResult(provider="fixture", fields=[]), rows, pages
    )


def test_lincoln_ocr_po_box_needs_identical_box_number_and_locality():
    source = SOURCE.replace("42 RESEARCH RD", "PO BCX 12345")
    same_box = [(1, "PO BOX 12345\nBOSTON MA 02110"), (2, source)]
    rows = extractor.extract_litera_lincoln_schedule_a_broker_rows(same_box)
    assert len(rows) == 1
    assert rows[0].address_line_1 == "PO BOX 12345"
    assert extractor.extract_litera_lincoln_schedule_a_broker_rows([
        (1, "PO BOX 54321\nBOSTON MA 02110"), (2, source),
    ]) == []


def test_fragmented_premium_and_partial_compensation_are_not_authoritative():
    fields=extractor.extract_litera_lincoln_schedule_a_fields([(2,DAMAGED)])
    assert not any(f.field_name.startswith('10a.') for f in fields)
    assert next(f.value for f in fields if f.field_name.startswith('3b.'))=='77.44'
    assert next(f.value for f in fields if f.field_name.startswith('3c.'))=='12.34'
    assert extractor.extract_nonexperience_total_premium_from_text(DAMAGED) is None


def test_incomplete_local_compensation_retains_the_complete_provider_recipient():
    provider=ScheduleABrokerRow(name='EXAMPLE BENEFITS LLC',address_line_1='42 RESEARCH RD',
        city='BOSTON',state='MA',zip_code='02110',commission_total='77.44',fee_total='12.34')
    result=NormalizedExtractionResult(provider='Recorded provider',fields=[],schedule_a_broker_rows=[provider])
    partial=extractor.extract_litera_lincoln_schedule_a_broker_rows([(2,DAMAGED)])
    assert not extractor.reconcile_lincoln_part_i_compensation(result,partial,[(2,DAMAGED)])
    assert len(result.schedule_a_broker_rows)==1
    assert result.schedule_a_broker_rows[0].commission_total=='77.44'
    assert result.schedule_a_broker_rows[0].fee_total=='12.34'
    assert result.schedule_a_broker_rows[0].decision=='REVIEW_REQUIRED'


def test_current_completed_source_recovers_only_reconciled_degraded_lincoln_pages():
    raw=bound(SOURCE)
    assert extractor.recover_lincoln_source_pages([(2,DAMAGED)],raw,'source.pdf')==[(2,SOURCE)]
    assert raw['lincoln_bound_source_recovery']['pages']==[2]


def test_bound_read_with_spaced_field_numbers_and_omitted_title_retains_layout_identity():
    spaced=SOURCE.replace('SCHEDULE A REPORTING INFORMATION\n','').replace('(d)','( d )').replace('(Part III,#8)','( Part III , # 8 )')
    raw=bound(spaced)
    assert extractor.recover_lincoln_source_pages([(2,DAMAGED)],raw,'source.pdf')==[(2,spaced)]
    assert next(f.value for f in extractor.extract_litera_lincoln_schedule_a_fields([(2,spaced)]) if f.field_name.startswith('1e.'))=='17'


@pytest.mark.parametrize("printed,expected", [
    ("00001D039241 00000", "00001D03924100000"),
    ("00001D039242 00000", "00001D03924200000"),
    ("000012345678\n2025", "000012345678"),
])
def test_lincoln_contract_uses_the_complete_printed_identifier_on_its_own_line(printed, expected):
    page = SOURCE.replace("000012345678 00000", printed)
    fields = extractor.extract_litera_lincoln_schedule_a_fields([(2, page)])
    contract = next(field for field in fields if field.field_name.startswith("1d."))
    assert contract.value == expected
    assert contract.candidate_values == [expected]


@pytest.mark.parametrize('mismatch',['document','amount','contract','readable_premium'])
def test_alternate_source_cannot_override_a_different_document_or_readable_conflict(mismatch):
    raw=bound(SOURCE)
    local=DAMAGED
    if mismatch=='document':raw['outputs'][0]['sourceUrl']='https://qa.invalid/other'
    if mismatch=='amount':local=local.replace('$77.44','$88.55')
    if mismatch=='contract':local=local.replace('000012345678','000098765432')
    if mismatch=='readable_premium':local=local.replace('87 2, 345 «el','$9,876.54')
    assert extractor.recover_lincoln_source_pages([(2,local)],raw,'source.pdf')==[(2,local)]


def test_weekly_income_is_a_declared_temporary_disability_benefit_only():
    observations=collect_schedule_a_source_observations([(2,DAMAGED)])
    assert observations['benefit_types']==['temporary_disability']
    assert collect_schedule_a_source_observations([(1,'An explanatory note mentions Weekly Income benefits.')])['benefit_types']==[]


def test_yearless_worksheet_date_does_not_invent_a_year():
    assert extractor.normalize_worksheet_date('1/1',None,end_of_month=False) is None
    assert extractor.normalize_worksheet_date('1/1','2025',end_of_month=False)=='01/01/2025'
