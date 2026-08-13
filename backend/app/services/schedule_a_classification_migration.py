from __future__ import annotations

from app.models import (
    AuditLog,
    ExtractedField,
    FieldRule,
    FieldRuleStatus,
    FilingStatus,
    FTWilliamsComparisonField,
    ScheduleAContractType,
)
from app.repositories import Repository
from app.services.field_rules import DEFAULT_FIELD_RULES
from app.services.filing_pipeline import summarize_mapped_fields
from app.services.schedule_a_classification import (
    apply_schedule_a_classification,
    filter_schedule_a_fields_for_contract_type,
)


INACTIVE_STATUSES = {FilingStatus.DELETED, FilingStatus.SUPERSEDED}


async def reclassify_active_filings(
    repository: Repository,
    *,
    apply_changes: bool = False,
) -> list[dict]:
    rules = await _published_rules_without_seeding(repository)
    report: list[dict] = []

    for filing in await repository.list_filings():
        if not filing.id or filing.status in INACTIVE_STATUSES:
            continue
        stored_fields = await repository.list_fields(filing.id)
        if not stored_fields:
            continue
        fields = [field.model_copy(deep=True) for field in stored_fields]
        before_by_id = {
            field.id: (field.proposed_value, field.status, field.status_reason)
            for field in fields
            if field.id
        }
        classification = apply_schedule_a_classification(
            fields,
            filing.schedule_a_classification_signals,
        )
        changed_fields = [
            field
            for field in fields
            if field.id and before_by_id.get(field.id) != (field.proposed_value, field.status, field.status_reason)
        ]
        relevant_fields = filter_schedule_a_fields_for_contract_type(
            fields,
            classification.contract_type,
            rules=rules,
        )
        summary = summarize_mapped_fields(relevant_fields)
        item = {
            "filing_id": filing.id,
            "file_name": filing.file_name,
            "previous_contract_type": filing.schedule_a_contract_type.value,
            "next_contract_type": classification.contract_type.value,
            "reason": classification.reason,
            "confidence": classification.confidence,
            "evidence": list(classification.evidence),
            "derived_field_count": len(
                [field for field in changed_fields if str(field.status_reason or "").startswith("Automatically derived")]
            ),
            "changed_field_count": len(changed_fields),
        }
        report.append(item)
        if not apply_changes:
            continue

        for field in changed_fields:
            await repository.update_field(
                filing.id,
                field.id,
                field.proposed_value,
                status=field.status,
                status_reason=field.status_reason,
            )
        filing_updates = {
            "schedule_a_contract_type": classification.contract_type,
            "schedule_a_contract_type_reason": classification.reason,
            "schedule_a_contract_type_confirmed": True,
            "schedule_a_contract_type_confidence": classification.confidence,
            "schedule_a_contract_type_evidence": list(classification.evidence),
            "overall_confidence": summary["overall_confidence"],
            "missing_high_priority_count": summary["missing_high_priority_count"],
            "missing_medium_priority_count": summary["missing_medium_priority_count"],
            "missing_low_priority_count": summary["missing_low_priority_count"],
            "low_confidence_count": summary["low_confidence_count"],
            "unmapped_count": summary["unmapped_count"],
            "review_field_count": summary["review_field_count"],
            "found_field_count": summary["found_field_count"],
            "excluded_field_count": max(0, len(fields) - len(relevant_fields)),
        }
        await repository.update_filing(filing.id, filing_updates)
        stored_review = await repository.get_ftwilliams_review(filing.id)
        if stored_review:
            relevant_field_ids = {field.id for field in relevant_fields if field.id}
            fields_by_id = {field.id: field for field in fields if field.id}
            fields_by_rule = {field.mapped_rule_key: field for field in fields if field.mapped_rule_key}
            stored_review.schedule_a_contract_type = classification.contract_type
            stored_review.schedule_a_contract_type_reason = classification.reason
            stored_review.schedule_a_contract_type_confirmed = True
            stored_review.schedule_a_contract_type_confidence = classification.confidence
            stored_review.schedule_a_contract_type_evidence = list(classification.evidence)
            known_types = {
                ScheduleAContractType.EXPERIENCE_RATED,
                ScheduleAContractType.NONEXPERIENCE_RATED,
            }
            stored_review.schedule_a_contract_type_mismatch = bool(
                stored_review.ftw_schedule_a_contract_type in known_types
                and stored_review.ftw_schedule_a_contract_type != classification.contract_type
            )
            for comparison in stored_review.fields:
                source_field = (
                    fields_by_id.get(comparison.field_id)
                    if comparison.field_id
                    else fields_by_rule.get(comparison.rule_key)
                )
                if not source_field:
                    continue
                _refresh_comparison_from_field(comparison, source_field)
                if source_field.id not in relevant_field_ids:
                    comparison.update_included = False
            await repository.upsert_ftwilliams_review(stored_review)
        await repository.add_audit(
            AuditLog(
                filing_id=filing.id,
                event="SCHEDULE_A_AUTO_CLASSIFICATION_MIGRATED",
                message="Existing filing was reclassified using the automatic Schedule A rating rules.",
                details=item,
            )
        )

    return report


def _refresh_comparison_from_field(comparison: FTWilliamsComparisonField, field: ExtractedField) -> None:
    comparison.extracted_value = str(field.value or "")
    comparison.proposed_value = str(field.proposed_value or field.value or "")
    comparison.confidence = field.confidence
    comparison.priority = field.priority
    comparison.extraction_status = field.status
    comparison.changed = comparison.current_value.strip() != comparison.proposed_value.strip()


async def _published_rules_without_seeding(repository: Repository) -> list[FieldRule]:
    versions = await repository.list_field_rule_versions()
    if not versions:
        return DEFAULT_FIELD_RULES
    published: list[FieldRule] = []
    for key in {rule.key for rule in versions}:
        latest_published = max(
            (rule for rule in versions if rule.key == key and rule.status == FieldRuleStatus.PUBLISHED),
            key=lambda rule: rule.version,
            default=None,
        )
        latest_disabled = max(
            (rule for rule in versions if rule.key == key and rule.status == FieldRuleStatus.DISABLED),
            key=lambda rule: rule.version,
            default=None,
        )
        if latest_published and (not latest_disabled or latest_published.version > latest_disabled.version):
            published.append(latest_published)
    return published
