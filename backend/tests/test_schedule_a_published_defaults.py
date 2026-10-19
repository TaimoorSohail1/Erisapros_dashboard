from app.models import FieldRule, NormalizedExtractionField, NormalizedExtractionResult
from app.services.schedule_a_extraction_pipeline import apply_schedule_a_pipeline
import pytest


def published_rule():
    return FieldRule(key="schedule_a_information_not_provided", label="11. Did the insurance company fail to provide any information necessary to complete Schedule A?",
        ftw_field="11. Provision of Information", source="Schedule A - Part III", field_type="Static", priority="LOW",
        notes="Always no", version=7)


@pytest.mark.parametrize("authoritative", [False, True])
def test_missing_static_value_uses_the_actual_published_rule_with_its_provenance(authoritative):
    rule = published_rule()
    result = NormalizedExtractionResult(provider="Example", fields=[
        NormalizedExtractionField(field_name="1a. Name of Insurance Company", value="Example Insurance Company"),
    ])
    result = apply_schedule_a_pipeline(result, authoritative=authoritative, shadow=True, rules=[rule])
    value = next(f for f in result.fields if f.field_name == rule.label)
    assert value.value == "No"
    assert value.evidence[0].provider == "Published field rule default"
    assert value.evidence[0].page is None
    assert value.decision == "AUTOMATIC"
    assert any(d.get("rule_key") == rule.key and d.get("rule_version") == 7 for d in result.raw["customer_defaults"])


def test_explicit_yes_is_not_overwritten_by_the_published_no_default():
    rule = published_rule()
    result = NormalizedExtractionResult(provider="Example", fields=[
        NormalizedExtractionField(field_name=rule.label, value="Yes", page=1, source_text=rule.label + " Yes", confidence=.99),
    ])
    result = apply_schedule_a_pipeline(result, authoritative=False, shadow=False, rules=[rule])
    assert result.fields[0].value == "Yes"
    assert result.fields[0].decision == "REVIEW_REQUIRED"
