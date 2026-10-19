"""Required-field checks apply to this form and known contract category."""
import pytest

from app.models import FieldRule, NormalizedExtractionField, NormalizedExtractionResult
from app.services.schedule_a_extraction_pipeline import apply_schedule_a_pipeline


def rule(key,label,source='Schedule A',applicability='BOTH'):
    return FieldRule(key=key,label=label,ftw_field=label,priority='HIGH',source=source,
                     field_type='Dynamic',required=True,applicability=applicability)


def source_result(signals, premium=True):
    values=[('1a. Name of Insurance Company','Example Life Insurance Company'),
            ('1b. Insurance Carrier EIN','12-3456789'),('1c. NAIC Code','12345'),
            ('1d. Contract/Policy Number','00984512'),('1e. Persons Covered (End of Policy Year)','53'),
            ('1f. Policy Year Beginning Date','01/01/2024'),('1g. Policy Year Ending Date','12/31/2024')]
    if premium:values.append(('10a. Paid premiums','1250.50'))
    return NormalizedExtractionResult(provider='source test',classification_signals=signals,
        fields=[NormalizedExtractionField(field_name=label,value=value,confidence=.99,page=1,
                                          source_text=f'{label}: {value}') for label,value in values])


def test_form_5500_required_field_is_not_a_missing_schedule_a_pdf_value():
    rules=[rule('form_5500_part_i_1f_plan_sponsor_address','1f. Plan Sponsor Address',
                source='Form 5500 - Part I',applicability='FORM_5500')]
    result=apply_schedule_a_pipeline(source_result([]),authoritative=True,rules=rules)
    assert result.raw['extraction_quality']['missing_required_fields']==[]


@pytest.mark.parametrize('premium',[True,False])
def test_known_nonexperience_contract_requires_only_its_applicable_financial_fields(premium):
    rules=[rule('schedule_a_part_iii_9b_1_benefit_charges_1_claims_paid','9b(1). Claims paid',applicability='EXPERIENCE'),
           rule('schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier','10a. Paid premiums',applicability='NONEXPERIENCE')]
    result=apply_schedule_a_pipeline(source_result(['EXPLICIT_NONEXPERIENCE_RATED'],premium),authoritative=True,rules=rules)
    assert result.raw['extraction_quality']['missing_required_fields']==([] if premium else ['10a. Paid premiums'])


@pytest.mark.parametrize('signals,expected',[
    (['EXPLICIT_EXPERIENCE_RATED'],['9b(1). Claims paid']),
    ([],['9b(1). Claims paid','10a. Paid premiums']),
])
def test_experience_requirements_and_unclassified_review_are_not_suppressed(signals,expected):
    rules=[rule('schedule_a_part_iii_9b_1_benefit_charges_1_claims_paid','9b(1). Claims paid',applicability='EXPERIENCE'),
           rule('schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier','10a. Paid premiums',applicability='NONEXPERIENCE')]
    result=apply_schedule_a_pipeline(source_result(signals,premium=False),authoritative=True,rules=rules)
    assert result.raw['extraction_quality']['missing_required_fields']==expected
    assert result.raw['extraction_quality']['decision']=='REVIEW_REQUIRED'
