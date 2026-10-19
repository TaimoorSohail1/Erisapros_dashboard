import pytest
from app.models import NormalizedExtractionField, NormalizedExtractionResult, ScheduleABrokerRow
from app.services.schedule_a_semantic_layer import SemanticDocument, enrich_schedule_a_result
from app.services.schedule_a_extraction_pipeline import apply_schedule_a_pipeline
from app.services.mapping import map_extraction_to_rules
from app.services.extractor import extract_vsp_broker_rows, extract_vsp_schedule_a_fields


@pytest.mark.parametrize("fees", ["0", "613.34"])
@pytest.mark.parametrize("authoritative", [False, True])
def test_combined_payment_requires_review_even_when_provider_puts_it_only_in_commission(fees, authoritative):
    source = "Insurance Fees and Commissions Paid to Agents and Brokers:\nAgent or Broker   Commissions/Fees\nEXAMPLE BENEFITS LLC $613.34"
    result = NormalizedExtractionResult(provider="Example", fields=[
        NormalizedExtractionField(field_name="3b. Amount of Commissions", value="613.34", confidence=.99),
        NormalizedExtractionField(field_name="3c. Amount of Fees", value=fees, confidence=.99),
    ], schedule_a_broker_rows=[ScheduleABrokerRow(name="EXAMPLE BENEFITS LLC", commission_total="613.34",
        fee_total=fees, commission_source_text=source, source_page=1, confidence=.99)])
    result = enrich_schedule_a_result(result, SemanticDocument.from_page_texts([(1, source)]), rules=[])
    result = apply_schedule_a_pipeline(result, authoritative=authoritative, shadow=True, rules=[])
    payment_fields = [f for f in result.fields if f.field_name.startswith(("3b.", "3c."))]
    assert payment_fields and all(f.decision == "REVIEW_REQUIRED" for f in payment_fields)
    assert result.schedule_a_broker_rows[0].decision == "REVIEW_REQUIRED"
    assert any(v.validator == "payment_classification_ambiguity" for v in result.schedule_a_broker_rows[0].validation_results)
    mapped = map_extraction_to_rules("offline", result.fields)
    assert all(not f.proposed_value for f in mapped["fields"] if f.source_field_name.startswith(("3b.", "3c.")))


def test_vsp_parser_labels_a_combined_payment_as_unclassified_not_commission_only():
    pages = [(1, """VSP vision care Schedule A Form (5500) Insurance Information
Insurance Carrier: Vision Service Plan
Commissions/Fees Paid for Policy Agent or Broker or Contract Year
Different Advisory Inc $1,234.5650 Other RdBoston MA 02110
""")]
    row = extract_vsp_broker_rows(pages)[0]
    assert row.decision == "REVIEW_REQUIRED"
    assert "unclassified" in row.commission_rows[0].purpose.lower()
    assert "commission-only" not in row.commission_source_text.lower()
    fields = extract_vsp_schedule_a_fields(pages)
    assert all(f.decision == "REVIEW_REQUIRED" for f in fields if f.field_name.startswith(("3b.", "3c.", "3d.")))
