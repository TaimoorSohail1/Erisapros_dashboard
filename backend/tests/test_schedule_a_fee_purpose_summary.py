"""A payment category is not the fee-purpose text printed for a recipient."""
from copy import deepcopy

import pytest

from app.models import (ExtractedField, ExtractedFieldStatus, FieldPriority, FormType,
                        NormalizedExtractionField, NormalizedExtractionResult,
                        ScheduleABrokerMoneyRow, ScheduleABrokerRow, SourceEvidence)
from app.services.filing_pipeline import harmonize_schedule_a_business_rule_fields
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result


def fee_row(purpose='Administration', fee='20.50', address='10 Main St'):
    source=f'Example Agency {address} Boston MA 02199 Commissions Paid 5.00 Fees Paid {fee} Purpose {purpose}'
    return ScheduleABrokerRow(name='Example Agency',address_line_1=address,city='Boston',state='MA',zip_code='02199',
        commission_total='5.00',fee_total=fee,purpose=purpose,source_page=2,confidence=.99,
        commission_rows=[ScheduleABrokerMoneyRow(amount='5.00',purpose='Base Commissions')],
        fee_rows=[ScheduleABrokerMoneyRow(amount=fee,purpose=purpose)],
        evidence=[SourceEvidence(provider='Printed recipient table',page=2,source_text=source)])


def result_with_summary(rows, summary='COMMISSIONS & FEES'):
    from decimal import Decimal
    commission=sum(Decimal(row.commission_total) for row in rows)
    fees=sum(Decimal(row.fee_total) for row in rows)
    fields=[NormalizedExtractionField(field_name=name,value=value,page=2,confidence=.99,
        source_text=f'Derived recipient payment category: {summary}\nCommissions {commission}; Fees {fees}')
        for name,value in [('3b. Amount of Commissions',str(commission)),('3c. Amount of Fees',str(fees)),('3d. Purpose',summary)]]
    return NormalizedExtractionResult(provider='test',fields=fields,schedule_a_broker_rows=rows,
        raw={'extraction_stages':[{'stage':'before_canonical','fields':[f.model_dump(mode='json') for f in fields]}]})


@pytest.mark.parametrize('purpose', ['Administration','Renewal Incentive','Bonus Paid'])
def test_fee_summary_uses_the_fee_purpose_not_a_commission_category(purpose):
    before=result_with_summary([fee_row(purpose)])
    broker_before=deepcopy(before.schedule_a_broker_rows)
    result=resolve_schedule_a_result(before)
    actual=next(f for f in result.fields if f.field_name.startswith('3d.'))
    assert actual.value==purpose
    assert actual.decision=='AUTOMATIC'
    assert result.schedule_a_broker_rows[0].fee_rows==broker_before[0].fee_rows
    assert result.raw['extraction_stages'][0]['fields'][2]['value']=='COMMISSIONS & FEES'


def test_same_fee_purpose_across_distinct_recipients_is_not_replaced_by_combined_category():
    result=resolve_schedule_a_result(result_with_summary([fee_row('Bonus Paid'),fee_row('Bonus Paid',address='30 Oak Rd')]))
    assert next(f.value for f in result.fields if f.field_name.startswith('3d.'))=='Bonus Paid'
    assert len(result.schedule_a_broker_rows)==2


def test_distinct_fee_purposes_stay_with_each_recipient_not_one_invented_scalar():
    result=resolve_schedule_a_result(result_with_summary([fee_row('Administration'),fee_row('Renewal Incentive',address='30 Oak Rd')]))
    assert not any(f.field_name.startswith('3d.') and f.value for f in result.fields)
    assert [row.purpose for row in result.schedule_a_broker_rows]==['Administration','Renewal Incentive']


def test_a_real_explicit_summary_is_not_overwritten_by_recipient_inference():
    result=resolve_schedule_a_result(result_with_summary([fee_row('Administration')],summary='Special Consulting'))
    assert next(f.value for f in result.fields if f.field_name.startswith('3d.'))=='Special Consulting'


def test_summary_reconciliation_does_not_discard_a_real_candidate_conflict():
    result=result_with_summary([fee_row()])
    result.fields[2].candidate_values=['COMMISSIONS & FEES','Another printed purpose']
    actual=resolve_schedule_a_result(result).fields[2]
    assert actual.value=='COMMISSIONS & FEES'
    assert actual.decision=='REVIEW_REQUIRED'
    assert 'Another printed purpose' in actual.candidate_values


def test_unreconciled_fee_components_cannot_supply_an_automatic_summary():
    row=fee_row()
    row.fee_rows[0].amount='99.00'
    result=resolve_schedule_a_result(result_with_summary([row]))
    assert not result.raw.get('fee_purpose_summary_reconciliation')
    assert result.schedule_a_broker_rows[0].decision=='REVIEW_REQUIRED'


def test_missing_purpose_is_not_manufactured_from_amounts_by_the_reconciler():
    row=fee_row()
    row.fee_rows[0].purpose=None
    result=resolve_schedule_a_result(result_with_summary([row]))
    assert not result.raw.get('fee_purpose_summary_reconciliation')


@pytest.mark.parametrize('category', ['commission','fee'])
def test_recipient_component_sum_difference_of_one_cent_requires_review(category):
    row=fee_row()
    getattr(row,category+'_rows')[0].amount='5.01' if category=='commission' else '20.51'
    result=resolve_schedule_a_result(result_with_summary([row]))
    actual=result.schedule_a_broker_rows[0]
    assert actual.decision=='REVIEW_REQUIRED'
    assert any(v.validator==f'broker_{category}_components' and v.status=='ERROR' for v in actual.validation_results)


def test_signed_recipient_components_reconcile_without_losing_adjustments():
    row=fee_row()
    row.commission_rows=[ScheduleABrokerMoneyRow(amount='-0.80',purpose='Base Commissions'),ScheduleABrokerMoneyRow(amount='5.80',purpose='Base Commissions')]
    row.fee_rows=[ScheduleABrokerMoneyRow(amount='-0.50',purpose='Administration'),ScheduleABrokerMoneyRow(amount='21.00',purpose='Administration')]
    result=resolve_schedule_a_result(result_with_summary([row]))
    assert result.schedule_a_broker_rows[0].decision=='AUTOMATIC'
    assert result.schedule_a_broker_rows[0].fee_rows[0].amount=='-0.50'


@pytest.mark.parametrize('multiple_benefits', [False,True])
def test_section_total_difference_of_one_cent_is_not_a_rounding_pass(multiple_benefits):
    row=fee_row()
    if multiple_benefits:
        row.commission_rows=[ScheduleABrokerMoneyRow(coverage='LIFE',amount='2.00',purpose='Base Commissions'),
                             ScheduleABrokerMoneyRow(coverage='DENTAL',amount='3.00',purpose='Base Commissions')]
    result=result_with_summary([row])
    result.fields[0].value='5.01'
    result.fields[0].source_text='Printed commissions total $5.01'
    actual=resolve_schedule_a_result(result)
    assert any(v.validator=='broker_total_reconciliation' and v.status=='ERROR' for v in actual.fields[0].validation_results)


def mapped(code,value,*,status=ExtractedFieldStatus.MATCHED):
    keys={'3b':'schedule_a_part_i_3b_amount_of_commissions','3c':'schedule_a_part_i_3c_amount_of_fees','3d':'schedule_a_part_i_3d_purpose'}
    return ExtractedField(filing_id='test',source_field_name=code,normalized_field_name=code,
        mapped_rule_key=keys[code],ftw_field=code,priority=FieldPriority.LOW,value=value,proposed_value=value,
        confidence=.99,page=2,source_text=f'Printed {code}: {value}',form_type=FormType.SCHEDULE_A,status=status)


@pytest.mark.parametrize('purpose', ['Administration','Fees','N/A'])
def test_later_filing_pass_does_not_replace_an_explicit_fee_purpose_with_money_categories(purpose):
    fields=[mapped('3b','5.00'),mapped('3c','20.50'),mapped('3d',purpose)]
    harmonize_schedule_a_business_rule_fields(fields)
    assert fields[2].value==fields[2].proposed_value==purpose


@pytest.mark.parametrize('fees', ['0','20.50'])
def test_later_filing_pass_does_not_recreate_an_omitted_fee_purpose(fees):
    fields=[mapped('3b','5.00'),mapped('3c',fees),mapped('3d','',status=ExtractedFieldStatus.MISSING)]
    harmonize_schedule_a_business_rule_fields(fields)
    assert fields[2].proposed_value==''
    assert fields[2].status==ExtractedFieldStatus.MISSING
