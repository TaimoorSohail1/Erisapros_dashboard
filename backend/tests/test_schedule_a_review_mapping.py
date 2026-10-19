import pytest

from app.models import ExtractionValidationResult, ExtractedFieldStatus, FormType, NormalizedExtractionField
from app.services.mapping import map_extraction_to_rules
from app.services.filing_pipeline import harmonize_schedule_a_business_rule_fields
from app.services.ftwilliams_review import FTWilliamsReviewService


@pytest.mark.parametrize("decision,validations", [
    ("REVIEW_REQUIRED", []),
    ("AUTOMATIC", [ExtractionValidationResult(validator="source_conflict", status="ERROR", reason="Two source values disagree.")]),
])
def test_mapping_keeps_review_value_visible_without_an_automatic_proposal(decision, validations):
    field = NormalizedExtractionField(
        field_name="1e. Persons Covered (End of Policy Year)", value="171", confidence=.99,
        decision=decision, validation_results=validations,
    )
    mapped = map_extraction_to_rules("offline", [field], form_type=FormType.SCHEDULE_A)
    actual = next(f for f in mapped["fields"] if f.source_field_name == field.field_name)
    assert actual.value == "171"
    assert actual.proposed_value == ""
    assert actual.status == ExtractedFieldStatus.LOW_CONFIDENCE
    assert "review" in actual.status_reason.lower()
    assert field.value == "171" and field.confidence == .99


def test_purpose_harmonization_does_not_reactivate_an_explicit_review_hold():
    fields = [NormalizedExtractionField(
        field_name=name, value=value, confidence=.99, page=1, source_text="Printed compensation table",
        decision="REVIEW_REQUIRED" if name.startswith("3d.") else "AUTOMATIC",
    ) for name, value in (
        ("3b. Amount of Commissions", "125"), ("3c. Amount of Fees", "0"),
        ("3d. Purpose", "Combined commission/fee classification unresolved"),
    )]
    mapped = map_extraction_to_rules("offline", fields, form_type=FormType.SCHEDULE_A)
    harmonize_schedule_a_business_rule_fields(mapped["fields"])
    purpose = next(f for f in mapped["fields"] if f.source_field_name.startswith("3d."))
    assert purpose.proposed_value == ""
    assert purpose.status == ExtractedFieldStatus.LOW_CONFIDENCE


def test_offline_carrier_alias_cleanup_cannot_reactivate_a_withheld_source_conflict():
    original = NormalizedExtractionField(field_name='1a. Name of Insurance Company',
        value='Example Life Insurance Company and affiliates ("Example")',confidence=.99,
        decision='REVIEW_REQUIRED')
    mapped = map_extraction_to_rules('offline',[original],form_type=FormType.SCHEDULE_A)
    carrier = next(field for field in mapped['fields'] if field.mapped_rule_key == 'schedule_a_part_i_1a_name_of_insurance_company')
    service = FTWilliamsReviewService(ftwilliams=object())
    fields = service._fields_with_canonical_carrier_legal_name([carrier])
    assert fields[0].value == original.value
    assert fields[0].proposed_value == ''
    assert fields[0].status == ExtractedFieldStatus.LOW_CONFIDENCE
