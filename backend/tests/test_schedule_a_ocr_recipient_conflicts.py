"""Independent OCR identities are review evidence, never merge permission."""
import pytest
from app.models import NormalizedExtractionResult,ScheduleABrokerRow,SourceEvidence
from app.services.schedule_a_broker_validation import hold_local_ocr_recipient_conflicts
from app.services.schedule_a_extraction_pipeline import apply_schedule_a_pipeline


def recipient(name, *, amount='25.50', street='9 EXAMPLE RD'):
    return ScheduleABrokerRow(name=name,address_line_1=street,city='BOSTON',state='MA',zip_code='02110',
        commission_total=amount,fee_total='0',organization_code='6',source_page=2,confidence=.99,
        evidence=[SourceEvidence(provider='Independent reader',page=2,source_text=name+' '+street,table_cell=(1,0))])


@pytest.mark.parametrize('local,expected',[
    ('NORTHWEST BENEFXTS LLC','NORTHWEST BENEFITS LLC'),
    ('DIFFERENT SERVICES LLC','DIFFERENT SERVICES LLC'),
])
def test_independent_name_conflict_is_withheld_without_merging_recipients(local,expected):
    prior=recipient('NORTHWEST BENEFITS LLC')
    result=NormalizedExtractionResult(provider='Provider',fields=[],schedule_a_broker_rows=[recipient(local)],raw={
        'local_ocr_pages':[{'page':2,'text':'Independent OCR source'}],
        'extraction_stages':[{'stage':'structured_provider_normalized','broker_rows':[prior.model_dump(mode='json')]}]})
    hold_local_ocr_recipient_conflicts(result)
    result=apply_schedule_a_pipeline(result,authoritative=True,shadow=False,rules=[])
    assert len(result.schedule_a_broker_rows)==1
    row=result.schedule_a_broker_rows[0]
    assert row.name==expected
    assert row.decision=='REVIEW_REQUIRED'
    assert any(v.validator=='ocr_recipient_identity_conflict' for v in row.validation_results)
    assert prior.name=='NORTHWEST BENEFITS LLC'


@pytest.mark.parametrize('variation',['different_payment','ambiguous_anchor','no_local_ocr'])
def test_incomplete_identity_anchors_do_not_restore_or_merge_names(variation):
    prior=recipient('NORTHWEST BENEFITS LLC')
    local=recipient('NORTHWEST BENEFXTS LLC',amount='26.50' if variation=='different_payment' else '25.50')
    rows=[local,recipient('ANOTHER LEGAL ENTITY LLC')] if variation=='ambiguous_anchor' else [local]
    result=NormalizedExtractionResult(provider='Provider',fields=[],schedule_a_broker_rows=rows,raw={
        'local_ocr_pages':[] if variation=='no_local_ocr' else [{'page':2,'text':'Independent OCR source'}],
        'extraction_stages':[{'stage':'structured_provider_normalized','broker_rows':[prior.model_dump(mode='json')]}]})
    before=[row.model_dump(mode='json') for row in rows]
    hold_local_ocr_recipient_conflicts(result)
    assert [row.model_dump(mode='json') for row in result.schedule_a_broker_rows]==before
