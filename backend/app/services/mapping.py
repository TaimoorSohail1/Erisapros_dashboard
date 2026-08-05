from datetime import datetime
from app.config import get_settings
from app.models import DocumentType, ExtractedField, ExtractedFieldStatus, FieldPriority, FilingStatus, FormType, NormalizedExtractionField
from app.services.field_rules import find_rule_for_field, form_type_for_rule, normalize_name, rules_for_form_type


def map_extraction_to_rules(
    filing_id: str,
    fields: list[NormalizedExtractionField],
    form_type: FormType | None = None,
    source_document_type: DocumentType | None = None,
):
    settings = get_settings()
    low_confidence_threshold = settings.low_confidence_threshold
    now = datetime.utcnow()
    mapped_by_rule: dict[str, ExtractedField] = {}
    unmapped: list[ExtractedField] = []
    field_rules = rules_for_form_type(form_type)

    for field in fields:
        field_rule = find_rule_for_field(field.field_name, field_rules)
        confidence = normalize_confidence(field.confidence)
        status = ExtractedFieldStatus.MATCHED
        status_reason = "Matched to FT Williams field rule."
        if field_rule and field_rule.priority == FieldPriority.IGNORE:
            status = ExtractedFieldStatus.IGNORED
            status_reason = "Field rule is marked ignore."
        elif not field_rule:
            status = ExtractedFieldStatus.UNMAPPED
            status_reason = "No Field Rules alias matched this extracted field."
        elif is_placeholder_value(field.value):
            status = ExtractedFieldStatus.MISSING
            status_reason = "Extractor matched the field name but did not return a value."
        elif confidence < low_confidence_threshold:
            status = ExtractedFieldStatus.LOW_CONFIDENCE
            status_reason = f"Confidence {confidence:.0%} is below the {low_confidence_threshold:.0%} review threshold."

        extracted = ExtractedField(
            filing_id=filing_id,
            source_field_name=field_rule.label if field_rule else field.field_name,
            normalized_field_name=normalize_name(field_rule.label if field_rule else field.field_name),
            mapped_rule_key=field_rule.key if field_rule else None,
            mapped_label=field_rule.label if field_rule else None,
            ftw_field=field_rule.ftw_field if field_rule else None,
            xml_tag=field_rule.xml_tag if field_rule else None,
            priority=field_rule.priority if field_rule else FieldPriority.LOW,
            value="" if is_placeholder_value(field.value) else field.value,
            proposed_value="" if is_placeholder_value(field.value) else field.value,
            confidence=confidence,
            page=field.page,
            source_text=field.source_text,
            source_document_type=source_document_type,
            form_type=form_type or (form_type_for_rule(field_rule) if field_rule else None),
            status=status,
            status_reason=status_reason,
            created_at=now,
            updated_at=now,
        )
        if not field_rule:
            unmapped.append(extracted)
            continue

        current = mapped_by_rule.get(field_rule.key)
        if not current or extraction_rank(extracted) > extraction_rank(current):
            mapped_by_rule[field_rule.key] = extracted

    mapped = list(mapped_by_rule.values())
    extracted_rule_keys = {field.mapped_rule_key for field in mapped if field.mapped_rule_key}
    present_keys = {
        field.mapped_rule_key
        for field in mapped
        if field.mapped_rule_key and str(field.proposed_value or "").strip() and field.status != ExtractedFieldStatus.IGNORED
    }
    missing_fields = []
    for field_rule in field_rules:
        if field_rule.priority != FieldPriority.IGNORE and field_rule.key not in present_keys and field_rule.key not in extracted_rule_keys:
            missing_fields.append(
                ExtractedField(
                    filing_id=filing_id,
                    source_field_name=field_rule.label,
                    normalized_field_name=normalize_name(field_rule.label),
                    mapped_rule_key=field_rule.key,
                    mapped_label=field_rule.label,
                    ftw_field=field_rule.ftw_field,
                    xml_tag=field_rule.xml_tag,
                    priority=field_rule.priority,
                    source_document_type=source_document_type,
                    form_type=form_type or form_type_for_rule(field_rule),
                    value="",
                    proposed_value="",
                    confidence=0,
                    status=ExtractedFieldStatus.MISSING,
                    status_reason=f"{field_rule.priority.value} priority FT Williams field was not found in the extraction output.",
                    created_at=now,
                    updated_at=now,
                )
            )

    all_fields = [*mapped, *missing_fields, *dedupe_unmapped_fields(unmapped)]
    low_confidence_count = len([field for field in all_fields if field.status == ExtractedFieldStatus.LOW_CONFIDENCE])
    unmapped_count = len([field for field in all_fields if field.status == ExtractedFieldStatus.UNMAPPED])
    missing_high_priority_count = len([field for field in all_fields if field.status == ExtractedFieldStatus.MISSING and field.priority == FieldPriority.HIGH])
    missing_medium_priority_count = len([field for field in all_fields if field.status == ExtractedFieldStatus.MISSING and field.priority == FieldPriority.MEDIUM])
    missing_low_priority_count = len([field for field in all_fields if field.status == ExtractedFieldStatus.MISSING and field.priority == FieldPriority.LOW])
    scored = [field for field in all_fields if field.status != ExtractedFieldStatus.MISSING and field.priority != FieldPriority.IGNORE]
    overall_confidence = sum(field.confidence for field in scored) / len(scored) if scored else 0
    status = FilingStatus.NEEDS_REVIEW if missing_high_priority_count or low_confidence_count or unmapped_count else FilingStatus.READY_FOR_APPROVAL

    return {
        "fields": all_fields,
        "low_confidence_count": low_confidence_count,
        "missing_high_priority_count": missing_high_priority_count,
        "missing_medium_priority_count": missing_medium_priority_count,
        "missing_low_priority_count": missing_low_priority_count,
        "unmapped_count": unmapped_count,
        "overall_confidence": overall_confidence,
        "status": status,
    }


def normalize_confidence(value: float) -> float:
    if value > 1:
        return min(value / 100, 1)
    if value < 0:
        return 0
    return min(value, 1)


def is_placeholder_value(value: object) -> bool:
    text = str(value or "").strip().lower()
    if text in {"", "missing", "unreadable", "blank", "none", "null", "obscured", "not provided", "not shown", "not visible", "redacted"}:
        return True
    return any(marker in text for marker in ["obscured", "redaction", "redacted", "not visible", "unreadable"])


def extraction_rank(field: ExtractedField) -> tuple[int, float, int]:
    status_rank = {
        ExtractedFieldStatus.MATCHED: 3,
        ExtractedFieldStatus.LOW_CONFIDENCE: 2,
        ExtractedFieldStatus.MISSING: 1,
        ExtractedFieldStatus.IGNORED: 0,
        ExtractedFieldStatus.UNMAPPED: 0,
    }.get(field.status, 0)
    return (status_rank, field.confidence, 0)


def dedupe_unmapped_fields(fields: list[ExtractedField]) -> list[ExtractedField]:
    seen: set[tuple[str, str]] = set()
    deduped: list[ExtractedField] = []
    for field in fields:
        key = (field.normalized_field_name, str(field.proposed_value or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(field)
    return deduped
