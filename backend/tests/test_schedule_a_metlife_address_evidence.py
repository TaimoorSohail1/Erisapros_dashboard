"""Printed recipient identity must survive OCR punctuation, not disappear."""
from app.services.extractor import extract_metlife_standard_broker_rows
from app.models import NormalizedExtractionResult
from app.services.schedule_a_extraction_pipeline import apply_schedule_a_pipeline


def report(address):
    return [(4,'''Metropolitan Life Insurance Company
Schedule A Insurance Information
Name: EXAMPLE AGENT
'''+address+'''
Commissions Paid Fees Paid Organization
Coverage Amount Purpose Coverage Amount Purpose code
LIFE 42.50 Base Commissions 06
42.50 Sub-total 0 Sub-total
''')]


def test_metlife_city_label_noise_does_not_discard_the_printed_recipient_address():
    rows=extract_metlife_standard_broker_rows(report("Address: 26 RESEARCH RD STE 9 City: . '\nBOSTON ST: MA ZIP: 02110-0042"))
    assert len(rows)==1
    row=rows[0]
    assert row.address_line_1=='26 RESEARCH RD STE 9'
    assert (row.city,row.state,row.zip_code)==('BOSTON','MA','02110-0042')
    assert row.name=='EXAMPLE AGENT' and row.organization_code=='06'
    assert row.commission_total=='42.50' and row.fee_total=='0'


def test_metlife_unreadable_printed_address_stays_in_review_through_canonical_validation():
    rows=extract_metlife_standard_broker_rows(report('Address: unreadable City: ??? ST: MA ZIP: 02110'))
    result=apply_schedule_a_pipeline(NormalizedExtractionResult(provider='Source',fields=[],schedule_a_broker_rows=rows),
        authoritative=True,shadow=False,rules=[])
    assert len(result.schedule_a_broker_rows)==1
    row=result.schedule_a_broker_rows[0]
    assert row.decision=='REVIEW_REQUIRED'
    assert row.address_line_1 is None and row.city is None
    assert row.commission_total=='42.50'
    assert any(v.validator=='printed_recipient_address_unreadable' and v.status=='ERROR' for v in row.validation_results)
    assert any('Address: unreadable' in e.source_text for e in row.evidence)
