"""Customer-authorized defaults for proposed recipients, never FTW snapshots."""
from app.models import NormalizedExtractionField, NormalizedExtractionResult, ScheduleABrokerRow, SourceEvidence

DEFAULT_CODE_REASON = "Customer rule: Defaulted to 3 because organizational code was blank."


def default_blank_organization_codes(rows: list[ScheduleABrokerRow]) -> list[ScheduleABrokerRow]:
    normalized = []
    for original in rows:
        row = original.model_copy(deep=True)
        if row.organization_code is None or not str(row.organization_code).strip():
            row.organization_code = "3"
            row.organization_code_defaulted = True
        elif str(row.organization_code).strip() != "3":
            row.organization_code_defaulted = False
        normalized.append(row)
    return normalized


def apply_customer_defaults(result: NormalizedExtractionResult) -> None:
    result.schedule_a_broker_rows = default_blank_organization_codes(result.schedule_a_broker_rows)
    code_field = next((field for field in result.fields if field.field_name.strip().lower().startswith("3e.")), None)
    if code_field is None:
        code_field = NormalizedExtractionField(field_name="3e. Organizational Code", value="", confidence=0)
        result.fields.append(code_field)
        if result.schedule_a_broker_rows and not result.schedule_a_broker_rows[0].organization_code_defaulted:
            # An explicit recipient code is not a blank just because its scalar
            # counterpart was omitted by the provider.
            row = result.schedule_a_broker_rows[0]
            code_field.value = str(row.organization_code)
            code_field.confidence = row.confidence
            code_field.evidence = list(row.evidence)
            code_field.source_text = "Organizational code supplied on the first disclosed recipient."
    if code_field is not None and not str(code_field.value or "").strip():
        code_field.value = "3"
        code_field.candidate_values = ["3"]
        # Certainty of the explicit customer rule, not fabricated OCR confidence.
        code_field.confidence = 1.0
        code_field.source_text = DEFAULT_CODE_REASON
        code_field.evidence = [SourceEvidence(provider="Customer configured default", source_text=DEFAULT_CODE_REASON)]
        raw = dict(result.raw) if isinstance(result.raw, dict) else {"provider_raw": result.raw}
        raw["customer_defaults"] = [{"field": code_field.field_name, "value": "3", "reason": DEFAULT_CODE_REASON}]
        result.raw = raw
