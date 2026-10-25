"""Independent OCR identities are review evidence, never merge permission."""
import pytest
from app.models import ExtractionValidationResult,NormalizedExtractionResult,ScheduleABrokerMoneyRow,ScheduleABrokerRow,SourceEvidence
from app.services.schedule_a_broker_validation import hold_local_ocr_recipient_conflicts
from app.services.schedule_a_extraction_pipeline import apply_schedule_a_pipeline


def recipient(name, *, amount='25.50', street='9 EXAMPLE RD'):
    return ScheduleABrokerRow(name=name,address_line_1=street,city='BOSTON',state='MA',zip_code='02110',
        commission_total=amount,fee_total='0',commission_rows=[ScheduleABrokerMoneyRow(amount=amount,purpose='Commissions')],organization_code='6',source_page=2,confidence=.99,
        evidence=[SourceEvidence(provider='Independent reader',page=2,source_text=name+' '+street,table_cell=(1,0))])


def test_aggregate_provider_payment_does_not_hide_a_split_row_name_conflict():
    prior = recipient('NORTHWEST BENEFITS LLC')
    prior.fee_total = '7.25'
    current = recipient('NORTHWEST BENEFXTS LLC')
    other = recipient(prior.name, amount='0', street='9 EXAMPLE ROAD')
    other.fee_total = '7.25'
    independent = recipient(prior.name)
    result = NormalizedExtractionResult(provider='Provider', fields=[], schedule_a_broker_rows=[current, other], raw={
        'local_ocr_pages': [{'page': 2, 'text': 'Separate source rows with category subtotals'}],
        'extraction_stages': [{'stage': 'structured_provider_normalized', 'broker_rows': [prior.model_dump(mode='json')]}]})
    calls = []
    def read_rows(page):
        calls.append(page)
        return [independent, other]
    hold_local_ocr_recipient_conflicts(result, corroborating_rows=read_rows)
    assert calls == [2]
    assert current.name == prior.name
    assert current.commission_total == '25.50' and current.fee_total == '0'
    assert other.commission_total == '0' and other.fee_total == '7.25'
    assert len(result.schedule_a_broker_rows) == 2
    assert result.raw['local_ocr_recipient_conflicts'][0]['decision'] == 'CORROBORATED'


@pytest.mark.parametrize('reading', ['unavailable', 'aggregate_amounts', 'different_name', 'missing_fee', 'duplicate'])
def test_split_row_identity_remains_held_without_an_exact_physical_source_read(reading):
    prior = recipient('NORTHWEST BENEFITS LLC')
    prior.fee_total = '7.25'
    current = recipient('NORTHWEST BENEFXTS LLC')
    independent = recipient('OTHER BENEFITS LLC' if reading == 'different_name' else prior.name)
    if reading == 'aggregate_amounts':
        independent.fee_total = '7.25'
    if reading == 'missing_fee':
        independent.fee_total = None
    result = NormalizedExtractionResult(provider='Provider', fields=[], schedule_a_broker_rows=[current], raw={
        'local_ocr_pages': [{'page': 2, 'text': 'Physical commission row, separate fee row'}],
        'extraction_stages': [{'stage': 'structured_provider_normalized', 'broker_rows': [prior.model_dump(mode='json')]}]})
    def read_rows(page):
        return [] if reading == 'unavailable' else [independent, independent.model_copy(deep=True)] if reading == 'duplicate' else [independent]
    hold_local_ocr_recipient_conflicts(result, corroborating_rows=read_rows)
    assert current.decision == 'REVIEW_REQUIRED'
    assert any(v.validator == 'ocr_recipient_identity_conflict' for v in current.validation_results)
    assert current.commission_total == '25.50' and current.fee_total == '0'
    assert len(result.schedule_a_broker_rows) == 1


def test_separate_source_read_can_confirm_the_current_name_when_the_provider_is_wrong():
    previous=recipient('NORTHWEST BENEFITS 3 LLC')
    current=recipient('NORTHWEST BENEFITS LLC')
    independent=recipient(current.name)
    independent.evidence.extend(SourceEvidence(provider=provider,page=2,bounding_box=(1,2,30,40),source_text=independent.name+' 9 EXAMPLE RD')
        for provider in ['Document-bound Hartford grid identity OCR (PSM 6)','Independent Hartford grid identity OCR (PSM 4/7)'])
    result=NormalizedExtractionResult(provider='Provider',fields=[],schedule_a_broker_rows=[current],raw={
        'local_ocr_pages':[{'page':2,'text':'Independent OCR source'}],
        'extraction_stages':[{'stage':'structured_provider_normalized','broker_rows':[previous.model_dump(mode='json')]}]})
    hold_local_ocr_recipient_conflicts(result,grid_corroborating_rows=lambda page:[independent])
    assert result.schedule_a_broker_rows[0].name=='NORTHWEST BENEFITS LLC'
    assert not any(v.validator=='ocr_recipient_identity_conflict' for v in current.validation_results)
    assert current.commission_total=='25.50' and current.organization_code=='6'


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


@pytest.mark.parametrize('variation', ['confirmed', 'wrong_address', 'wrong_amount', 'wrong_name', 'duplicate', 'wrong_page', 'unrelated_error'])
def test_source_corroboration_is_identity_only_and_preserves_real_conflicts(variation):
    prior = recipient('NORTHWEST BENEFITS LLC')
    current = recipient('NORTHWEST BENEFXTS LLC')
    if variation == 'unrelated_error':
        current.validation_results.append(ExtractionValidationResult(validator='source_zip_shape', status='ERROR', reason='Separate source error'))
    third = recipient('ANOTHER COMPANY LLC' if variation == 'wrong_name' else prior.name,
                      street='8 OTHER RD' if variation == 'wrong_address' else prior.address_line_1,
                      amount='99.00' if variation == 'wrong_amount' else prior.commission_total)
    # Secondary OCR is not authority to overwrite money, codes or other fields.
    third.organization_code = '5'
    if variation == 'wrong_page':
        third.source_page = 3
    result = NormalizedExtractionResult(provider='Provider', fields=[], schedule_a_broker_rows=[current], raw={
        'local_ocr_pages':[{'page':2,'text':'Independent OCR source'}],
        'extraction_stages':[{'stage':'structured_provider_normalized','broker_rows':[prior.model_dump(mode='json')]}]})
    calls = []
    def read_rows(page):
        calls.append(page)
        return [third, third.model_copy(deep=True)] if variation == 'duplicate' else [third]
    hold_local_ocr_recipient_conflicts(result, corroborating_rows=read_rows)
    result = apply_schedule_a_pipeline(result, authoritative=True, shadow=False, rules=[])
    row = result.schedule_a_broker_rows[0]
    assert calls == [2]
    assert row.organization_code == '6' and row.commission_total == '25.50'
    assert len(result.schedule_a_broker_rows) == 1
    conflicts = [v for v in row.validation_results if v.validator == 'ocr_recipient_identity_conflict']
    if variation in {'confirmed', 'unrelated_error'}:
        assert not conflicts
        assert result.raw['local_ocr_recipient_conflicts'][0]['decision'] == 'CORROBORATED'
        assert row.name == prior.name
    else:
        assert conflicts and row.decision == 'REVIEW_REQUIRED'
    if variation == 'unrelated_error':
        assert any(v.validator == 'source_zip_shape' for v in row.validation_results)
        assert row.decision == 'REVIEW_REQUIRED'
