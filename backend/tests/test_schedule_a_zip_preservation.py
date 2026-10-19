import pytest
from app.services.extractor import normalize_zip_code
from app.models import NormalizedExtractionResult, ScheduleABrokerRow
from app.services.schedule_a_extraction_pipeline import apply_schedule_a_pipeline
from app.services.xml_builder import schedule_a_broker_update_values
from app.services.ftwilliams_contract import FTWPayloadValidationError


@pytest.mark.parametrize("printed,expected", [
    ("50398-900", "50398-900"),
    ("2110", "2110"),
    ("021103173", "02110-3173"),
    ("02110", "02110"),
])
def test_zip_normalization_formats_existing_digits_but_never_invents_one(printed, expected):
    assert normalize_zip_code(printed) == expected


def test_invalid_printed_zip_is_retained_but_held_even_when_canonical_validation_is_shadow_only():
    result = NormalizedExtractionResult(provider="Example", fields=[], schedule_a_broker_rows=[
        ScheduleABrokerRow(name="Example Broker", zip_code="50398-900", commission_total="125",
                          commission_source_text="Commissions $125", fee_total="0", confidence=.99),
    ])
    result = apply_schedule_a_pipeline(result, authoritative=False, shadow=True)
    row = result.schedule_a_broker_rows[0]
    assert row.zip_code == "50398-900"
    assert row.decision == "REVIEW_REQUIRED"
    assert any(v.validator == "source_zip_shape" and v.status == "ERROR" for v in row.validation_results)


def test_offline_broker_serialization_cannot_accept_a_review_required_recipient():
    row = ScheduleABrokerRow(name="Example Broker", zip_code="02110", commission_total="125",
                             decision="REVIEW_REQUIRED", organization_code="3")
    with pytest.raises(FTWPayloadValidationError, match="review"):
        schedule_a_broker_update_values([row])


def test_nine_printed_zip_digits_do_not_create_a_false_review_hold():
    result = NormalizedExtractionResult(provider="Example", fields=[], schedule_a_broker_rows=[
        ScheduleABrokerRow(name="Example Broker", zip_code="600063009", commission_total="125",
                          commission_source_text="Commissions $125", fee_total="0", confidence=.99),
    ])
    result = apply_schedule_a_pipeline(result, authoritative=False, shadow=False)
    assert result.schedule_a_broker_rows[0].decision != "REVIEW_REQUIRED"


@pytest.mark.parametrize('authoritative',[False,True])
@pytest.mark.parametrize('printed',['ABC','10'])
def test_invalid_explicit_organization_code_is_preserved_and_held_not_defaulted(printed,authoritative):
    result = NormalizedExtractionResult(provider='Example',fields=[],schedule_a_broker_rows=[
        ScheduleABrokerRow(name='Example Broker',address_line_1='42 RESEARCH RD',city='BOSTON',state='MA',
            zip_code='02110',organization_code=printed,commission_total='125',fee_total='0',
            commission_source_text='Commissions $125',confidence=.99),
    ])
    result=apply_schedule_a_pipeline(result,authoritative=authoritative,shadow=False)
    row=result.schedule_a_broker_rows[0]
    assert row.organization_code==printed and row.organization_code_defaulted is False
    assert row.decision=='REVIEW_REQUIRED'
    assert any(v.validator=='source_organization_code_shape' and v.status=='ERROR' for v in row.validation_results)
