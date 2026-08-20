import re

from app.models import DocumentType, FieldRule, FieldRuleMappingMode, FormType
from app.services.extractor import ExtractionService
from app.services.field_rule_admin import FieldRuleService
from app.services.field_rules import find_rule_for_field, normalize_name, rules_for_form_type


async def run_field_rule_qa(
    file_bytes: bytes,
    file_name: str,
    document_type: DocumentType,
    rules: list[FieldRule],
    *,
    extractor=None,
    rule_set_version: str = "",
) -> dict:
    """Run extraction and mapping diagnostics without creating a filing or sending XML."""
    form_type = FormType.FORM_5500 if document_type == DocumentType.PLAN_WORKSHEET else FormType.SCHEDULE_A
    relevant_rules = rules_for_form_type(form_type, rules)
    extraction_service = extractor or ExtractionService(relevant_rules)
    extraction = await extraction_service.extract_document(file_bytes, file_name, document_type)

    fields: list[dict] = []
    for extracted in extraction.fields:
        rule = find_rule_for_field(extracted.field_name, relevant_rules)
        matched_alias = matched_rule_name(extracted.field_name, rule, extracted.source_text) if rule else None
        update_tag = FieldRuleService.approved_update_tag(rule.key) if rule else None
        extraction_only = bool(rule and rule.mapping_mode == FieldRuleMappingMode.EXTRACTION_ONLY)
        fields.append(
            {
                "field_name": extracted.field_name,
                "value": extracted.value,
                "confidence": extracted.confidence,
                "page": extracted.page,
                "source_text": extracted.source_text,
                "matched": bool(rule),
                "matched_alias": matched_alias,
                "mapped_rule_key": rule.key if rule else None,
                "mapped_label": rule.label if rule else None,
                "mapping_mode": rule.mapping_mode.value if rule else None,
                "ftw_field": rule.ftw_field if rule and not extraction_only else None,
                "ftw_tag": update_tag if rule and not extraction_only else None,
                "will_send_to_ftw": bool(rule and not extraction_only and update_tag),
            }
        )

    matched_count = sum(1 for field in fields if field["matched"])
    extraction_only_count = sum(1 for field in fields if field["mapping_mode"] == FieldRuleMappingMode.EXTRACTION_ONLY.value)
    return {
        "provider": extraction.provider,
        "rule_set_version": rule_set_version,
        "document_type": document_type.value,
        "file_name": file_name,
        "summary": {
            "extracted": len(fields),
            "matched": matched_count,
            "unmatched": len(fields) - matched_count,
            "extraction_only": extraction_only_count,
        },
        "fields": fields,
    }


def matched_rule_name(field_name: str, rule: FieldRule, source_text: str | None = None) -> str:
    for line in str(source_text or "").splitlines():
        source_label = re.split(r"\s*(?::|\t|[–—])\s*", line.strip(), maxsplit=1)[0]
        normalized_source = normalize_name(source_label)
        for candidate in [*rule.aliases, rule.label]:
            if normalize_name(candidate) == normalized_source:
                return candidate
    normalized = normalize_name(field_name)
    for candidate in [rule.label, *rule.aliases]:
        if normalize_name(candidate) == normalized:
            return candidate
    return field_name
