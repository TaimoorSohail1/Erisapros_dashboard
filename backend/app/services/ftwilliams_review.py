from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
import re
import time
from urllib.parse import parse_qs, quote, urlsplit
import xml.etree.ElementTree as ET

from app.config import get_settings
from app.models import (
    AuditLog,
    ClientFacingError,
    DocumentType,
    ExtractedField,
    ExtractedFieldStatus,
    FieldPriority,
    FilingStatus,
    FormType,
    FTWilliamsComparisonField,
    FTWilliamsEditCheckIssue,
    FTWilliamsManualMatchRequest,
    FTWilliamsBrokerMatchesRequest,
    FTWilliamsScheduleABrokerRowsRequest,
    FTWilliamsOperationDiagnostic,
    FTWilliamsPlanLookup,
    FTWilliamsPlanLookupStatus,
    FTWilliamsPlanMapping,
    FTWilliamsPlanYearResolution,
    FTWilliamsQueryRequest,
    FTWilliamsReview,
    FTWilliamsReviewStatus,
    ScheduleAContractType,
    ScheduleABrokerMatch,
    ScheduleABrokerRow,
    FTWilliamsScheduleAContractTypeRequest,
    FTWilliamsScheduleAMatchRequest,
    FTWilliamsSendUpdateRequest,
    FTWilliamsStatusItem,
    ReviewEvent,
)
from app.repositories import get_repository
from app.services.error_normalizer import normalize_client_error
from app.services.extractor import merge_schedule_a_broker_rows
from app.services.field_rule_admin import FieldRuleService
from app.services.ftwilliams import FTWilliamsService
from app.services.ftwilliams_contract import FTWFieldValidationIssue, FTWPayloadValidationError
from app.services.ftwilliams_schema import FTWilliamsSchemaService
from app.services.ftwilliams_failures import classify_ftwilliams_failure, failure_issue_groups
from app.services.storage import StorageService
from app.services.ftwilliams_tags import (
    FORM_5500_CURRENT_TAGS_BY_RULE,
    FORM_5500_TAGS_BY_RULE,
    FORM_5500_UPDATE_TAGS_BY_RULE,
    SCHEDULE_A_CURRENT_TAGS_BY_RULE,
    SCHEDULE_A_TAGS_BY_RULE,
    normalize_compare_value,
    resolve_ftw_current_tag,
    resolve_ftw_current_value,
    resolve_ftw_tag,
    resolve_ftw_update_tag,
    values_meaningfully_different,
)
from app.services.schedule_a_classification import (
    ScheduleAClassification,
    apply_schedule_a_classification,
    classify_schedule_a_fields,
    classify_schedule_a_current,
    filter_schedule_a_fields_for_contract_type,
    schedule_a_contract_type_allows_rule,
)
from app.services.schedule_a_broker_matching import (
    current_schedule_a_broker_rows,
    match_schedule_a_brokers,
    resolved_schedule_a_broker_rows,
)
from app.services.xml_builder import (
    build_single_document_update_xml,
    build_schedule_a_records_update_xml,
    combine_ftw_update_xml,
    schedule_a_broker_multipart_rows,
    schedule_a_broker_update_values,
    schedule_a_replacement_data_gaps,
)


_CURRENT_DATA_SNAPSHOT_CACHE: dict[tuple[str, ...], tuple[float, dict]] = {}
_CURRENT_DATA_SNAPSHOT_INFLIGHT: dict[tuple[str, ...], asyncio.Task] = {}
_PLAN_LOOKUP_CACHE: dict[tuple[str, ...], tuple[float, FTWilliamsPlanLookup]] = {}
_PLAN_LOOKUP_INFLIGHT: dict[tuple[str, ...], asyncio.Task] = {}
_FTW_UPDATE_LOCKS: dict[tuple[str, ...], asyncio.Lock] = {}


def clear_ftw_current_snapshot_cache() -> None:
    """Clear process-local FT Williams plan and current-data snapshots."""
    _CURRENT_DATA_SNAPSHOT_CACHE.clear()
    _CURRENT_DATA_SNAPSHOT_INFLIGHT.clear()
    _PLAN_LOOKUP_CACHE.clear()
    _PLAN_LOOKUP_INFLIGHT.clear()


class FTWilliamsReviewService:
    _SCHEDULE_A_AUTO_MATCH_MIN_SCORE_MARGIN = 4
    def __init__(self, ftwilliams: FTWilliamsService | None = None):
        self.ftwilliams = ftwilliams or FTWilliamsService()

    @staticmethod
    def _edit_check_issue_key(issue: FTWilliamsEditCheckIssue) -> tuple[str, ...]:
        fallback_message = re.sub(r"\s+", " ", str(issue.message or "")).strip().upper()
        field_identity = str(issue.field_line or issue.field_label or fallback_message).strip().upper()
        return (
            str(issue.code or "").strip().upper(),
            str(issue.form_type or "").strip().upper(),
            str(issue.status_type or "").strip().upper(),
            str(issue.schedule_seq_no or "").strip(),
            field_identity,
        )

    @classmethod
    def _classify_edit_check_result(cls, review: FTWilliamsReview) -> None:
        baseline = list(review.edit_check_baseline_issues or [])
        final = list(review.edit_check_final_issues or [])
        baseline_by_key = {cls._edit_check_issue_key(issue): issue for issue in baseline}
        final_by_key = {cls._edit_check_issue_key(issue): issue for issue in final}
        review.edit_check_new_issues = [
            issue for key, issue in final_by_key.items() if key not in baseline_by_key
        ]
        review.edit_check_resolved_issues = [
            issue for key, issue in baseline_by_key.items() if key not in final_by_key
        ]

        if review.edit_check_final_success is None:
            review.edit_check_validation_status = "NOT_RUN"
        elif review.edit_check_final_success:
            review.edit_check_validation_status = "RESOLVED" if baseline else "CLEAN"
        elif not final:
            review.edit_check_validation_status = "CHECK_UNAVAILABLE"
        elif review.edit_check_baseline_success is False and not baseline:
            review.edit_check_validation_status = "CANNOT_COMPARE"
        elif review.edit_check_new_issues:
            review.edit_check_validation_status = "NEW_ISSUES"
        elif review.edit_check_resolved_issues:
            review.edit_check_validation_status = "IMPROVED"
        else:
            review.edit_check_validation_status = "EXISTING_ISSUES"

    async def prepare_review(
        self,
        filing_id: str,
        send_queries: bool = False,
        *,
        reuse_current_snapshot: bool = False,
        apply_automatic_derivations: bool = True,
        preloaded: tuple | None = None,
    ) -> FTWilliamsReview:
        repo = get_repository()
        if preloaded is None:
            published_rules = await FieldRuleService(repo).published_rules()
            filing = await repo.get_filing(filing_id)
            if not filing:
                raise ValueError("Filing not found")
            fields = await repo.list_fields(filing_id)
            existing_review = await repo.get_ftwilliams_review(filing_id)
        else:
            filing, fields, published_rules, existing_review = preloaded
            if not filing or filing.id != filing_id:
                raise ValueError("Filing not found")

        query_payload_base = self._query_payload_base()
        configured = bool(self.ftwilliams.status()["configured"])
        if reuse_current_snapshot and send_queries:
            plan_lookup = await self._shared_plan_lookup(
                filing,
                fields,
                configured=configured,
            )
        else:
            plan_lookup = await self._prepare_plan_lookup(
                filing,
                fields,
                send_queries=send_queries,
                configured=configured,
            )
        if (
            not send_queries
            and existing_review
            and existing_review.plan_lookup
            and self._should_preserve_authoritative_plan_lookup(plan_lookup, existing_review.plan_lookup)
        ):
            plan_lookup = deepcopy(existing_review.plan_lookup)
        review_identity_base = self._merge_plan_lookup_identity(query_payload_base, plan_lookup)
        current_query_payload_base = self._current_query_payload_identity(query_payload_base, plan_lookup)
        query_request_xmls: list[str] = []
        query_response_xmls: list[str] = []
        form_5500_current: dict[str, str] = {}
        schedule_a_current: dict[str, str] = {}
        matched_schedule_a: FTWilliamsStatusItem | None = None
        schedule_a_candidates: list[dict] = []
        schedule_a_records: list[dict] = []
        error_message: str | None = self._authoritative_plan_lookup_error(plan_lookup)
        current_query_success = False
        current_query_complete: bool | None = None
        current_query_failed = False
        current_year_exists = False
        bring_forward_required = False
        comparison_year: str | None = None
        comparison_year_source: str | None = None
        create_new_schedule_a = bool((existing_review.schedule_a_match or {}).get("create_new")) if existing_review else False
        new_schedule_desc = str((existing_review.schedule_a_match or {}).get("schedule_desc") or "").strip() if create_new_schedule_a else None
        schedule_a_broker_rows = self._normalized_schedule_a_broker_rows(filing.schedule_a_broker_rows)
        schedule_a_worksheet_summaries = self._normalized_schedule_a_worksheet_summaries(filing.schedule_a_worksheet_summaries)
        legacy_active_failure = bool(
            existing_review
            and existing_review.failure_dismissed_at is None
            and existing_review.status in {
                FTWilliamsReviewStatus.UPDATE_FAILED,
                FTWilliamsReviewStatus.UPDATE_UNKNOWN,
            }
        )
        active_failure = bool(existing_review and (existing_review.active_failure or legacy_active_failure))

        if not send_queries and existing_review:
            query_request_xmls.extend([existing_review.query_request_xml] if existing_review.query_request_xml else [])
            query_response_xmls.extend([existing_review.query_response_xml] if existing_review.query_response_xml else [])
            form_5500_current = dict(existing_review.form_5500_current_values) or self._review_current_values(
                existing_review,
                FormType.FORM_5500,
            )
            schedule_a_current = dict(existing_review.schedule_a_current_values) or self._review_current_values(
                existing_review,
                FormType.SCHEDULE_A,
            )
            schedule_a_candidates = list(existing_review.schedule_a_candidates or [])
            schedule_a_records = list(existing_review.schedule_a_records or [])
            current_query_success = existing_review.current_query_success
            current_query_complete = existing_review.current_query_complete
            current_year_exists = existing_review.current_year_exists
            bring_forward_required = existing_review.bring_forward_required
            comparison_year = existing_review.comparison_year
            comparison_year_source = existing_review.comparison_year_source

        if send_queries:
            if not configured:
                error_message = "FT Williams endpoint and KeyID must be configured before querying current FTW values."
            elif not self._has_current_query_inputs(current_query_payload_base):
                error_message = (
                    plan_lookup.error_message
                    or "FT Williams current-data query needs CustomerID/PlanID or FTWCustomerID/FTWPlanID plus filing year."
                )
            else:
                query_result = await self._query_current_values_for_target_year(
                    fields,
                    current_query_payload_base,
                    existing_review,
                    reuse_current_snapshot=reuse_current_snapshot,
                )
                query_request_xmls.extend(query_result["query_request_xmls"])
                query_response_xmls.extend(query_result["query_response_xmls"])
                form_5500_current = query_result["form_5500_current"]
                schedule_a_current = query_result["schedule_a_current"]
                matched_schedule_a = query_result["matched_schedule_a"]
                schedule_a_candidates = query_result["schedule_a_candidates"]
                schedule_a_records = query_result["schedule_a_records"]
                error_message = query_result["error_message"]
                current_query_success = query_result["current_query_success"]
                current_query_complete = query_result["current_query_complete"]
                current_query_failed = query_result["current_query_failed"]
                current_year_exists = query_result["current_year_exists"]
                bring_forward_required = query_result["bring_forward_required"]
                comparison_year = query_result["comparison_year"]
                comparison_year_source = query_result["comparison_year_source"]
                if query_result["current_query_failed"] and existing_review and self._review_has_valid_current_snapshot(existing_review):
                    form_5500_current = dict(existing_review.form_5500_current_values)
                    schedule_a_current = dict(existing_review.schedule_a_current_values)
                    schedule_a_candidates = list(existing_review.schedule_a_candidates or [])
                    schedule_a_records = list(existing_review.schedule_a_records or [])
                    current_query_success = False
                    current_query_complete = False
                    current_year_exists = existing_review.current_year_exists
                    bring_forward_required = False
                    comparison_year = existing_review.comparison_year
                    comparison_year_source = existing_review.comparison_year_source
                    preservation_note = "FT Williams current data could not be refreshed; the last valid snapshot was preserved for safety."
                    error_message = "; ".join(filter(None, [preservation_note, error_message]))

        selected_schedule_desc = None
        if matched_schedule_a:
            selected_schedule_desc = matched_schedule_a.query_results.get("ScheduleDesc") or matched_schedule_a.query_results.get("SCHEDULE_DESC")
        elif create_new_schedule_a:
            selected_schedule_desc = new_schedule_desc
        elif existing_review and existing_review.schedule_a_match:
            selected_schedule_desc = str(
                existing_review.schedule_a_match.get("schedule_desc")
                or existing_review.schedule_a_match.get("description")
                or existing_review.schedule_a_match.get("ScheduleDesc")
                or ""
            ).strip()
        if apply_automatic_derivations:
            fields = self._fields_with_schedule_a_summary_override(fields, schedule_a_worksheet_summaries, selected_schedule_desc)

        ftw_editability = self._ftw_editability_status(form_5500_current)
        if ftw_editability["editable"] is None and existing_review and not send_queries:
            ftw_editability = {
                "editable": existing_review.ftw_editable,
                "locked_status": existing_review.ftw_locked_status,
                "signed_status": existing_review.ftw_signed_status,
                "filing_status": existing_review.ftw_filing_status,
            }
        if ftw_editability["editable"] is False:
            status_summary = ", ".join(
                filter(
                    None,
                    [
                        f"lock status: {ftw_editability['locked_status']}" if ftw_editability["locked_status"] else None,
                        f"signed status: {ftw_editability['signed_status']}" if ftw_editability["signed_status"] else None,
                        f"filing status: {ftw_editability['filing_status']}" if ftw_editability["filing_status"] else None,
                    ],
                )
            )
            lock_message = (
                "FT Williams reports this Form 5500 as locked and not editable"
                f" ({status_summary}). Use Amend Filing in FT Williams, then query current data again."
            )
            error_message = "; ".join(filter(None, [error_message, lock_message]))

        plan_year_conflict = self._plan_year_conflict(fields, form_5500_current, schedule_a_current)
        plan_year_resolution = self._effective_plan_year_resolution(
            existing_review,
            fields,
            form_5500_current,
            schedule_a_current,
        )
        plan_year_update_confirmed = plan_year_resolution is not None
        form_5500_block_reason = None if plan_year_update_confirmed else self._form_5500_update_block_reason(fields, form_5500_current)
        schedule_a_block_reason = None if plan_year_update_confirmed else self._schedule_a_update_block_reason(fields, schedule_a_current)
        if form_5500_block_reason:
            error_message = "; ".join(filter(None, [error_message, form_5500_block_reason]))
        if schedule_a_block_reason:
            error_message = "; ".join(filter(None, [error_message, schedule_a_block_reason]))
        candidate_form_5500_fields = self._safe_update_fields(fields, FormType.FORM_5500, form_5500_current)
        safe_form_5500_fields = [] if form_5500_block_reason else candidate_form_5500_fields
        if apply_automatic_derivations:
            automatic_field_state = self._automatic_field_state(fields)
            computed_contract_classification = apply_schedule_a_classification(
                fields,
                filing.schedule_a_classification_signals,
            )
            await self._persist_automatic_field_changes(repo, filing_id, automatic_field_state, fields)
        else:
            computed_contract_classification = classify_schedule_a_fields(
                fields,
                filing.schedule_a_classification_signals,
            )
        extracted_contract_classification = self._effective_schedule_a_classification(
            filing,
            computed_contract_classification,
            preserve_confirmed=not apply_automatic_derivations,
        )
        ftw_contract_classification = classify_schedule_a_current(schedule_a_current) if schedule_a_current else None
        contract_type_mismatch = bool(
            ftw_contract_classification
            and extracted_contract_classification.contract_type != ftw_contract_classification.contract_type
            and extracted_contract_classification.contract_type.value != "UNKNOWN"
            and ftw_contract_classification.contract_type.value != "UNKNOWN"
        )
        safe_schedule_a_fields = filter_schedule_a_fields_for_contract_type(
            self._safe_update_fields(
                fields,
                FormType.SCHEDULE_A,
                schedule_a_current,
                has_structured_schedule_a_brokers=bool(schedule_a_broker_rows),
            ),
            extracted_contract_classification.contract_type,
            rules=published_rules,
        )
        comparison_fields = self._comparison_fields(
            fields,
            form_5500_current,
            schedule_a_current,
            # A filing-level safety block controls send readiness, not whether a
            # mapped field is writable. Keep supported decisions visible as
            # updates while the final payload remains locked by the block.
            update_fields=[*candidate_form_5500_fields, *safe_schedule_a_fields],
            schedule_a_contract_type=extracted_contract_classification.contract_type,
        )
        self._mark_structured_broker_comparisons(comparison_fields, schedule_a_broker_rows)
        include_5500_update = not bring_forward_required and self._should_build_update_payload(send_queries, form_5500_current)
        include_schedule_a_update = not bring_forward_required and (
            self._should_build_update_payload(send_queries, schedule_a_current) or (
                create_new_schedule_a and send_queries and bool(schedule_a_records)
            )
        )
        existing_identity = self._identity_from_review(existing_review) if existing_review else {}
        if existing_review and existing_review.ftw_seq_no:
            existing_identity["ftw_seq_no"] = existing_review.ftw_seq_no
        identity = review_identity_base | existing_identity | self._identity_from_status(matched_schedule_a)
        broker_matches, resolved_broker_rows = self._resolve_schedule_a_brokers(
            schedule_a_broker_rows,
            schedule_a_records,
            identity.get("ftw_seq_no"),
            existing_review.schedule_a_broker_matches if existing_review else [],
            create_new=create_new_schedule_a,
        )
        broker_match_complete = all(match.resolved for match in broker_matches)
        broker_match_blocked = bool(schedule_a_broker_rows) and not broker_match_complete
        if broker_match_blocked:
            error_message = "; ".join(filter(None, [
                error_message,
                "Schedule A broker rows need confirmation before FT Williams can be updated.",
            ]))
        ftw_plan_url = self._ftw_plan_page_url(identity, identity.get("year")) if bring_forward_required else None
        payload_validation_issues: list[FTWFieldValidationIssue] = []
        try:
            update_xml_5500 = build_single_document_update_xml(
                "DOL5500Data",
                safe_form_5500_fields if include_5500_update else [],
                FormType.FORM_5500,
                transaction_type="1",
                current_values=form_5500_current,
                **identity,
            )
        except FTWPayloadValidationError as exc:
            payload_validation_issues.extend(exc.issues)
            update_xml_5500 = ""
        try:
            update_xml_schedule_a = "" if bring_forward_required else self._build_schedule_a_update_xml(
                safe_schedule_a_fields if include_schedule_a_update else [],
                schedule_a_records,
                identity.get("ftw_seq_no"),
                identity,
                all_record_fields=(
                    self._schedule_a_plan_year_fields(safe_schedule_a_fields)
                    if plan_year_update_confirmed
                    else []
                ),
                schedule_update_blocked=bool(schedule_a_block_reason) or broker_match_blocked,
                add_new_schedule_a=create_new_schedule_a,
                new_schedule_desc=new_schedule_desc,
                schedule_a_broker_rows=resolved_broker_rows,
            )
        except FTWPayloadValidationError as exc:
            payload_validation_issues.extend(exc.issues)
            update_xml_schedule_a = ""
        if payload_validation_issues:
            validation_message = str(FTWPayloadValidationError(payload_validation_issues))
            error_message = "; ".join(filter(None, [error_message, validation_message]))
        proposed_xml = combine_ftw_update_xml(update_xml_5500, update_xml_schedule_a)
        await repo.update_filing(filing_id, {"proposed_xml": proposed_xml})

        preserve_verified_update = bool(
            existing_review
            and existing_review.status == FTWilliamsReviewStatus.UPDATE_SENT
            and existing_review.update_verification_attempted
            and existing_review.update_verification_success is not False
            and existing_review.update_remaining_count == 0
            and current_query_success
            and not active_failure
            and broker_match_complete
            and not any(field.changed and field.update_included for field in comparison_fields)
        )

        review = FTWilliamsReview(
            filing_id=filing_id,
            status=(
                FTWilliamsReviewStatus.UPDATE_SENT
                if preserve_verified_update
                else FTWilliamsReviewStatus.BRING_FORWARD_REQUIRED
                if bring_forward_required
                else FTWilliamsReviewStatus.CURRENT_QUERIED
                if current_query_success
                else FTWilliamsReviewStatus.PREVIEW_READY
            ),
            configured=configured,
            current_query_sent=send_queries or bool(existing_review and existing_review.current_query_sent),
            current_query_success=current_query_success,
            current_query_complete=current_query_complete,
            current_year_exists=current_year_exists,
            bring_forward_required=bring_forward_required,
            ftw_editable=ftw_editability["editable"],
            ftw_locked_status=ftw_editability["locked_status"],
            ftw_signed_status=ftw_editability["signed_status"],
            ftw_filing_status=ftw_editability["filing_status"],
            ftw_plan_url=ftw_plan_url,
            comparison_year=comparison_year,
            comparison_year_source=comparison_year_source,
            plan_year_conflict=plan_year_conflict,
            plan_year_resolution=plan_year_resolution,
            plan_year_resolution_begin=(existing_review.plan_year_resolution_begin if plan_year_resolution and existing_review else None),
            plan_year_resolution_end=(existing_review.plan_year_resolution_end if plan_year_resolution and existing_review else None),
            schedule_a_match=(
                self._schedule_match_payload_preserving_decision(matched_schedule_a, fields, existing_review)
                if matched_schedule_a
                else (
                    (existing_review.schedule_a_match or None)
                    if existing_review
                    and (
                        not send_queries
                        or current_query_failed
                        or str((existing_review.schedule_a_match or {}).get("source") or "").upper()
                        in {"MANUAL", "NEW_SCHEDULE_A"}
                    )
                    else None
                )
            ),
            schedule_a_candidates=schedule_a_candidates,
            schedule_a_records=schedule_a_records,
            schedule_a_broker_rows=schedule_a_broker_rows,
            schedule_a_broker_matches=broker_matches,
            schedule_a_broker_match_complete=broker_match_complete,
            schedule_a_worksheet_summaries=schedule_a_worksheet_summaries,
            schedule_a_contract_type=extracted_contract_classification.contract_type,
            schedule_a_contract_type_reason=extracted_contract_classification.reason,
            schedule_a_contract_type_confirmed=True,
            schedule_a_contract_type_confidence=extracted_contract_classification.confidence,
            schedule_a_contract_type_evidence=list(extracted_contract_classification.evidence),
            ftw_schedule_a_contract_type=ftw_contract_classification.contract_type if ftw_contract_classification else filing.ftw_schedule_a_contract_type,
            ftw_schedule_a_contract_type_reason=ftw_contract_classification.reason if ftw_contract_classification else filing.ftw_schedule_a_contract_type_reason,
            schedule_a_contract_type_mismatch=contract_type_mismatch,
            plan_lookup=plan_lookup,
            query_request_xml="\n\n".join(query_request_xmls) or None,
            query_response_xml="\n\n".join(query_response_xmls) or None,
            form_5500_current_values=form_5500_current,
            schedule_a_current_values=schedule_a_current,
            update_xml_5500=update_xml_5500,
            update_xml_schedule_a=update_xml_schedule_a,
            update_response_xml=(existing_review.update_response_xml if preserve_verified_update else None),
            update_verification_attempted=(existing_review.update_verification_attempted if preserve_verified_update else False),
            update_verification_success=(existing_review.update_verification_success if preserve_verified_update else None),
            update_verification_mismatches=(list(existing_review.update_verification_mismatches or []) if preserve_verified_update else []),
            update_verification_request_xml=(existing_review.update_verification_request_xml if preserve_verified_update else None),
            update_verification_response_xml=(existing_review.update_verification_response_xml if preserve_verified_update else None),
            schedule_a_restore_attempted=(existing_review.schedule_a_restore_attempted if preserve_verified_update else False),
            schedule_a_restore_success=(existing_review.schedule_a_restore_success if preserve_verified_update else None),
            schedule_a_restore_response_xml=(existing_review.schedule_a_restore_response_xml if preserve_verified_update else None),
            schedule_a_restore_verification_request_xml=(existing_review.schedule_a_restore_verification_request_xml if preserve_verified_update else None),
            schedule_a_restore_verification_response_xml=(existing_review.schedule_a_restore_verification_response_xml if preserve_verified_update else None),
            schedule_a_restore_verification_mismatches=(list(existing_review.schedule_a_restore_verification_mismatches or []) if preserve_verified_update else []),
            update_attempted_count=(existing_review.update_attempted_count if preserve_verified_update else 0),
            update_confirmed_count=(existing_review.update_confirmed_count if preserve_verified_update else 0),
            update_remaining_count=(existing_review.update_remaining_count if preserve_verified_update else 0),
            update_results=(list(existing_review.update_results or []) if preserve_verified_update else []),
            update_retry_count=(existing_review.update_retry_count if preserve_verified_update else 0),
            update_diagnostics=list(existing_review.update_diagnostics or []) if existing_review else [],
            schema_validation_results=(
                list(existing_review.schema_validation_results or []) if existing_review else []
            ),
            schema_validation_blocked=(
                bool(existing_review.schema_validation_blocked) if existing_review else False
            ),
            query_access_verified=bool(
                current_query_success and current_query_complete is not False
            ),
            update_access_status=(
                existing_review.update_access_status if existing_review else "NOT_ATTEMPTED"
            ),
            edit_check_baseline_request_xml=(
                existing_review.edit_check_baseline_request_xml if existing_review else None
            ),
            edit_check_baseline_response_xml=(
                existing_review.edit_check_baseline_response_xml if existing_review else None
            ),
            edit_check_baseline_success=(
                existing_review.edit_check_baseline_success if existing_review else None
            ),
            edit_check_baseline_issues=(
                list(existing_review.edit_check_baseline_issues or []) if existing_review else []
            ),
            edit_check_request_xml=(
                existing_review.edit_check_request_xml if existing_review else None
            ),
            edit_check_response_xml=(
                existing_review.edit_check_response_xml if existing_review else None
            ),
            edit_check_final_success=(
                existing_review.edit_check_final_success if existing_review else None
            ),
            edit_check_final_issues=(
                list(existing_review.edit_check_final_issues or []) if existing_review else []
            ),
            edit_check_validation_status=(
                existing_review.edit_check_validation_status if existing_review else "NOT_RUN"
            ),
            edit_check_new_issues=(
                list(existing_review.edit_check_new_issues or []) if existing_review else []
            ),
            edit_check_resolved_issues=(
                list(existing_review.edit_check_resolved_issues or []) if existing_review else []
            ),
            audit_pdf_status=(existing_review.audit_pdf_status if existing_review else "NOT_REQUESTED"),
            audit_pdf_key=(existing_review.audit_pdf_key if existing_review else None),
            audit_pdf_bucket=(existing_review.audit_pdf_bucket if existing_review else None),
            audit_pdf_local_path=(existing_review.audit_pdf_local_path if existing_review else None),
            audit_pdf_sha256=(existing_review.audit_pdf_sha256 if existing_review else None),
            audit_pdf_created_at=(existing_review.audit_pdf_created_at if existing_review else None),
            audit_pdf_error=(existing_review.audit_pdf_error if existing_review else None),
            error_message=error_message,
            client_error=self._normalize_review_error(error_message, comparison_fields),
            active_failure=active_failure,
            active_failure_reason=(
                existing_review.active_failure_reason or existing_review.error_message
                if active_failure and existing_review
                else None
            ),
            active_failure_client_error=(
                existing_review.active_failure_client_error
                or existing_review.client_error
                or self._normalize_review_error(
                    existing_review.active_failure_reason or existing_review.error_message,
                    comparison_fields,
                )
                if active_failure and existing_review
                else None
            ),
            active_failure_at=(existing_review.active_failure_at or existing_review.updated_at if active_failure and existing_review else None),
            failure_dismissed_at=(existing_review.failure_dismissed_at if existing_review else None),
            failure_dismissed_reason=(existing_review.failure_dismissed_reason if existing_review else None),
            fields=comparison_fields,
            **identity,
        )
        review = await repo.upsert_ftwilliams_review(review)
        filing_contract_updates = {
            "schedule_a_contract_type": extracted_contract_classification.contract_type,
            "schedule_a_contract_type_reason": extracted_contract_classification.reason,
            "schedule_a_contract_type_confirmed": True,
            "schedule_a_contract_type_confidence": extracted_contract_classification.confidence,
            "schedule_a_contract_type_evidence": list(extracted_contract_classification.evidence),
        }
        classification_relevant_fields = filter_schedule_a_fields_for_contract_type(
            fields,
            extracted_contract_classification.contract_type,
            rules=published_rules,
        )
        filing_contract_updates |= self._review_field_count_updates(fields, classification_relevant_fields)
        if ftw_contract_classification:
            filing_contract_updates |= {
                "ftw_schedule_a_contract_type": ftw_contract_classification.contract_type,
                "ftw_schedule_a_contract_type_reason": ftw_contract_classification.reason,
            }
        await repo.update_filing(filing_id, filing_contract_updates)
        await repo.add_audit(
            AuditLog(
                filing_id=filing_id,
                event="FTWILLIAMS_REVIEW_PREPARED",
                message="FT Williams side-by-side comparison prepared.",
                details={
                    "send_queries": send_queries,
                    "current_query_success": current_query_success,
                    "current_year_exists": current_year_exists,
                    "bring_forward_required": bring_forward_required,
                    "plan_lookup_status": plan_lookup.status,
                    "field_count": len(comparison_fields),
                    "error": error_message,
                },
            )
        )
        return review

    async def apply_manual_plan_match(self, filing_id: str, payload: FTWilliamsManualMatchRequest) -> FTWilliamsReview:
        repo = get_repository()
        filing = await repo.get_filing(filing_id)
        if not filing:
            raise ValueError("Filing not found")
        fields = await repo.list_fields(filing_id)
        identifiers = self._extract_plan_lookup_identifiers(fields, filing)
        company_employer_id = identifiers.get("company_employer_id")
        plan_number = identifiers.get("plan_number")
        if not company_employer_id or not plan_number:
            raise ValueError("Manual FT Williams match needs extracted sponsor EIN and plan number first.")

        identity = self._manual_identity(payload)
        if not self._has_plan_identity(identity):
            raise ValueError("Enter CustomerID/PlanID or FTWCustomerID/FTWPlanID before saving the FT Williams match.")

        mapping = FTWilliamsPlanMapping(
            company_employer_id=company_employer_id,
            plan_number=plan_number,
            year=self._normalize_year(payload.year or identifiers.get("year")),
            plan_name=identifiers.get("plan_name"),
            sponsor_name=identifiers.get("sponsor_name"),
            **identity,
        )
        await repo.upsert_ftwilliams_plan_mapping(mapping)
        review = await self.prepare_review(filing_id, send_queries=False)
        await repo.add_audit(
            AuditLog(
                filing_id=filing_id,
                event="FTWILLIAMS_MANUAL_MATCH_SAVED",
                message="Manual FT Williams plan match saved for extracted EIN and plan number.",
                details={"company_employer_id": company_employer_id, "plan_number": plan_number, **identity},
            )
        )
        return review

    async def resolve_plan_year_conflict(
        self,
        filing_id: str,
        resolution: FTWilliamsPlanYearResolution | str,
    ) -> FTWilliamsReview:
        repo = get_repository()
        filing = await repo.get_filing(filing_id)
        if not filing:
            raise ValueError("Filing not found")
        review = await repo.get_ftwilliams_review(filing_id)
        if not review or not review.current_query_success:
            raise ValueError("Query current FT Williams values before resolving the plan year conflict.")
        try:
            selected_resolution = FTWilliamsPlanYearResolution(str(getattr(resolution, "value", resolution)))
        except ValueError as exc:
            raise ValueError("Choose either the Plan Worksheet dates or the current FT Williams dates.") from exc

        fields = await repo.list_fields(filing_id)
        worksheet_begin = self._field_value_by_rule(fields, "form_5500_part_i_6_plan_year_beginning_date")
        worksheet_end = self._field_value_by_rule(fields, "form_5500_part_i_7_plan_year_ending_date")
        if selected_resolution == FTWilliamsPlanYearResolution.USE_WORKSHEET:
            selected_begin, selected_end = worksheet_begin, worksheet_end
            reason = "Plan year confirmed from the Plan Worksheet."
        else:
            selected_begin = review.form_5500_current_values.get("PlanYearBeginDate")
            selected_end = review.form_5500_current_values.get("PlanYearEndDate")
            reason = "Plan year confirmed from current FT Williams values."
        if not selected_begin or not selected_end:
            raise ValueError("Both plan year beginning and ending dates are required before resolving this conflict.")

        field_by_rule = {str(field.mapped_rule_key or ""): field for field in fields}
        missing_fields: list[ExtractedField] = []
        for rule_key, label, form_type, selected_value in (
            ("form_5500_part_i_6_plan_year_beginning_date", "6. Plan Year Beginning Date", FormType.FORM_5500, selected_begin),
            ("form_5500_part_i_7_plan_year_ending_date", "7. Plan Year Ending Date", FormType.FORM_5500, selected_end),
            ("schedule_a_part_iv_4d_plan_year_beginning_date", "4d. Plan Year Beginning Date", FormType.SCHEDULE_A, selected_begin),
            ("schedule_a_part_iv_4e_plan_year_ending_date", "4e. Plan Year Ending Date", FormType.SCHEDULE_A, selected_end),
        ):
            if rule_key in field_by_rule:
                continue
            missing_fields.append(
                ExtractedField(
                    filing_id=filing_id,
                    source_field_name=label,
                    normalized_field_name=rule_key,
                    mapped_rule_key=rule_key,
                    mapped_label=label,
                    form_type=form_type,
                    source_document_type=DocumentType.PLAN_WORKSHEET,
                    priority=FieldPriority.HIGH,
                    value=selected_value,
                    proposed_value=selected_value,
                    confidence=1,
                    status=ExtractedFieldStatus.EDITED,
                    status_reason=reason,
                )
            )
        if missing_fields:
            created_fields = await repo.add_fields(missing_fields)
            fields.extend(created_fields)
            field_by_rule.update({str(field.mapped_rule_key or ""): field for field in created_fields})

        for rule_key, selected_value in (
            ("form_5500_part_i_6_plan_year_beginning_date", selected_begin),
            ("form_5500_part_i_7_plan_year_ending_date", selected_end),
            ("schedule_a_part_iv_4d_plan_year_beginning_date", selected_begin),
            ("schedule_a_part_iv_4e_plan_year_ending_date", selected_end),
        ):
            field = field_by_rule.get(rule_key)
            if not field or not field.id:
                continue
            updated = await repo.update_field(
                filing_id,
                field.id,
                selected_value,
                status=ExtractedFieldStatus.EDITED,
                status_reason=reason,
            )
            if updated:
                fields = [updated if item.id == updated.id else item for item in fields]

        review.plan_year_resolution = selected_resolution
        review.plan_year_resolution_begin = selected_begin
        review.plan_year_resolution_end = selected_end
        review = await repo.upsert_ftwilliams_review(review)
        published_rules = await FieldRuleService(repo).published_rules()
        resolved_review = await self.prepare_review(
            filing_id,
            send_queries=False,
            apply_automatic_derivations=False,
            preloaded=(filing, fields, published_rules, review),
        )
        await repo.add_audit(
            AuditLog(
                filing_id=filing_id,
                event="FTWILLIAMS_PLAN_YEAR_RESOLVED",
                message="Reviewer resolved the Form 5500 and Schedule A plan year conflict.",
                details={
                    "resolution": selected_resolution.value,
                    "plan_year_begin": selected_begin,
                    "plan_year_end": selected_end,
                },
            )
        )
        return resolved_review

    async def select_schedule_a_match(self, filing_id: str, payload: FTWilliamsScheduleAMatchRequest) -> FTWilliamsReview:
        repo = get_repository()
        published_rules = await FieldRuleService(repo).published_rules()
        filing = await repo.get_filing(filing_id)
        if not filing:
            raise ValueError("Filing not found")
        if not payload.create_new and not str(payload.ftw_seq_no or "").strip():
            raise ValueError("FTWSeqNo is required unless creating a new Schedule A.")
        fields = await repo.list_fields(filing_id)
        review = await repo.get_ftwilliams_review(filing_id)
        if not review:
            review = await self.prepare_review(filing_id, send_queries=False)

        query_identity = self._current_query_identity_from_review(review)
        update_identity = self._identity_from_review(review)
        configured = bool(self.ftwilliams.status()["configured"])
        form_5500_current = self._review_current_values(review, FormType.FORM_5500)
        schedule_a_current: dict[str, str] = {}
        schedule_a_candidates = list(review.schedule_a_candidates or [])
        schedule_a_records = list(review.schedule_a_records or [])
        query_request_xml = review.query_request_xml
        query_response_xml = review.query_response_xml
        error_message = review.error_message
        current_query_success = review.current_query_success
        schedule_a_broker_rows = self._normalized_schedule_a_broker_rows(filing.schedule_a_broker_rows or review.schedule_a_broker_rows)
        schedule_a_worksheet_summaries = self._normalized_schedule_a_worksheet_summaries(filing.schedule_a_worksheet_summaries or review.schedule_a_worksheet_summaries)

        if not payload.create_new and configured and self._has_current_query_inputs(query_identity):
            response = await self.ftwilliams.run_query(
                FTWilliamsQueryRequest(operation="query_schedule_a", send=True, ftw_seq_no=payload.ftw_seq_no, **query_identity)
            )
            query_request_xml = "\n\n".join(filter(None, [query_request_xml, response.request_xml]))
            if response.raw_response:
                query_response_xml = "\n\n".join(filter(None, [query_response_xml, response.raw_response]))
            success_status = next((status for status in response.statuses if str(status.error_code or "") == "0"), None)
            if response.success and success_status and success_status.query_results:
                if not success_status.ftw_seq_no:
                    success_status.ftw_seq_no = payload.ftw_seq_no
                selected_status = success_status
                schedule_a_current = selected_status.query_results
                update_identity = update_identity | self._identity_from_status(selected_status)
                error_message = None
                current_query_success = True
                schedule_a_candidates = self._merge_schedule_candidate_payloads(schedule_a_candidates, selected_status, fields)
                schedule_a_records = self._merge_schedule_record_payloads(schedule_a_records, selected_status)
            else:
                error_message = response.error or self._status_error(response.statuses) or "Selected FT Williams Schedule A could not be queried."

        new_schedule_desc = None
        if payload.create_new:
            schedule_a_current = {}
            new_schedule_desc = self._schedule_desc_from_payload_or_fields(payload, fields, schedule_a_records)
            schedule_a_match = {
                "ftw_seq_no": None,
                "score": None,
                "carrier": payload.carrier or self._field_value_by_rule(fields, "schedule_a_part_i_1a_name_of_insurance_company"),
                "carrier_ein": payload.carrier_ein or self._field_value_by_rule(fields, "schedule_a_part_i_1b_insurance_carrier_ein"),
                "contract": payload.contract or self._field_value_by_rule(fields, "schedule_a_part_i_1d_contract_policy_number"),
                "schedule_desc": new_schedule_desc,
                "create_new": True,
                "source": "NEW_SCHEDULE_A",
            }
            error_message = None if current_query_success else error_message
        else:
            schedule_a_match = {
                "ftw_seq_no": payload.ftw_seq_no,
                "score": None,
                "carrier": payload.carrier or schedule_a_current.get("InsCarrierName") or schedule_a_current.get("INS_CARRIER_NAME"),
                "carrier_ein": payload.carrier_ein or schedule_a_current.get("InsCarrierEIN") or schedule_a_current.get("INS_CARRIER_EIN"),
                "contract": payload.contract or schedule_a_current.get("InsContractNum") or schedule_a_current.get("INS_CONTRACT_NUM"),
                "schedule_desc": schedule_a_current.get("ScheduleDesc") or schedule_a_current.get("SCHEDULE_DESC") or payload.schedule_desc,
                "source": "MANUAL",
            }
        previous_broker_matches = (
            review.schedule_a_broker_matches
            if self._same_schedule_a_selection(review.schedule_a_match, schedule_a_match)
            else []
        )
        broker_matches, resolved_broker_rows = self._resolve_schedule_a_brokers(
            schedule_a_broker_rows,
            schedule_a_records,
            payload.ftw_seq_no,
            previous_broker_matches,
            create_new=payload.create_new,
        )
        broker_match_complete = all(match.resolved for match in broker_matches)
        broker_match_blocked = bool(schedule_a_broker_rows) and not broker_match_complete
        if broker_match_blocked:
            error_message = "; ".join(filter(None, [
                error_message,
                "Schedule A broker rows need confirmation before FT Williams can be updated.",
            ]))
        fields = self._fields_with_schedule_a_summary_override(
            fields,
            schedule_a_worksheet_summaries,
            new_schedule_desc if payload.create_new else schedule_a_match.get("schedule_desc"),
        )
        plan_year_conflict = self._plan_year_conflict(fields, form_5500_current, schedule_a_current)
        plan_year_resolution = self._effective_plan_year_resolution(review, fields, form_5500_current, schedule_a_current)
        plan_year_update_confirmed = plan_year_resolution is not None
        form_5500_block_reason = None if plan_year_update_confirmed else self._form_5500_update_block_reason(fields, form_5500_current)
        schedule_a_block_reason = None if plan_year_update_confirmed else self._schedule_a_update_block_reason(fields, schedule_a_current)
        if form_5500_block_reason:
            error_message = "; ".join(filter(None, [error_message, form_5500_block_reason]))
        if schedule_a_block_reason:
            error_message = "; ".join(filter(None, [error_message, schedule_a_block_reason]))
        candidate_form_5500_fields = self._safe_update_fields(fields, FormType.FORM_5500, form_5500_current)
        safe_form_5500_fields = [] if form_5500_block_reason else candidate_form_5500_fields
        automatic_field_state = self._automatic_field_state(fields)
        computed_contract_classification = apply_schedule_a_classification(
            fields,
            filing.schedule_a_classification_signals,
        )
        await self._persist_automatic_field_changes(repo, filing_id, automatic_field_state, fields)
        extracted_contract_classification = self._effective_schedule_a_classification(filing, computed_contract_classification)
        ftw_contract_classification = classify_schedule_a_current(schedule_a_current) if schedule_a_current else None
        contract_type_mismatch = bool(
            ftw_contract_classification
            and extracted_contract_classification.contract_type != ftw_contract_classification.contract_type
            and extracted_contract_classification.contract_type.value != "UNKNOWN"
            and ftw_contract_classification.contract_type.value != "UNKNOWN"
        )
        safe_schedule_a_fields = filter_schedule_a_fields_for_contract_type(
            self._safe_update_fields(
                fields,
                FormType.SCHEDULE_A,
                schedule_a_current,
                has_structured_schedule_a_brokers=bool(schedule_a_broker_rows),
            ),
            extracted_contract_classification.contract_type,
            rules=published_rules,
        )
        comparison_fields = self._comparison_fields(
            fields,
            form_5500_current,
            schedule_a_current,
            update_fields=[*candidate_form_5500_fields, *safe_schedule_a_fields],
            schedule_a_contract_type=extracted_contract_classification.contract_type,
        )
        self._mark_structured_broker_comparisons(comparison_fields, schedule_a_broker_rows)
        include_5500_update = self._should_build_update_payload(review.current_query_sent, form_5500_current)
        include_schedule_a_update = self._should_build_update_payload(review.current_query_sent, schedule_a_current) or (
            payload.create_new and review.current_query_sent and bool(schedule_a_records)
        )
        payload_validation_issues: list[FTWFieldValidationIssue] = []
        try:
            update_xml_5500 = build_single_document_update_xml(
                "DOL5500Data",
                safe_form_5500_fields if include_5500_update else [],
                FormType.FORM_5500,
                transaction_type="1",
                current_values=form_5500_current,
                **update_identity,
            )
        except FTWPayloadValidationError as exc:
            payload_validation_issues.extend(exc.issues)
            update_xml_5500 = ""
        try:
            update_xml_schedule_a = self._build_schedule_a_update_xml(
                safe_schedule_a_fields if include_schedule_a_update else [],
                schedule_a_records,
                payload.ftw_seq_no,
                update_identity,
                all_record_fields=(
                    self._schedule_a_plan_year_fields(safe_schedule_a_fields)
                    if plan_year_update_confirmed
                    else []
                ),
                schedule_update_blocked=bool(schedule_a_block_reason) or broker_match_blocked,
                add_new_schedule_a=payload.create_new,
                new_schedule_desc=new_schedule_desc,
                schedule_a_broker_rows=resolved_broker_rows,
            )
        except FTWPayloadValidationError as exc:
            payload_validation_issues.extend(exc.issues)
            update_xml_schedule_a = ""
        if payload_validation_issues:
            validation_message = str(FTWPayloadValidationError(payload_validation_issues))
            error_message = "; ".join(filter(None, [error_message, validation_message]))
        proposed_xml = combine_ftw_update_xml(update_xml_5500, update_xml_schedule_a)
        await repo.update_filing(filing_id, {"proposed_xml": proposed_xml})

        updated_review = FTWilliamsReview(
            **{
                **review.model_dump(exclude={"id", "created_at", "updated_at"}),
                "status": FTWilliamsReviewStatus.CURRENT_QUERIED if current_query_success else FTWilliamsReviewStatus.PREVIEW_READY,
                "current_query_success": current_query_success,
                "plan_year_conflict": plan_year_conflict,
                "plan_year_resolution": plan_year_resolution,
                "plan_year_resolution_begin": review.plan_year_resolution_begin if plan_year_resolution else None,
                "plan_year_resolution_end": review.plan_year_resolution_end if plan_year_resolution else None,
                "schedule_a_match": schedule_a_match,
                "schedule_a_candidates": schedule_a_candidates,
                "schedule_a_records": schedule_a_records,
                "schedule_a_broker_rows": schedule_a_broker_rows,
                "schedule_a_broker_matches": broker_matches,
                "schedule_a_broker_match_complete": broker_match_complete,
                "schedule_a_worksheet_summaries": schedule_a_worksheet_summaries,
                "schedule_a_contract_type": extracted_contract_classification.contract_type,
                "schedule_a_contract_type_reason": extracted_contract_classification.reason,
                "schedule_a_contract_type_confirmed": True,
                "schedule_a_contract_type_confidence": extracted_contract_classification.confidence,
                "schedule_a_contract_type_evidence": list(extracted_contract_classification.evidence),
                "ftw_schedule_a_contract_type": ftw_contract_classification.contract_type if ftw_contract_classification else review.ftw_schedule_a_contract_type,
                "ftw_schedule_a_contract_type_reason": ftw_contract_classification.reason if ftw_contract_classification else review.ftw_schedule_a_contract_type_reason,
                "schedule_a_contract_type_mismatch": contract_type_mismatch,
                "query_request_xml": query_request_xml,
                "query_response_xml": query_response_xml,
                "form_5500_current_values": form_5500_current,
                "schedule_a_current_values": schedule_a_current,
                "update_xml_5500": update_xml_5500,
                "update_xml_schedule_a": update_xml_schedule_a,
                "error_message": error_message,
                "client_error": self._normalize_review_error(error_message, comparison_fields),
                "fields": comparison_fields,
                **update_identity,
                "ftw_seq_no": payload.ftw_seq_no,
            }
        )
        updated_review.id = review.id
        updated_review.created_at = review.created_at
        updated_review = await repo.upsert_ftwilliams_review(updated_review)
        filing_updates = {
            "schedule_a_contract_type": extracted_contract_classification.contract_type,
            "schedule_a_contract_type_reason": extracted_contract_classification.reason,
            "schedule_a_contract_type_confirmed": True,
            "schedule_a_contract_type_confidence": extracted_contract_classification.confidence,
            "schedule_a_contract_type_evidence": list(extracted_contract_classification.evidence),
        }
        classification_relevant_fields = filter_schedule_a_fields_for_contract_type(
            fields,
            extracted_contract_classification.contract_type,
            rules=published_rules,
        )
        filing_updates |= self._review_field_count_updates(fields, classification_relevant_fields)
        if ftw_contract_classification:
            filing_updates |= {
                "ftw_schedule_a_contract_type": ftw_contract_classification.contract_type,
                "ftw_schedule_a_contract_type_reason": ftw_contract_classification.reason,
            }
        await repo.update_filing(filing_id, filing_updates)
        await repo.add_audit(
            AuditLog(
                filing_id=filing_id,
                event="FTWILLIAMS_SCHEDULE_A_MATCH_SELECTED",
                message="Reviewer selected the FT Williams Schedule A sequence." if not payload.create_new else "Reviewer marked the uploaded Schedule A as a new FT Williams Schedule A.",
                details=schedule_a_match,
            )
        )
        return updated_review

    async def set_schedule_a_broker_matches(
        self,
        filing_id: str,
        payload: FTWilliamsBrokerMatchesRequest,
    ) -> FTWilliamsReview:
        repo = get_repository()
        if not await repo.get_filing(filing_id):
            raise ValueError("Filing not found")
        review = await repo.get_ftwilliams_review(filing_id)
        if not review:
            review = await self.prepare_review(filing_id, send_queries=False)

        seen_extracted: set[int] = set()
        seen_ftw: set[int] = set()
        decisions: list[ScheduleABrokerMatch] = []
        for decision in payload.decisions:
            if decision.extracted_index in seen_extracted:
                raise ValueError(f"Extracted broker row {decision.extracted_index + 1} was submitted more than once.")
            seen_extracted.add(decision.extracted_index)
            if decision.create_new:
                decisions.append(
                    ScheduleABrokerMatch(
                        extracted_index=decision.extracted_index,
                        status="CONFIRMED_NEW",
                        resolved=True,
                        reason="Reviewer confirmed this is a new FT Williams broker row.",
                    )
                )
                continue
            if decision.ftw_index is None:
                raise ValueError("Each broker decision must select an FT Williams row or add a new row.")
            if decision.ftw_index in seen_ftw:
                raise ValueError(f"FT Williams broker row {decision.ftw_index + 1} was assigned more than once.")
            seen_ftw.add(decision.ftw_index)
            decisions.append(
                ScheduleABrokerMatch(
                    extracted_index=decision.extracted_index,
                    ftw_index=decision.ftw_index,
                    status="CONFIRMED",
                    resolved=True,
                    reason="Reviewer confirmed the FT Williams broker row.",
                )
            )

        review.schedule_a_broker_matches = decisions
        await repo.upsert_ftwilliams_review(review)
        updated_review = await self.prepare_review(filing_id, send_queries=False)
        await repo.add_audit(
            AuditLog(
                filing_id=filing_id,
                event="FTWILLIAMS_SCHEDULE_A_BROKERS_MATCHED",
                message="Schedule A broker row decisions saved.",
                details={"decision_count": len(decisions)},
            )
        )
        return updated_review

    async def update_schedule_a_broker_rows(
        self,
        filing_id: str,
        payload: FTWilliamsScheduleABrokerRowsRequest,
    ) -> FTWilliamsReview:
        """Replace reviewer-visible broker rows after validating the exact FTW payload."""
        repo = get_repository()
        filing = await repo.get_filing(filing_id)
        if not filing:
            raise ValueError("Filing not found")

        rows = self._normalized_schedule_a_broker_rows(payload.rows)
        try:
            # A reviewer may correct incomplete rows one at a time. Validate all
            # values that are present here, but enforce required fields only in
            # the final FT Williams payload-building/send path.
            schedule_a_broker_update_values(rows, require_complete=False)
        except FTWPayloadValidationError as exc:
            raise ValueError(self._friendly_broker_validation_error(exc)) from exc

        await repo.update_filing(filing_id, {"schedule_a_broker_rows": rows, "error_message": None})
        review = await repo.get_ftwilliams_review(filing_id)
        if review:
            # Row indexes may change after an edit or exclusion. Re-resolve them
            # instead of carrying a decision to the wrong FT Williams row.
            review.schedule_a_broker_matches = []
            await repo.upsert_ftwilliams_review(review)
        updated_review = await self.prepare_review(filing_id, send_queries=False)
        await repo.add_audit(
            AuditLog(
                filing_id=filing_id,
                event="FTWILLIAMS_SCHEDULE_A_BROKER_ROWS_UPDATED",
                message="Reviewer edited the Schedule A broker rows used for the FT Williams update.",
                details={"broker_row_count": len(rows)},
            )
        )
        return updated_review

    def _friendly_broker_validation_error(
        self,
        error: FTWPayloadValidationError,
    ) -> str:
        if not error.issues:
            return "The broker rows are not valid for FT Williams."
        issue = error.issues[0]
        match = re.fullmatch(
            r"(Name|AddressLine1|AddressLine2|City|State|ZipCode|CommPdAmt|FeesPdAmt|FeesPdText|Code)(\d+|XX)",
            issue.tag,
        )
        field_name = {
            "Name": "Broker / person",
            "AddressLine1": "Address line 1",
            "AddressLine2": "Address line 2",
            "City": "City",
            "State": "State",
            "ZipCode": "ZIP code",
            "CommPdAmt": "Commission",
            "FeesPdAmt": "Fee",
            "FeesPdText": "Purpose",
            "Code": "Organization code",
        }.get(match.group(1) if match else issue.tag, issue.tag)
        row_number = int(match.group(2)) if match and match.group(2).isdigit() else 1
        return f"Broker row {row_number} - {field_name}: {issue.reason}. Current value: {issue.value}"

    async def set_schedule_a_contract_type(
        self,
        filing_id: str,
        payload: FTWilliamsScheduleAContractTypeRequest,
    ) -> FTWilliamsReview:
        repo = get_repository()
        published_rules = await FieldRuleService(repo).published_rules()
        filing = await repo.get_filing(filing_id)
        if not filing:
            raise ValueError("Filing not found")
        if payload.contract_type not in {
            ScheduleAContractType.EXPERIENCE_RATED,
            ScheduleAContractType.NONEXPERIENCE_RATED,
            ScheduleAContractType.NEEDS_REVIEW,
        }:
            raise ValueError("Choose experience-rated, nonexperience-rated, or needs review.")

        confirmed = payload.contract_type in {
            ScheduleAContractType.EXPERIENCE_RATED,
            ScheduleAContractType.NONEXPERIENCE_RATED,
        }
        reason = (payload.reason or "").strip() or (
            "Reviewer confirmed Schedule A contract type."
            if confirmed
            else "Reviewer marked Schedule A contract type as needing review."
        )
        await repo.update_filing(
            filing_id,
            {
                "schedule_a_contract_type": payload.contract_type,
                "schedule_a_contract_type_reason": reason,
                "schedule_a_contract_type_confirmed": confirmed,
                "error_message": None,
            },
        )
        fields = await repo.list_fields(filing_id)
        relevant_fields = filter_schedule_a_fields_for_contract_type(fields, payload.contract_type, rules=published_rules)
        await repo.update_filing(
            filing_id,
            self._review_field_count_updates(fields, relevant_fields),
        )
        # Rebuild the complete preview and replace-style XML. Mutating only the
        # comparison flags can leave an opposite contract-type tag in stale XML.
        updated_review = await self.prepare_review(filing_id, send_queries=False)
        await repo.add_audit(
            AuditLog(
                filing_id=filing_id,
                event="SCHEDULE_A_CONTRACT_TYPE_CONFIRMED" if confirmed else "SCHEDULE_A_CONTRACT_TYPE_NEEDS_REVIEW",
                message="Reviewer updated the Schedule A contract classification.",
                details={"contract_type": payload.contract_type, "reason": reason},
            )
        )
        return updated_review

    def _review_field_count_updates(
        self,
        all_fields: list[ExtractedField],
        relevant_fields: list[ExtractedField],
    ) -> dict:
        review_fields = [field for field in relevant_fields if field.priority != FieldPriority.IGNORE]
        found_fields = [
            field
            for field in review_fields
            if field.status not in {ExtractedFieldStatus.MISSING, ExtractedFieldStatus.UNMAPPED}
            and str(field.proposed_value or field.value or "").strip()
        ]
        missing_high = len(
            [field for field in review_fields if field.status == ExtractedFieldStatus.MISSING and field.priority == FieldPriority.HIGH]
        )
        missing_medium = len(
            [field for field in review_fields if field.status == ExtractedFieldStatus.MISSING and field.priority == FieldPriority.MEDIUM]
        )
        missing_low = len(
            [field for field in review_fields if field.status == ExtractedFieldStatus.MISSING and field.priority == FieldPriority.LOW]
        )
        low_confidence = len([field for field in review_fields if field.status == ExtractedFieldStatus.LOW_CONFIDENCE])
        unmapped = len([field for field in review_fields if field.status == ExtractedFieldStatus.UNMAPPED])
        return {
            "review_field_count": len(review_fields),
            "found_field_count": len(found_fields),
            "excluded_field_count": max(0, len(all_fields) - len(relevant_fields)),
            "missing_high_priority_count": missing_high,
            "missing_medium_priority_count": missing_medium,
            "missing_low_priority_count": missing_low,
            "low_confidence_count": low_confidence,
            "unmapped_count": unmapped,
        }

    @staticmethod
    def _automatic_field_state(fields: list[ExtractedField]) -> dict[str, tuple[str, ExtractedFieldStatus, str | None]]:
        return {
            field.id: (field.proposed_value, field.status, field.status_reason)
            for field in fields
            if field.id
        }

    @staticmethod
    async def _persist_automatic_field_changes(
        repo,
        filing_id: str,
        before: dict[str, tuple[str, ExtractedFieldStatus, str | None]],
        fields: list[ExtractedField],
    ) -> None:
        for field in fields:
            if not field.id:
                continue
            current = (field.proposed_value, field.status, field.status_reason)
            if before.get(field.id) == current:
                continue
            await repo.update_field(
                filing_id,
                field.id,
                field.proposed_value,
                status=field.status,
                status_reason=field.status_reason,
            )

    async def send_approved_update(self, filing_id: str, payload: FTWilliamsSendUpdateRequest) -> FTWilliamsReview | None:
        repo = get_repository()
        filing = await repo.get_filing(filing_id)
        if not filing:
            raise ValueError("Filing not found")
        review = await repo.get_ftwilliams_review(filing_id)
        retrying_failed_ftw_update = bool(
            filing.status == FilingStatus.FAILED
            and review
            and (
                review.active_failure
                or review.failure_dismissed_at is not None
                or review.status in {
                    FTWilliamsReviewStatus.UPDATE_FAILED,
                    FTWilliamsReviewStatus.UPDATE_UNKNOWN,
                }
            )
        )
        if filing.status != FilingStatus.APPROVED and not retrying_failed_ftw_update:
            raise ValueError("Approve the filing before sending approved values to FT Williams.")
        return await self.approve_and_update(
            filing_id,
            reason=payload.reason,
            send_to_ftw=True,
            refresh_current_before_update=payload.refresh_current_before_update,
            run_edit_checks=payload.run_edit_checks,
        )

    async def dismiss_active_failure(self, filing_id: str, reason: str) -> FTWilliamsReview:
        repo = get_repository()
        review = await repo.get_ftwilliams_review(filing_id)
        if not review:
            raise ValueError("FT Williams review not found")
        legacy_active = bool(
            review.failure_dismissed_at is None
            and review.status in {
                FTWilliamsReviewStatus.UPDATE_FAILED,
                FTWilliamsReviewStatus.UPDATE_UNKNOWN,
            }
        )
        if not review.active_failure and not legacy_active:
            unresolved_audits = await repo.list_unresolved_ftwilliams_failure_audits()
            legacy_active = any(audit.filing_id == filing_id for audit in unresolved_audits)
        if not review.active_failure and not legacy_active:
            raise ValueError("No active FT Williams failure to dismiss")
        dismissed_reason = reason.strip() or "Dismissed by operator"
        review.active_failure = False
        review.active_failure_type = None
        review.active_failure_issue_count = None
        review.active_failure_issue_groups = []
        review.failure_dismissed_at = datetime.utcnow()
        review.failure_dismissed_reason = dismissed_reason
        await repo.upsert_ftwilliams_review(review)
        await repo.add_audit(
            AuditLog(
                filing_id=filing_id,
                event="FTWILLIAMS_UPDATE_FAILURE_DISMISSED",
                message="FT Williams update failure was manually dismissed from the active queue.",
                details={
                    "reason": dismissed_reason,
                    "error": review.active_failure_reason or review.error_message,
                    "error_code": (
                        review.active_failure_client_error.code
                        if review.active_failure_client_error
                        else review.client_error.code if review.client_error else None
                    ),
                },
            )
        )
        return review

    async def _prepare_plan_lookup(
        self,
        filing,
        fields: list[ExtractedField],
        *,
        send_queries: bool,
        configured: bool,
    ) -> FTWilliamsPlanLookup:
        identifiers = self._extract_plan_lookup_identifiers(fields, filing)
        lookup = FTWilliamsPlanLookup(**identifiers)

        if not lookup.company_employer_id or not lookup.plan_number:
            lookup.status = FTWilliamsPlanLookupStatus.MISSING_IDENTIFIERS
            lookup.error_message = "Plan lookup needs sponsor EIN and plan number from Schedule A or the plan worksheet."
            return lookup

        repo = get_repository()
        derived_identity = self._derived_customer_plan_identity(lookup)
        lookup.matched_identity = derived_identity or None
        if not derived_identity:
            lookup.status = FTWilliamsPlanLookupStatus.MISSING_IDENTIFIERS
            lookup.error_message = "Plan lookup needs sponsor EIN and plan number before deriving FT Williams CustomerID/PlanID."
            return lookup

        stored_mapping = await repo.get_ftwilliams_plan_mapping(lookup.company_employer_id, lookup.plan_number)
        if stored_mapping:
            lookup.year = stored_mapping.year or lookup.year
            mapping_identity = self._identity_from_mapping(stored_mapping)
            lookup.matches = [self._mapping_match(stored_mapping)]
            lookup.matched_identity = {**derived_identity, **mapping_identity}
            lookup.status = FTWilliamsPlanLookupStatus.MATCHED
            lookup.error_message = None
            return lookup

        payload = FTWilliamsQueryRequest(
            operation="query_plan",
            customer_id=derived_identity["customer_id"],
            plan_id=derived_identity["plan_id"],
            send=send_queries,
        )
        try:
            lookup.request_xml = self.ftwilliams.mask_key_id(self.ftwilliams.build_request_xml(payload))
        except ValueError as exc:
            lookup.status = FTWilliamsPlanLookupStatus.FAILED
            lookup.error_message = str(exc)
            return lookup

        if not send_queries:
            lookup.status = FTWilliamsPlanLookupStatus.REQUEST_READY
            return lookup

        if not configured:
            lookup.status = FTWilliamsPlanLookupStatus.FAILED
            lookup.error_message = "FT Williams endpoint and KeyID must be configured before plan lookup."
            return lookup

        response = await self.ftwilliams.run_query(payload)
        lookup.request_xml = response.request_xml
        lookup.response_xml = response.raw_response

        if response.error:
            archive_error = await self._try_archive_plan_lookup(lookup, derived_identity, repo)
            if lookup.status == FTWilliamsPlanLookupStatus.MATCHED:
                return lookup
            lookup.status = FTWilliamsPlanLookupStatus.FAILED
            lookup.error_message = "; ".join(filter(None, [response.error, archive_error]))
            return lookup
        if response.statuses and not response.success:
            plan_error = (
                self._status_error(response.statuses)
                or "No FT Williams plan matched the derived CustomerID/PlanID from extracted EIN and plan number."
            )
            fallback_error = await self._try_same_customer_plan_lookup(lookup, derived_identity, repo)
            if lookup.status == FTWilliamsPlanLookupStatus.MATCHED:
                return lookup
            archive_error = await self._try_archive_plan_lookup(lookup, derived_identity, repo)
            if lookup.status == FTWilliamsPlanLookupStatus.MATCHED:
                return lookup
            batch_error = await self._try_plan_ids_batch_lookup(lookup, repo)
            if lookup.status == FTWilliamsPlanLookupStatus.MATCHED:
                return lookup
            if lookup.status != FTWilliamsPlanLookupStatus.MULTIPLE_MATCHES:
                lookup.status = FTWilliamsPlanLookupStatus.NOT_FOUND
            lookup.error_message = "; ".join(filter(None, [plan_error, fallback_error, archive_error, batch_error]))
            return lookup
        if not response.raw_response:
            archive_error = await self._try_archive_plan_lookup(lookup, derived_identity, repo)
            if lookup.status == FTWilliamsPlanLookupStatus.MATCHED:
                return lookup
            batch_error = await self._try_plan_ids_batch_lookup(lookup, repo)
            if lookup.status == FTWilliamsPlanLookupStatus.MATCHED:
                return lookup
            lookup.status = FTWilliamsPlanLookupStatus.FAILED
            lookup.error_message = "; ".join(
                filter(None, ["FT Williams plan lookup did not return a response.", archive_error, batch_error])
            )
            return lookup

        if not response.success or not response.statuses:
            plan_error = (
                self._status_error(response.statuses)
                or "No FT Williams plan matched the derived CustomerID/PlanID from extracted EIN and plan number."
            )
            fallback_error = await self._try_same_customer_plan_lookup(lookup, derived_identity, repo)
            if lookup.status == FTWilliamsPlanLookupStatus.MATCHED:
                return lookup
            archive_error = await self._try_archive_plan_lookup(lookup, derived_identity, repo)
            if lookup.status == FTWilliamsPlanLookupStatus.MATCHED:
                return lookup
            batch_error = await self._try_plan_ids_batch_lookup(lookup, repo)
            if lookup.status == FTWilliamsPlanLookupStatus.MATCHED:
                return lookup
            if lookup.status != FTWilliamsPlanLookupStatus.MULTIPLE_MATCHES:
                lookup.status = FTWilliamsPlanLookupStatus.NOT_FOUND
            lookup.error_message = "; ".join(filter(None, [plan_error, fallback_error, archive_error, batch_error]))
            return lookup

        success_status = next((status for status in response.statuses if str(status.error_code or "") == "0"), response.statuses[0])
        lookup.matches = [self._plan_status_match(success_status, lookup, derived_identity)]
        lookup.matched_identity = {
            **derived_identity,
            **self._identity_from_status(success_status),
        }
        if self._has_plan_identity(lookup.matched_identity or {}):
            lookup.status = FTWilliamsPlanLookupStatus.MATCHED
            lookup.error_message = None
            return lookup

        lookup.status = FTWilliamsPlanLookupStatus.FOUND_NO_FTW_IDS
        lookup.error_message = "PlanData lookup succeeded, but FT Williams did not return usable plan identifiers."
        return lookup

    def _should_preserve_authoritative_plan_lookup(
        self,
        prepared: FTWilliamsPlanLookup,
        existing: FTWilliamsPlanLookup,
    ) -> bool:
        authoritative_statuses = {
            FTWilliamsPlanLookupStatus.MATCHED,
            FTWilliamsPlanLookupStatus.FOUND_NO_FTW_IDS,
            FTWilliamsPlanLookupStatus.MULTIPLE_MATCHES,
            FTWilliamsPlanLookupStatus.NOT_FOUND,
            FTWilliamsPlanLookupStatus.FAILED,
        }
        if prepared.status != FTWilliamsPlanLookupStatus.REQUEST_READY:
            return False
        if existing.status not in authoritative_statuses:
            return False
        return (
            self._normalize_ein_digits(prepared.company_employer_id)
            == self._normalize_ein_digits(existing.company_employer_id)
            and self._normalize_plan_number(prepared.plan_number)
            == self._normalize_plan_number(existing.plan_number)
            and self._normalize_year(prepared.year) == self._normalize_year(existing.year)
        )

    def _authoritative_plan_lookup_error(self, lookup: FTWilliamsPlanLookup) -> str | None:
        if lookup.status in {
            FTWilliamsPlanLookupStatus.FOUND_NO_FTW_IDS,
            FTWilliamsPlanLookupStatus.MULTIPLE_MATCHES,
            FTWilliamsPlanLookupStatus.NOT_FOUND,
            FTWilliamsPlanLookupStatus.FAILED,
        }:
            return lookup.error_message
        return None

    async def _shared_plan_lookup(
        self,
        filing,
        fields: list[ExtractedField],
        *,
        configured: bool,
    ) -> FTWilliamsPlanLookup:
        """Single-flight identical plan lookups during bulk automatic intake."""
        identifiers = self._extract_plan_lookup_identifiers(fields, filing)
        settings = get_settings()
        key = (
            str(settings.ftwlink_endpoint_url or ""),
            str(identifiers.get("company_employer_id") or ""),
            str(identifiers.get("plan_number") or ""),
            str(identifiers.get("year") or ""),
        )
        now = time.monotonic()
        ttl = max(0, settings.ftw_snapshot_ttl_seconds)
        cached = _PLAN_LOOKUP_CACHE.get(key)
        if cached and now - cached[0] <= ttl:
            return deepcopy(cached[1])

        if key in _PLAN_LOOKUP_INFLIGHT:
            return deepcopy(await _PLAN_LOOKUP_INFLIGHT[key])

        task = asyncio.create_task(
            self._prepare_plan_lookup(
                filing,
                fields,
                send_queries=True,
                configured=configured,
            )
        )
        _PLAN_LOOKUP_INFLIGHT[key] = task
        try:
            lookup = await task
            if lookup.status == FTWilliamsPlanLookupStatus.MATCHED:
                _PLAN_LOOKUP_CACHE[key] = (time.monotonic(), deepcopy(lookup))
            return deepcopy(lookup)
        finally:
            if _PLAN_LOOKUP_INFLIGHT.get(key) is task:
                _PLAN_LOOKUP_INFLIGHT.pop(key, None)

    async def _try_same_customer_plan_lookup(
        self,
        lookup: FTWilliamsPlanLookup,
        derived_identity: dict[str, str],
        repo,
    ) -> str | None:
        customer_id = derived_identity.get("customer_id")
        if not customer_id or derived_identity.get("plan_id") == customer_id:
            return None

        payload = FTWilliamsQueryRequest(
            operation="query_plan",
            customer_id=customer_id,
            plan_id=customer_id,
            send=True,
        )
        try:
            request_xml = self.ftwilliams.mask_key_id(self.ftwilliams.build_request_xml(payload))
        except ValueError as exc:
            return str(exc)

        lookup.request_xml = "\n\n".join(filter(None, [lookup.request_xml, request_xml]))
        response = await self.ftwilliams.run_query(payload)
        if response.request_xml and response.request_xml != request_xml:
            lookup.request_xml = "\n\n".join(filter(None, [lookup.request_xml, response.request_xml]))
        if response.raw_response:
            lookup.response_xml = "\n\n".join(filter(None, [lookup.response_xml, response.raw_response]))

        if response.error:
            return response.error
        if not response.success or not response.statuses:
            return self._status_error(response.statuses) or "PlanData fallback with CustomerID as PlanID did not find a matching plan."

        success_status = next((status for status in response.statuses if str(status.error_code or "") == "0"), response.statuses[0])
        match = self._plan_status_match(success_status, lookup, {"customer_id": customer_id, "plan_id": customer_id})
        lookup.matches = [match]
        lookup.matched_identity = {
            "customer_id": customer_id,
            "plan_id": customer_id,
            **self._identity_from_status(success_status),
        }
        if not self._has_plan_identity(lookup.matched_identity or {}):
            return "PlanData fallback found the plan but did not return usable identifiers."

        lookup.status = FTWilliamsPlanLookupStatus.MATCHED
        lookup.error_message = None
        await self._persist_plan_mapping(lookup, repo, match, source="PLAN_ID_FALLBACK")
        return None

    async def _try_archive_plan_lookup(
        self,
        lookup: FTWilliamsPlanLookup,
        derived_identity: dict[str, str],
        repo,
    ) -> str | None:
        payload = FTWilliamsQueryRequest(
            operation="archive_5500_get_data",
            company_employer_id=lookup.company_employer_id,
            plan_number=lookup.plan_number,
            send=True,
        )
        try:
            request_xml = self.ftwilliams.mask_key_id(self.ftwilliams.build_request_xml(payload))
        except ValueError as exc:
            return str(exc)

        lookup.request_xml = "\n\n".join(filter(None, [lookup.request_xml, request_xml]))
        response = await self.ftwilliams.run_query(payload)
        if response.request_xml and response.request_xml != request_xml:
            lookup.request_xml = "\n\n".join(filter(None, [lookup.request_xml, response.request_xml]))
        if response.raw_response:
            lookup.response_xml = "\n\n".join(filter(None, [lookup.response_xml, response.raw_response]))

        if response.error:
            return response.error
        if not response.raw_response:
            return "Archive5500 lookup did not return a response."

        matches = self.ftwilliams.parse_archive_lookup_response(response.raw_response)
        lookup.matches = self._plan_lookup_matches(matches, lookup)
        if not lookup.matches:
            name_lookup_error = await self._try_archive_name_lookup(lookup, derived_identity, repo)
            if lookup.status == FTWilliamsPlanLookupStatus.MATCHED:
                return None
            archive_error = self._status_error(response.statuses) or "Archive5500 lookup did not find a matching plan."
            return "; ".join(filter(None, [archive_error, name_lookup_error]))
        if len(lookup.matches) > 1:
            lookup.status = FTWilliamsPlanLookupStatus.MULTIPLE_MATCHES
            return "Archive5500 lookup returned multiple possible plan matches."

        match = lookup.matches[0]
        match_identity = self._identity_from_lookup_match(match)
        lookup.matched_identity = {**derived_identity, **match_identity}
        if not self._has_plan_identity(lookup.matched_identity or {}):
            return "Archive5500 lookup found the plan but did not return usable CustomerID/PlanID or FTW IDs."

        lookup.status = FTWilliamsPlanLookupStatus.MATCHED
        lookup.error_message = None
        await self._persist_plan_mapping(lookup, repo, match, source="ARCHIVE_LOOKUP")
        return None

    async def _try_archive_name_lookup(
        self,
        lookup: FTWilliamsPlanLookup,
        derived_identity: dict[str, str],
        repo,
    ) -> str | None:
        company_names = self._company_name_candidates(lookup)
        if not company_names:
            return "Archive5500 name lookup needs a sponsor or plan name."

        errors: list[str] = []
        for company_name in company_names:
            payload = FTWilliamsQueryRequest(
                operation="archive_5500_ein_lookup",
                company_name=company_name,
                company_state=lookup.company_state,
                send=True,
            )
            try:
                request_xml = self.ftwilliams.mask_key_id(self.ftwilliams.build_request_xml(payload))
            except ValueError as exc:
                errors.append(str(exc))
                continue

            lookup.request_xml = "\n\n".join(filter(None, [lookup.request_xml, request_xml]))
            response = await self.ftwilliams.run_query(payload)
            if response.request_xml and response.request_xml != request_xml:
                lookup.request_xml = "\n\n".join(filter(None, [lookup.request_xml, response.request_xml]))
            if response.raw_response:
                lookup.response_xml = "\n\n".join(filter(None, [lookup.response_xml, response.raw_response]))

            if response.error:
                errors.append(response.error)
                continue
            if not response.raw_response:
                errors.append(f"Archive5500 name lookup for {company_name!r} did not return a response.")
                continue

            matches = self.ftwilliams.parse_archive_lookup_response(response.raw_response)
            lookup.matches = self._plan_lookup_matches(matches, lookup)
            if not lookup.matches:
                errors.append(
                    self._status_error(response.statuses)
                    or f"Archive5500 name lookup for {company_name!r} did not find a matching plan."
                )
                continue
            if len(lookup.matches) > 1:
                lookup.status = FTWilliamsPlanLookupStatus.MULTIPLE_MATCHES
                return f"Archive5500 name lookup for {company_name!r} returned multiple possible plan matches."

            match = lookup.matches[0]
            match_identity = self._identity_from_lookup_match(match)
            lookup.matched_identity = {**derived_identity, **match_identity}
            if not self._has_plan_identity(lookup.matched_identity or {}):
                errors.append(
                    f"Archive5500 name lookup for {company_name!r} found the plan but did not return usable CustomerID/PlanID or FTW IDs."
                )
                continue

            lookup.status = FTWilliamsPlanLookupStatus.MATCHED
            lookup.error_message = None
            await self._persist_plan_mapping(lookup, repo, match, source="ARCHIVE_NAME_LOOKUP")
            return None

        return "; ".join(errors) if errors else "Archive5500 name lookup did not find a matching plan."

    async def _try_plan_ids_batch_lookup(self, lookup: FTWilliamsPlanLookup, repo) -> str | None:
        """Resolve real FT Williams IDs without assuming CustomerID equals EIN.

        CustomerID and PlanID are user-defined in FT Williams. PlanIDs_Batch
        is therefore the authoritative discovery fallback when legacy lookups
        cannot locate the extracted EIN/plan number.
        """
        payload = FTWilliamsQueryRequest(operation="plan_ids_batch", send=True)
        try:
            request_xml = self.ftwilliams.mask_key_id(self.ftwilliams.build_request_xml(payload))
        except ValueError as exc:
            return str(exc)

        lookup.request_xml = "\n\n".join(filter(None, [lookup.request_xml, request_xml]))
        response = await self.ftwilliams.run_query(payload)
        if response.request_xml and response.request_xml != request_xml:
            lookup.request_xml = "\n\n".join(filter(None, [lookup.request_xml, response.request_xml]))
        if response.error:
            return response.error
        if not response.raw_response:
            return "PlanIDs_Batch did not return a response."

        records = self.ftwilliams.parse_plan_ids_batch_response(response.raw_response)
        if not records:
            return self._status_error(response.statuses) or "PlanIDs_Batch returned no accessible plans."

        direct_matches = [record for record in records if self._plan_lookup_score(record, lookup) >= 8]
        if len(direct_matches) == 1:
            self._append_plan_ids_batch_summary(lookup, len(records), direct_matches[0])
            return await self._accept_plan_ids_batch_match(lookup, repo, direct_matches[0])
        if len(direct_matches) > 1:
            self._append_plan_ids_batch_summary(lookup, len(records))
            lookup.matches = direct_matches
            lookup.status = FTWilliamsPlanLookupStatus.MULTIPLE_MATCHES
            return "PlanIDs_Batch returned multiple plans with the extracted EIN and plan number."

        probe_limit = max(1, min(50, get_settings().ftw_plan_lookup_probe_limit))
        ranked_candidates = sorted(
            (
                (self._plan_ids_probe_score(record, lookup), index, record)
                for index, record in enumerate(records)
            ),
            key=lambda item: (-item[0], item[1]),
        )
        if len(records) == 1:
            probe_records = records
        else:
            probe_records = [record for score, _, record in ranked_candidates if score > 0][:probe_limit]
        if not probe_records:
            self._append_plan_ids_batch_summary(lookup, len(records))
            lookup.status = FTWilliamsPlanLookupStatus.NOT_FOUND
            return (
                f"PlanIDs_Batch returned {len(records)} plans but none matched the extracted plan metadata. "
                "Select the FT Williams plan manually."
            )

        probed_entries: list[tuple[dict[str, str], str | None, str | None]] = []
        errors: list[str] = []
        concurrency = max(1, min(20, get_settings().ftw_slot_query_concurrency))

        async def probe(record: dict[str, str]):
            identity = self._identity_from_lookup_match(record)
            if not self._has_plan_identity(identity):
                return record, None, None, "PlanIDs_Batch returned an incomplete identifier pair."
            query = FTWilliamsQueryRequest(operation="query_plan", send=True, **identity)
            try:
                built_request = self.ftwilliams.mask_key_id(self.ftwilliams.build_request_xml(query))
            except ValueError as exc:
                return record, None, None, str(exc)
            result = await self.ftwilliams.run_query(query)
            return record, result, built_request, None

        for batch_start in range(0, len(probe_records), concurrency):
            batch_records = probe_records[batch_start : batch_start + concurrency]
            results = await asyncio.gather(*(probe(record) for record in batch_records))
            for record, result, built_request, probe_error in results:
                if probe_error:
                    errors.append(probe_error)
                if result is None:
                    continue
                if not result.success or not result.statuses:
                    error = result.error or self._status_error(result.statuses)
                    if error:
                        errors.append(error)
                    continue
                status = next(
                    (item for item in result.statuses if str(item.error_code or "") == "0"),
                    result.statuses[0],
                )
                match = dict(record)
                match.update(status.query_results or {})
                status_identity = self._identity_from_status(status)
                match.update(
                    {
                        "CustomerID": status_identity.get("customer_id") or match.get("CustomerID", ""),
                        "PlanID": status_identity.get("plan_id") or match.get("PlanID", ""),
                        "FTWCustomerID": status_identity.get("ftw_customer_id") or match.get("FTWCustomerID", ""),
                        "FTWPlanID": status_identity.get("ftw_plan_id") or match.get("FTWPlanID", ""),
                    }
                )
                if status.plan_name and not match.get("PlanName"):
                    match["PlanName"] = status.plan_name
                probed_entries.append(
                    (
                        {key: value for key, value in match.items() if value},
                        result.request_xml or built_request,
                        result.raw_response,
                    )
                )

            exact_entries = [entry for entry in probed_entries if self._plan_lookup_score(entry[0], lookup) >= 8]
            if len(exact_entries) == 1:
                match, selected_request, selected_response = exact_entries[0]
                self._append_plan_ids_batch_summary(lookup, len(records), match)
                self._append_plan_lookup_trace(lookup, selected_request, selected_response)
                return await self._accept_plan_ids_batch_match(lookup, repo, match)
            if len(exact_entries) > 1:
                self._append_plan_ids_batch_summary(lookup, len(records))
                lookup.matches = [entry[0] for entry in exact_entries]
                lookup.status = FTWilliamsPlanLookupStatus.MULTIPLE_MATCHES
                return "PlanIDs_Batch resolved multiple plans with the extracted EIN and plan number."

        probed_matches = [entry[0] for entry in probed_entries]
        partial = self._plan_lookup_matches(probed_matches, lookup)
        if len(partial) == 1:
            selected = next((entry for entry in probed_entries if entry[0] == partial[0]), None)
            self._append_plan_ids_batch_summary(lookup, len(records), partial[0])
            if selected:
                self._append_plan_lookup_trace(lookup, selected[1], selected[2])
            return await self._accept_plan_ids_batch_match(lookup, repo, partial[0])
        if len(partial) > 1:
            self._append_plan_ids_batch_summary(lookup, len(records))
            lookup.matches = partial
            lookup.status = FTWilliamsPlanLookupStatus.MULTIPLE_MATCHES
            return "PlanIDs_Batch returned multiple possible plan matches."
        if len(records) == 1:
            self._append_plan_ids_batch_summary(lookup, len(records), records[0])
            return await self._accept_plan_ids_batch_match(lookup, repo, records[0])
        self._append_plan_ids_batch_summary(lookup, len(records))
        lookup.matches = probe_records
        lookup.status = FTWilliamsPlanLookupStatus.MULTIPLE_MATCHES
        detail = "; ".join(errors[:3])
        return " ".join(
            filter(
                None,
                [
                    detail,
                    f"Checked {len(probe_records)} of {len(records)} filtered PlanIDs_Batch candidates without a verified match.",
                    "Select the FT Williams plan manually.",
                ],
            )
        )

    @staticmethod
    def _append_plan_ids_batch_summary(
        lookup: FTWilliamsPlanLookup,
        result_count: int,
        match: dict[str, str] | None = None,
    ) -> None:
        root = ET.Element("PlanIDsBatchSummary")
        ET.SubElement(root, "ResultCount").text = str(result_count)
        if match:
            selected = ET.SubElement(root, "SelectedPlan")
            for key in ["CustomerID", "PlanID", "FTWCustomerID", "FTWPlanID", "CompanyEmployerID", "PlanNumber"]:
                value = str(match.get(key) or "").strip()
                if value:
                    ET.SubElement(selected, key).text = value
        summary = ET.tostring(root, encoding="unicode")
        lookup.response_xml = "\n\n".join(filter(None, [lookup.response_xml, summary]))

    @staticmethod
    def _append_plan_lookup_trace(
        lookup: FTWilliamsPlanLookup,
        request_xml: str | None,
        response_xml: str | None,
    ) -> None:
        lookup.request_xml = "\n\n".join(filter(None, [lookup.request_xml, request_xml]))
        lookup.response_xml = "\n\n".join(filter(None, [lookup.response_xml, response_xml]))

    async def _accept_plan_ids_batch_match(
        self,
        lookup: FTWilliamsPlanLookup,
        repo,
        match: dict[str, str],
    ) -> str | None:
        identity = self._identity_from_lookup_match(match)
        if not self._has_plan_identity(identity):
            return "PlanIDs_Batch found a plan but did not return a complete identifier pair."
        lookup.matches = [match]
        lookup.matched_identity = identity
        lookup.status = FTWilliamsPlanLookupStatus.MATCHED
        lookup.error_message = None
        await self._persist_plan_mapping(lookup, repo, match, source="PLAN_IDS_BATCH")
        return None

    async def _persist_plan_mapping(self, lookup: FTWilliamsPlanLookup, repo, match: dict[str, str], *, source: str) -> None:
        await repo.upsert_ftwilliams_plan_mapping(
            FTWilliamsPlanMapping(
                company_employer_id=lookup.company_employer_id,
                plan_number=lookup.plan_number,
                year=lookup.year,
                plan_name=match.get("PlanLine1") or match.get("PlanName") or lookup.plan_name,
                sponsor_name=match.get("CompanyName") or lookup.sponsor_name,
                source=source,
                **(lookup.matched_identity or {}),
            )
        )

    async def _query_current_values_for_target_year(
        self,
        fields: list[ExtractedField],
        query_payload_base: dict,
        existing_review: FTWilliamsReview | None,
        *,
        reuse_current_snapshot: bool = False,
    ) -> dict:
        target_year = str(query_payload_base.get("year") or "").strip()
        result = await self._run_current_queries_for_year(
            fields,
            query_payload_base,
            existing_review,
            reuse_current_snapshot=reuse_current_snapshot,
        )
        expects_form_5500 = any(field.form_type == FormType.FORM_5500 for field in fields)
        expects_schedule_a = any(field.form_type == FormType.SCHEDULE_A for field in fields)
        missing_required_record = (
            not result["current_query_failed"]
            and (
                (expects_schedule_a and not result["schedule_a_candidates"])
                or (not expects_schedule_a and expects_form_5500 and not result["form_5500_current"])
            )
        )
        current_year_exists = bool(
            result["schedule_a_candidates"]
            if expects_schedule_a
            else result["form_5500_current"]
        )
        result["comparison_year"] = target_year if current_year_exists else None
        result["comparison_year_source"] = "CURRENT" if current_year_exists else None
        result["current_year_exists"] = current_year_exists
        result["bring_forward_required"] = missing_required_record
        if missing_required_record:
            note = (
                f"The required current-year {target_year} FT Williams record is missing. "
                "Use FT Williams' native Bring Forward action, then refresh FTW data. "
                "No prior-year values were loaded into this comparison."
            )
            result["error_message"] = "; ".join(filter(None, [note, result["error_message"]]))
        return result

    async def _run_current_queries_for_year(
        self,
        fields: list[ExtractedField],
        query_payload_base: dict,
        existing_review: FTWilliamsReview | None,
        *,
        reuse_current_snapshot: bool = False,
    ) -> dict:
        snapshot = await self._current_data_snapshot(
            query_payload_base,
            reuse=reuse_current_snapshot,
        )
        query_request_xmls: list[str] = list(snapshot["query_request_xmls"])
        query_response_xmls: list[str] = list(snapshot["query_response_xmls"])
        form_5500_current: dict[str, str] = dict(snapshot["form_5500_current"])
        schedule_a_current: dict[str, str] = {}
        matched_schedule_a: FTWilliamsStatusItem | None = None
        schedule_a_candidates: list[dict] = []
        schedule_a_records: list[dict] = []
        error_message: str | None = snapshot["form_5500_error"]
        schedule_statuses = deepcopy(snapshot["schedule_statuses"])
        schedule_a_error = snapshot["schedule_a_error"]
        existing_match = existing_review.schedule_a_match if existing_review else None
        existing_create_new = bool((existing_match or {}).get("create_new"))
        if schedule_statuses:
            schedule_a_candidates = self._schedule_candidate_payloads(schedule_statuses, fields)
            schedule_a_records = self._schedule_record_payloads(schedule_statuses)
            manual_sequence = (
                str((existing_match or {}).get("ftw_seq_no") or "").strip()
                if str((existing_match or {}).get("source") or "").upper() == "MANUAL"
                else ""
            )
            if existing_create_new:
                # A reviewer explicitly chose to create a new Schedule A.  The
                # existing set is still queried so it can be preserved in the
                # complete update payload, but no existing sequence should be
                # selected (or required) for the new record itself.
                matched_schedule_a = None
                schedule_a_error = None
            elif manual_sequence:
                # A reviewer-selected sequence is authoritative.  Refresh its
                # current values even when extracted identity fields differ;
                # those differences are exactly what the review must show.
                matched_schedule_a = next(
                    (
                        status
                        for status in schedule_statuses
                        if str(status.ftw_seq_no or "").strip() == manual_sequence
                        and status.query_results
                    ),
                    None,
                )
            elif not existing_create_new:
                matched_schedule_a = self._match_schedule_a_status(
                    fields,
                    schedule_statuses,
                    preferred_ftw_seq_no=self._preferred_schedule_a_sequence(existing_review),
                )
            schedule_a_current = matched_schedule_a.query_results if matched_schedule_a else {}
            if not matched_schedule_a and not existing_create_new:
                schedule_a_error = (
                    "FT Williams Schedule A records were found, but none safely matched the extracted "
                    "carrier, EIN, NAIC, or contract. Select the correct existing record or create a new Schedule A."
                )
        else:
            schedule_a_error = schedule_a_error or "Schedule A query did not return a usable current schedule."
        if schedule_a_error:
            error_message = "; ".join(filter(None, [error_message, schedule_a_error]))

        expects_form_5500 = any(field.form_type == FormType.FORM_5500 for field in fields)
        expects_schedule_a = any(field.form_type == FormType.SCHEDULE_A for field in fields)
        has_preserved_schedule_set = bool(existing_create_new and schedule_statuses)
        has_any_current = bool(form_5500_current or schedule_a_current or has_preserved_schedule_set)
        has_required_current = (not expects_form_5500 or bool(form_5500_current)) and (
            not expects_schedule_a or bool(schedule_a_current) or has_preserved_schedule_set
        )
        current_query_failed = bool(
            (expects_form_5500 and snapshot["form_5500_query_failed"])
            or (expects_schedule_a and snapshot["schedule_a_query_failed"])
        )

        return {
            "query_request_xmls": query_request_xmls,
            "query_response_xmls": query_response_xmls,
            "form_5500_current": form_5500_current,
            "schedule_a_current": schedule_a_current,
            "matched_schedule_a": matched_schedule_a,
            "schedule_a_candidates": schedule_a_candidates,
            "schedule_a_records": schedule_a_records,
            "error_message": error_message,
            "current_query_success": has_any_current and not current_query_failed,
            "current_query_complete": has_required_current and not current_query_failed,
            "current_query_failed": current_query_failed,
        }

    async def _current_data_snapshot(self, query_payload_base: dict, *, reuse: bool) -> dict:
        key = self._current_data_snapshot_key(query_payload_base)
        now = time.monotonic()
        ttl = max(0, get_settings().ftw_snapshot_ttl_seconds)
        cached = _CURRENT_DATA_SNAPSHOT_CACHE.get(key)
        if reuse and cached and now - cached[0] <= ttl:
            return deepcopy(cached[1])

        if reuse and key in _CURRENT_DATA_SNAPSHOT_INFLIGHT:
            return deepcopy(await _CURRENT_DATA_SNAPSHOT_INFLIGHT[key])

        task = asyncio.create_task(self._fetch_current_data_snapshot(query_payload_base))
        if reuse:
            _CURRENT_DATA_SNAPSHOT_INFLIGHT[key] = task
        try:
            snapshot = await task
            if not snapshot["form_5500_query_failed"] and not snapshot["schedule_a_query_failed"]:
                _CURRENT_DATA_SNAPSHOT_CACHE[key] = (time.monotonic(), deepcopy(snapshot))
            return deepcopy(snapshot)
        finally:
            if reuse and _CURRENT_DATA_SNAPSHOT_INFLIGHT.get(key) is task:
                _CURRENT_DATA_SNAPSHOT_INFLIGHT.pop(key, None)

    async def _fetch_current_data_snapshot(self, query_payload_base: dict) -> dict:
        query_request_xmls: list[str] = []
        query_response_xmls: list[str] = []
        form_5500_current: dict[str, str] = {}

        query_5500 = await self.ftwilliams.run_query(
            FTWilliamsQueryRequest(operation="query_5500", send=True, **query_payload_base)
        )
        query_request_xmls.append(query_5500.request_xml)
        if query_5500.raw_response:
            query_response_xmls.append(query_5500.raw_response)
        if query_5500.success and query_5500.statuses:
            form_5500_current = query_5500.statuses[0].query_results
            form_5500_error = None
            form_5500_query_failed = False
        else:
            form_5500_error = (
                query_5500.error
                or self._status_error(query_5500.statuses)
                or "Form 5500 query did not succeed."
            )
            form_5500_query_failed = not self._response_explicitly_reports_missing(query_5500)

        schedule_statuses, schedule_request_xmls, schedule_response_xmls, schedule_a_error = (
            await self._query_schedule_a_statuses(query_payload_base)
        )
        query_request_xmls.extend(schedule_request_xmls)
        query_response_xmls.extend(schedule_response_xmls)
        return {
            "query_request_xmls": query_request_xmls,
            "query_response_xmls": query_response_xmls,
            "form_5500_current": form_5500_current,
            "form_5500_error": form_5500_error,
            "form_5500_query_failed": form_5500_query_failed,
            "schedule_statuses": schedule_statuses,
            "schedule_a_error": schedule_a_error,
            "schedule_a_query_failed": bool(schedule_a_error),
        }

    def _current_data_snapshot_key(self, query_payload_base: dict) -> tuple[str, ...]:
        settings = get_settings()
        return (
            str(settings.ftwlink_endpoint_url or ""),
            str(query_payload_base.get("ftw_customer_id") or ""),
            str(query_payload_base.get("ftw_plan_id") or ""),
            str(query_payload_base.get("customer_id") or ""),
            str(query_payload_base.get("plan_id") or ""),
            str(query_payload_base.get("year") or ""),
        )

    @staticmethod
    def _review_has_valid_current_snapshot(review: FTWilliamsReview) -> bool:
        return bool(
            review.current_year_exists
            and (
                review.form_5500_current_values
                or review.schedule_a_current_values
                or review.schedule_a_records
                or review.schedule_a_candidates
            )
        )

    @staticmethod
    def _response_explicitly_reports_missing(response) -> bool:
        statuses = list(response.statuses or [])
        return bool(statuses) and all(str(status.error_code or "") == "59" for status in statuses)

    async def _query_schedule_a_statuses(self, query_payload_base: dict) -> tuple[list[FTWilliamsStatusItem], list[str], list[str], str | None]:
        request_xmls: list[str] = []
        response_xmls: list[str] = []
        statuses: list[FTWilliamsStatusItem] = []
        errors: list[str] = []
        fatal_plan_error = False

        concurrency = max(1, min(20, get_settings().ftw_slot_query_concurrency))

        async def query_sequence(sequence: int):
            response = await self.ftwilliams.run_query(
                FTWilliamsQueryRequest(
                    operation="query_schedule_a",
                    send=True,
                    ftw_seq_no=str(sequence),
                    **query_payload_base,
                )
            )
            return sequence, response

        # Use deterministic batches. A fatal plan-level response stops the
        # next batch, preserving the old early-exit behavior while allowing
        # independent slots within each batch to overlap.
        for batch_start in range(1, 21, concurrency):
            batch = range(batch_start, min(21, batch_start + concurrency))
            responses = await asyncio.gather(*(query_sequence(sequence) for sequence in batch))
            for sequence, response in responses:
                request_xmls.append(response.request_xml)
                if response.raw_response:
                    response_xmls.append(response.raw_response)
                for status in response.statuses:
                    if str(status.error_code or "") == "0":
                        if not status.ftw_seq_no:
                            status.ftw_seq_no = str(sequence)
                        statuses.append(status)
                error = response.error or self._status_error(response.statuses, ignore_error_codes={"59"})
                if error:
                    errors.append(error)
                if self._has_fatal_plan_query_error(response.statuses):
                    fatal_plan_error = True
            if fatal_plan_error:
                break

        if fatal_plan_error:
            return statuses, request_xmls, response_xmls, "; ".join(errors) if errors else None

        fallback = await self.ftwilliams.run_query(FTWilliamsQueryRequest(operation="query_schedule_a", send=True, **query_payload_base))
        request_xmls.append(fallback.request_xml)
        if fallback.raw_response:
            response_xmls.append(fallback.raw_response)
        if fallback.success:
            statuses = self._merge_schedule_statuses(statuses, [status for status in fallback.statuses if str(status.error_code or "") == "0"])
        else:
            error = fallback.error or self._status_error(fallback.statuses, ignore_error_codes={"59"})
            if error:
                errors.append(error)
        return statuses, request_xmls, response_xmls, "; ".join(errors) if errors else None

    async def approve_and_update(
        self,
        filing_id: str,
        *,
        reason: str = "",
        send_to_ftw: bool = False,
        refresh_current_before_update: bool = True,
        run_edit_checks: bool = False,
        override_blockers: bool = False,
    ) -> FTWilliamsReview | None:
        if not send_to_ftw:
            return await self._approve_and_update_unlocked(
                filing_id,
                reason=reason,
                send_to_ftw=False,
                refresh_current_before_update=refresh_current_before_update,
                run_edit_checks=run_edit_checks,
                override_blockers=override_blockers,
            )

        repo = get_repository()
        existing_review = await repo.get_ftwilliams_review(filing_id)
        lock_key = self._ftw_update_lock_key(existing_review, filing_id)
        lock = _FTW_UPDATE_LOCKS.setdefault(lock_key, asyncio.Lock())
        async with lock:
            # The fresh vendor snapshot and the replace-style write execute in
            # one critical section so parallel Schedule A uploads cannot
            # overwrite each other's sibling records.
            return await self._approve_and_update_unlocked(
                filing_id,
                reason=reason,
                send_to_ftw=True,
                refresh_current_before_update=refresh_current_before_update,
                run_edit_checks=run_edit_checks,
                override_blockers=override_blockers,
            )

    @staticmethod
    def _ftw_update_lock_key(review: FTWilliamsReview | None, filing_id: str) -> tuple[str, ...]:
        if review:
            customer = str(review.ftw_customer_id or review.customer_id or "").strip()
            plan = str(review.ftw_plan_id or review.plan_id or "").strip()
            year = str(review.year or review.comparison_year or "").strip()
            if customer and plan and year:
                return "ftw-plan", customer, plan, year
        return "filing", str(filing_id)

    async def _approve_and_update_unlocked(
        self,
        filing_id: str,
        *,
        reason: str = "",
        send_to_ftw: bool = False,
        refresh_current_before_update: bool = True,
        run_edit_checks: bool = False,
        override_blockers: bool = False,
    ) -> FTWilliamsReview | None:
        repo = get_repository()
        published_rules = await FieldRuleService(repo).published_rules()
        if not send_to_ftw:
            fields = await repo.list_fields(filing_id)
            review = await repo.get_ftwilliams_review(filing_id)
            approval_fields = fields
            if review and review.schedule_a_contract_type in {
                ScheduleAContractType.EXPERIENCE_RATED,
                ScheduleAContractType.NONEXPERIENCE_RATED,
            }:
                approval_fields = filter_schedule_a_fields_for_contract_type(fields, review.schedule_a_contract_type, rules=published_rules)
            approval_error = self._approval_blocking_error(approval_fields)
            if review and review.current_query_sent and (
                not review.current_year_exists or review.bring_forward_required
            ):
                raise ValueError(
                    "The current-year FT Williams record is missing. Complete FT Williams' native Bring Forward action and refresh FTW data before approval."
                )
            contract_type_error = self._review_contract_type_block_reason(review) if review else None
            if contract_type_error:
                raise ValueError(contract_type_error)
            plan_year_error = self._review_plan_year_block_reason(review) if review else None
            if plan_year_error:
                raise ValueError(plan_year_error)
            if approval_error and not override_blockers:
                raise ValueError(approval_error)
            await repo.update_filing(
                filing_id,
                {
                    "status": FilingStatus.APPROVED,
                    "approved_at": datetime.utcnow(),
                    "error_message": None,
                },
            )
            await repo.add_event(ReviewEvent(filing_id=filing_id, type="APPROVE", reason=reason))
            await repo.add_audit(
                AuditLog(
                    filing_id=filing_id,
                    event="APPROVED",
                    message="Reviewer approved filing.",
                    details={
                        "reason": reason,
                        "override_blockers": bool(approval_error and override_blockers),
                        "approval_blockers": approval_error,
                    },
                )
            )
            return await repo.get_ftwilliams_review(filing_id)

        # Rebuild the outbound XML immediately before sending. FT Williams treats
        # Schedule A updates as a full replacement set, so sending stale XML that
        # only contains the selected schedule can remove the other Schedule A rows.
        existing_review = await repo.get_ftwilliams_review(filing_id)
        had_active_failure = bool(
            existing_review
            and (
                existing_review.active_failure
                or (
                    existing_review.failure_dismissed_at is None
                    and existing_review.status in {
                        FTWilliamsReviewStatus.UPDATE_FAILED,
                        FTWilliamsReviewStatus.UPDATE_UNKNOWN,
                    }
                )
            )
        )
        # Live replace-style writes must always use a fresh vendor snapshot.
        # A cached snapshot can omit a Schedule A that was manually added in
        # FT Williams after the cache was populated.
        can_reuse_current_snapshot = False
        review = await self.prepare_review(
            filing_id,
            send_queries=refresh_current_before_update,
            reuse_current_snapshot=can_reuse_current_snapshot,
        )
        if not review.configured:
            error_message = "FT Williams endpoint and KeyID must be configured before sending approved updates."
            await self._record_update_failure(repo, filing_id, review, error_message)
            raise ValueError(error_message)
        if not review.current_query_success or review.current_query_complete is False:
            error_message = review.error_message or "Current FT Williams data must be queried successfully before sending approved updates."
            await self._record_update_failure(repo, filing_id, review, error_message)
            raise ValueError(error_message)
        if not review.current_year_exists or review.bring_forward_required:
            error_message = (
                "The current-year FT Williams record is missing. Complete FT Williams' native Bring Forward action "
                "and refresh FTW data before sending any update."
            )
            await self._record_update_failure(repo, filing_id, review, error_message)
            raise ValueError(error_message)
        if review.ftw_editable is False:
            error_message = (
                "FT Williams reports this filing as locked and not editable. "
                "Use Amend Filing in FT Williams, then query current data again before sending."
            )
            await self._record_update_failure(repo, filing_id, review, error_message)
            raise ValueError(error_message)
        # The review preview intentionally carries complete replacement XML,
        # but approval must send only forms with a real proposed change. This
        # prevents a Form 5500-only edit from rewriting every Schedule A and
        # changing sibling records as a side effect.
        self._prune_noop_update_payloads(review)
        contract_type_error = self._review_contract_type_block_reason(review)
        if contract_type_error:
            await self._record_update_failure(repo, filing_id, review, contract_type_error)
            raise ValueError(contract_type_error)
        plan_year_error = self._review_plan_year_block_reason(review)
        if plan_year_error:
            await self._record_update_failure(repo, filing_id, review, plan_year_error)
            raise ValueError(plan_year_error)
        schedule_a_required_error = self._missing_required_schedule_a_payload(review)
        if schedule_a_required_error:
            await self._record_update_failure(repo, filing_id, review, schedule_a_required_error)
            raise ValueError(schedule_a_required_error)
        if (
            review.update_xml_schedule_a
            and "DOLScheduleAData" in review.update_xml_schedule_a
            and not get_settings().ftwlink_schedule_a_updates_enabled
        ):
            error_message = (
                "Schedule A sending is temporarily disabled because the FT Williams sandbox returned an "
                "empty response and removed existing Schedule A records during verified replace-style tests. "
                "Restore the records in FT Williams and confirm the vendor update contract before enabling sends."
            )
            await self._record_update_failure(repo, filing_id, review, error_message)
            raise ValueError(error_message)
        schedule_a_safety_error = self._missing_schedule_a_records_for_safe_send(review)
        if schedule_a_safety_error:
            await self._record_update_failure(repo, filing_id, review, schedule_a_safety_error)
            raise ValueError(schedule_a_safety_error)
        if not any(
            [
                bool(review.update_xml_5500 and "DOL5500Data" in review.update_xml_5500),
                bool(review.update_xml_schedule_a and "DOLScheduleAData" in review.update_xml_schedule_a),
            ]
        ):
            error_message = "No FT Williams changes remain to send for the currently queried forms."
            await self._record_update_failure(repo, filing_id, review, error_message)
            raise ValueError(error_message)

        settings = get_settings()
        review.query_access_verified = bool(
            review.current_query_success and review.current_query_complete is not False
        )
        review.schema_validation_results = []
        review.schema_validation_blocked = False
        if settings.ftw_schema_validation_enabled:
            validation_mode = "ENFORCE" if settings.ftw_schema_enforcement_enabled else "OBSERVE"
            schema_service = FTWilliamsSchemaService()
            if review.update_xml_5500 and "DOL5500Data" in review.update_xml_5500:
                review.schema_validation_results.append(
                    schema_service.validate_outgoing_xml(
                        FormType.FORM_5500,
                        review.year or review.comparison_year or "",
                        review.update_xml_5500,
                        mode=validation_mode,
                    )
                )
            if review.update_xml_schedule_a and "DOLScheduleAData" in review.update_xml_schedule_a:
                trusted_preserved_values = self._trusted_schedule_a_preserved_values(review)
                review.schema_validation_results.append(
                    schema_service.validate_outgoing_xml(
                        FormType.SCHEDULE_A,
                        review.year or review.comparison_year or "",
                        review.update_xml_schedule_a,
                        mode=validation_mode,
                        trusted_preserved_values=trusted_preserved_values,
                    )
                )
            schema_issues = [
                issue
                for result in review.schema_validation_results
                for issue in result.issues
            ]
            if schema_issues and settings.ftw_schema_enforcement_enabled:
                review.schema_validation_blocked = True
                details = "; ".join(
                    f"{issue.tag}:{issue.value} ({issue.reason}; expected {issue.expected_format or 'documented FT format'})"
                    for issue in schema_issues
                )
                error_message = f"FT Williams schema validation blocked the update: {details}"
                await self._record_update_failure(repo, filing_id, review, error_message)
                raise ValueError(error_message)

        effective_edit_checks = bool(run_edit_checks or settings.ftw_auto_edit_checks_enabled)
        if effective_edit_checks:
            baseline = await self.ftwilliams.run_query(
                FTWilliamsQueryRequest(
                    operation="edit_checks_5500",
                    ftw_customer_id=review.ftw_customer_id,
                    ftw_plan_id=review.ftw_plan_id,
                    year=review.year,
                    send=True,
                )
            )
            review.edit_check_baseline_request_xml = baseline.request_xml
            review.edit_check_baseline_response_xml = baseline.raw_response or baseline.error
            review.edit_check_baseline_success = baseline.success
            review.edit_check_baseline_issues = self.ftwilliams.parse_edit_check_issues(
                baseline.statuses or []
            )
            review.update_diagnostics = self._operation_diagnostics([baseline])

        preserved_validation_results = list(review.schema_validation_results)
        preserved_baseline = {
            "request": review.edit_check_baseline_request_xml,
            "response": review.edit_check_baseline_response_xml,
            "success": review.edit_check_baseline_success,
            "issues": list(review.edit_check_baseline_issues or []),
        }

        response_parts: list[str] = []
        retry_count = 0
        success = False
        ftw_accepted = False
        error_message: str | None = None
        verification_attempted = False
        verification_success: bool | None = None
        verification_mismatches: list[dict] = []
        verification_request_xml: str | None = None
        verification_response_xml: str | None = None
        schedule_a_snapshot_xml = self._build_schedule_a_restore_xml(review)
        recovery_responses: list = []
        schedule_a_restore = {
            "attempted": False,
            "success": None,
            "response_xml": None,
            "verification_request_xml": None,
            "verification_response_xml": None,
            "verification_mismatches": [],
        }
        responses = await self._send_update_payload(review)
        sent_form_types = {
            FormType.FORM_5500
            for response in responses
            if response.operation == "update_5500"
        } | {
            FormType.SCHEDULE_A
            for response in responses
            if response.operation == "update_schedule_a"
        }
        attempted_fields = {
            self._comparison_field_key(field): {
                "field_id": field.field_id,
                "tag": field.ftw_tag,
                "label": field.label,
                "form_type": field.form_type.value if field.form_type else None,
                "sent_value": field.proposed_value,
            }
            for field in review.fields
            if field.changed and field.update_included and field.form_type in sent_form_types
        }
        attempted_field_keys = set(attempted_fields)
        attempted_count = len(attempted_field_keys)
        response_parts.extend(
            response.raw_response or response.error or ""
            for response in responses
            if response.raw_response or response.error
        )
        ftw_accepted = bool(responses) and all(response.success for response in responses)
        review.update_access_status = "GRANTED" if ftw_accepted else "DENIED"
        error_message = None if ftw_accepted else "; ".join(
            filter(None, [response.error or self._status_error(response.statuses) for response in responses])
        )

        if any(self._update_response_is_ambiguous(response) for response in responses):
            ambiguous_schedule_response = any(
                response.operation == "update_schedule_a" and self._update_response_is_ambiguous(response)
                for response in responses
            )
            if ambiguous_schedule_response and schedule_a_snapshot_xml:
                schedule_a_restore = await self._restore_schedule_a_snapshot(review, schedule_a_snapshot_xml)
                recovery_responses.extend(schedule_a_restore.get("responses") or [])
            return await self._record_ambiguous_update(
                repo,
                filing_id,
                review,
                attempted_fields,
                attempted_field_keys,
                responses,
                schedule_a_restore=schedule_a_restore,
                recovery_responses=recovery_responses,
            )

        mixed_schedule_response = next(
            (
                response
                for response in responses
                if response.operation == "update_schedule_a"
                and self._update_response_is_mixed(response)
            ),
            None,
        )
        if mixed_schedule_response is not None:
            vendor_error = self._status_error(mixed_schedule_response.statuses)
            error_message = "FT Williams partially accepted the Schedule A replacement set" + (
                f": {vendor_error}" if vendor_error else "."
            )
            if schedule_a_snapshot_xml:
                schedule_a_restore = await self._restore_schedule_a_snapshot(review, schedule_a_snapshot_xml)
                recovery_responses.extend(schedule_a_restore.get("responses") or [])
                if schedule_a_restore["success"]:
                    error_message += " The original Schedule A records were automatically restored and verified."
                else:
                    error_message += (
                        " Automatic restoration could not be verified; manual FT Williams recovery is required."
                    )
            else:
                error_message += " No complete pre-update snapshot was available; manual FT Williams recovery is required."

        if ftw_accepted:
            clear_ftw_current_snapshot_cache()
            verification = await self._verify_update_readback(review)
            verification_attempted = True
            verification_success = bool(verification["success"])
            verification_mismatches = verification["mismatches"]
            verification_request_xml = verification["request_xml"]
            verification_response_xml = verification["response_xml"]
            if verification_success:
                success = True
            else:
                mismatch_fields = ", ".join(
                    str(item.get("tag") or item.get("form") or "field")
                    for item in verification_mismatches[:8]
                )
                error_message = (
                    "FT Williams accepted the update, but read-back verification did not match the sent values"
                    f" ({mismatch_fields or 'updated fields'})."
                )
                schedule_a_corruption_confirmed = self._schedule_a_verification_confirms_corruption(
                    verification_mismatches
                )
                if schedule_a_corruption_confirmed and schedule_a_snapshot_xml:
                    schedule_a_restore = await self._restore_schedule_a_snapshot(review, schedule_a_snapshot_xml)
                    recovery_responses.extend(schedule_a_restore.get("responses") or [])
                    if schedule_a_restore["success"]:
                        error_message += " The original Schedule A records were automatically restored and verified."
                    else:
                        error_message += (
                            " Automatic restoration could not be verified; manual FT Williams recovery is required."
                        )
                elif any(
                    str(item.get("form") or "") == "DOLScheduleAData"
                    for item in verification_mismatches
                ):
                    error_message += (
                        " The Schedule A record set is still present, so no destructive full-record rollback was attempted."
                    )

        clear_ftw_current_snapshot_cache()
        reconciled = await self.prepare_review(filing_id, send_queries=True)
        remaining_field_keys = self._remaining_attempted_keys(attempted_field_keys, reconciled)
        remaining_count = len(remaining_field_keys)
        confirmed_count = max(0, attempted_count - remaining_count)
        if self._reconciled_update_is_safe(
            attempted_count=attempted_count,
            remaining_count=remaining_count,
            current_query_success=reconciled.current_query_success,
            verification_attempted=verification_attempted,
            verification_mismatches=verification_mismatches,
        ):
            success = True
            error_message = None
            verification_attempted = True
            verification_success = True
            verification_mismatches = []

        review = reconciled
        review.schema_validation_results = preserved_validation_results
        review.schema_validation_blocked = False
        review.query_access_verified = bool(
            review.current_query_success and review.current_query_complete is not False
        )
        review.update_access_status = "GRANTED" if ftw_accepted else "DENIED"
        review.edit_check_baseline_request_xml = preserved_baseline["request"]
        review.edit_check_baseline_response_xml = preserved_baseline["response"]
        review.edit_check_baseline_success = preserved_baseline["success"]
        review.edit_check_baseline_issues = preserved_baseline["issues"]
        review.edit_check_final_success = None
        review.edit_check_final_issues = []
        review.edit_check_validation_status = "NOT_RUN"
        review.edit_check_new_issues = []
        review.edit_check_resolved_issues = []
        review.update_response_xml = "\n\n".join(response_parts) or None
        review.update_verification_attempted = verification_attempted
        review.update_verification_success = verification_success
        review.update_verification_mismatches = verification_mismatches
        review.update_verification_request_xml = verification_request_xml
        review.update_verification_response_xml = verification_response_xml
        review.schedule_a_restore_attempted = bool(schedule_a_restore["attempted"])
        review.schedule_a_restore_success = schedule_a_restore["success"]
        review.schedule_a_restore_response_xml = schedule_a_restore["response_xml"]
        review.schedule_a_restore_verification_request_xml = schedule_a_restore["verification_request_xml"]
        review.schedule_a_restore_verification_response_xml = schedule_a_restore["verification_response_xml"]
        review.schedule_a_restore_verification_mismatches = list(
            schedule_a_restore["verification_mismatches"] or []
        )
        review.update_attempted_count = attempted_count
        review.update_confirmed_count = confirmed_count
        review.update_remaining_count = remaining_count
        mismatches_by_tag = {
            str(item.get("tag") or ""): item
            for item in verification_mismatches
            if item.get("tag")
        }
        review.update_results = [
            {
                **attempted_fields[key],
                "status": "NEEDS_CORRECTION" if key in remaining_field_keys else "VERIFIED",
                "reason": (
                    mismatches_by_tag.get(str(attempted_fields[key].get("tag") or ""), {}).get("reason")
                    or ("FT Williams still returns a different value." if key in remaining_field_keys else "Confirmed by FT Williams read-back.")
                ),
            }
            for key in attempted_field_keys
            if key in attempted_fields
        ]
        review.update_retry_count = retry_count
        review.update_diagnostics = self._operation_diagnostics([*responses, *recovery_responses])
        verified_update = bool(success)

        if verified_update and effective_edit_checks:
            edit_checks = await self.ftwilliams.run_query(
                FTWilliamsQueryRequest(
                    operation="edit_checks_5500",
                    ftw_customer_id=review.ftw_customer_id,
                    ftw_plan_id=review.ftw_plan_id,
                    year=review.year,
                    send=True,
                )
            )
            review.edit_check_request_xml = edit_checks.request_xml
            review.edit_check_response_xml = edit_checks.raw_response or edit_checks.error
            review.edit_check_final_success = edit_checks.success
            review.edit_check_final_issues = self.ftwilliams.parse_edit_check_issues(
                edit_checks.statuses or []
            )
            review.update_diagnostics.extend(self._operation_diagnostics([edit_checks]))
            self._classify_edit_check_result(review)

        if verified_update and settings.ftw_pdf_audit_enabled:
            review.audit_pdf_status = "GENERATING"
            try:
                pdf_response, pdf_bytes = await self.ftwilliams.generate_dol_document(
                    ftw_customer_id=review.ftw_customer_id or "",
                    ftw_plan_id=review.ftw_plan_id or "",
                    year=review.year or review.comparison_year or "",
                    document="A",
                    ftw_seq_no=review.ftw_seq_no,
                )
                if not pdf_response.success or not pdf_bytes:
                    raise ValueError(
                        pdf_response.error
                        or self._status_error(pdf_response.statuses)
                        or "FT Williams did not return a valid Schedule A PDF."
                    )
                stored_pdf = StorageService().save_pdf(
                    f"{filing_id}-ftw-schedule-a-{review.year or review.comparison_year or 'current'}.pdf",
                    "application/pdf",
                    pdf_bytes,
                )
                review.audit_pdf_status = "AVAILABLE"
                review.audit_pdf_key = stored_pdf.get("key")
                review.audit_pdf_bucket = stored_pdf.get("bucket")
                review.audit_pdf_local_path = stored_pdf.get("local_path")
                review.audit_pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
                review.audit_pdf_created_at = datetime.utcnow()
                review.audit_pdf_error = None
            except Exception as exc:  # Evidence failure must not turn a verified write into a failed write.
                review.audit_pdf_status = "FAILED"
                review.audit_pdf_error = str(exc)

        review.error_message = error_message
        review.status = FTWilliamsReviewStatus.UPDATE_SENT if success else FTWilliamsReviewStatus.UPDATE_FAILED
        review.client_error = self._normalize_review_error(review.error_message, review.fields)
        if success:
            review.active_failure = False
            review.active_failure_reason = None
            review.active_failure_client_error = None
            review.active_failure_type = None
            review.active_failure_issue_count = None
            review.active_failure_issue_groups = []
            review.active_failure_at = None
            review.failure_dismissed_at = None
            review.failure_dismissed_reason = None
        else:
            self._activate_failure(review, error_message or "FT Williams update failed.")

        await repo.upsert_ftwilliams_review(review)
        await repo.update_filing(
            filing_id,
            {
                "status": FilingStatus.APPROVED if success else FilingStatus.FAILED,
                "approved_at": datetime.utcnow() if success else None,
                "error_message": review.error_message,
            },
        )
        await repo.add_event(ReviewEvent(filing_id=filing_id, type="APPROVE_AND_FTW_UPDATE", reason=reason))
        await repo.add_audit(
            AuditLog(
                filing_id=filing_id,
                event="FTWILLIAMS_UPDATE_SENT" if success else "FTWILLIAMS_UPDATE_FAILED",
                message="Approved fields were sent to FT Williams." if success else "FT Williams update failed.",
                details={
                    "error": review.error_message,
                    "run_edit_checks": effective_edit_checks,
                    "updated_field_count": confirmed_count,
                    "ftw_accepted": ftw_accepted,
                    "update_attempted_count": attempted_count,
                    "update_confirmed_count": confirmed_count,
                    "update_remaining_count": remaining_count,
                    "update_retry_count": retry_count,
                    "verification_attempted": review.update_verification_attempted,
                    "verification_success": review.update_verification_success,
                    "verification_mismatch_count": len(review.update_verification_mismatches),
                    "schedule_a_restore_attempted": review.schedule_a_restore_attempted,
                    "schedule_a_restore_success": review.schedule_a_restore_success,
                    "schedule_a_restore_mismatch_count": len(review.schedule_a_restore_verification_mismatches),
                    "error_code": review.active_failure_client_error.code if review.active_failure_client_error else None,
                    "operation_diagnostics": [item.model_dump(mode="json") for item in review.update_diagnostics],
                    "schema_validation": [item.model_dump(mode="json") for item in review.schema_validation_results],
                    "query_access_verified": review.query_access_verified,
                    "update_access_status": review.update_access_status,
                    "edit_check_baseline_success": review.edit_check_baseline_success,
                    "edit_check_final_success": review.edit_check_final_success,
                    "edit_check_validation_status": review.edit_check_validation_status,
                    "edit_check_new_issue_count": len(review.edit_check_new_issues),
                    "edit_check_resolved_issue_count": len(review.edit_check_resolved_issues),
                    "audit_pdf_status": review.audit_pdf_status,
                    "audit_pdf_sha256": review.audit_pdf_sha256,
                },
            )
        )
        if success and had_active_failure:
            await repo.add_audit(
                AuditLog(
                    filing_id=filing_id,
                    event="FTWILLIAMS_UPDATE_FAILURE_RESOLVED",
                    message="Previous FT Williams update failure was resolved by a verified successful update.",
                    details={"updated_field_count": confirmed_count},
                )
            )
        return review

    async def _send_update_payload(self, review: FTWilliamsReview) -> list:
        responses = []
        if review.update_xml_5500 and "DOL5500Data" in review.update_xml_5500:
            form_response = await self.ftwilliams.send_xml("update_5500", review.update_xml_5500)
            responses.append(form_response)
            if not form_response.success or self._update_response_is_ambiguous(form_response):
                return responses
        if review.update_xml_schedule_a and "DOLScheduleAData" in review.update_xml_schedule_a:
            responses.append(await self.ftwilliams.send_xml("update_schedule_a", review.update_xml_schedule_a))
        return responses

    def _build_schedule_a_restore_xml(self, review: FTWilliamsReview) -> str:
        """Build the exact pre-write Schedule A set for emergency restoration."""
        if not review.update_xml_schedule_a or "DOLScheduleAData" not in review.update_xml_schedule_a:
            return ""
        records = list(review.schedule_a_records or [])
        if not records:
            return ""
        identity = self._current_query_identity_from_review(review)
        restore_xml = build_schedule_a_records_update_xml(
            records,
            None,
            [],
            transaction_type="2",
            **{key: value for key, value in identity.items() if key != "ftw_seq_no"},
        )
        if schedule_a_replacement_data_gaps(records, restore_xml):
            return ""
        return restore_xml

    async def _restore_schedule_a_snapshot(self, review: FTWilliamsReview, restore_xml: str) -> dict:
        """Restore and verify the pre-write Schedule A set after an unsafe result."""
        result = {
            "attempted": True,
            "success": False,
            "response_xml": None,
            "verification_request_xml": None,
            "verification_response_xml": None,
            "verification_mismatches": [],
            "responses": [],
        }
        response = await self.ftwilliams.send_xml("update_schedule_a", restore_xml)
        result["responses"] = [response]
        result["response_xml"] = response.raw_response or response.error
        if not response.success or self._update_response_is_ambiguous(response):
            result["verification_mismatches"] = [
                {
                    "form": "DOLScheduleAData",
                    "tag": "DOLScheduleAData",
                    "reason": response.error
                    or self._status_error(response.statuses)
                    or "FT Williams did not confirm the restoration request.",
                }
            ]
            return result

        clear_ftw_current_snapshot_cache()
        restore_review = review.model_copy(deep=True)
        restore_review.update_xml_5500 = None
        restore_review.update_xml_schedule_a = restore_xml
        verification = await self._verify_update_readback(restore_review)
        result["success"] = bool(verification["success"])
        result["verification_request_xml"] = verification["request_xml"]
        result["verification_response_xml"] = verification["response_xml"]
        result["verification_mismatches"] = list(verification["mismatches"] or [])
        return result

    @staticmethod
    def _update_response_is_ambiguous(response) -> bool:
        if not response.sent or response.http_status is None or not 200 <= response.http_status < 300:
            return False
        raw_response = str(response.raw_response or "").strip()
        parse_failed = any(str(status.error_code or "") == "PARSE_ERROR" for status in response.statuses or [])
        return not raw_response or parse_failed

    @staticmethod
    def _update_response_is_mixed(response) -> bool:
        """True when FT Williams accepted some records and rejected others."""
        status_codes = {
            str(status.error_code or "").strip()
            for status in response.statuses or []
            if str(status.error_code or "").strip()
        }
        return "0" in status_codes and any(code != "0" for code in status_codes)

    @staticmethod
    def _comparison_field_key(field: FTWilliamsComparisonField) -> str:
        return str(
            field.field_id
            or field.rule_key
            or f"{field.form_type}:{field.ftw_tag}:{field.label}"
        )

    def _changed_field_keys(self, review: FTWilliamsReview) -> set[str]:
        return {
            self._comparison_field_key(field)
            for field in review.fields
            if field.changed and field.update_included
        }

    def _remaining_attempted_count(self, attempted_keys: set[str], review: FTWilliamsReview) -> int:
        return len(self._remaining_attempted_keys(attempted_keys, review))

    def _remaining_attempted_keys(self, attempted_keys: set[str], review: FTWilliamsReview) -> set[str]:
        refreshed_by_key = {
            self._comparison_field_key(field): field
            for field in review.fields
        }
        return {
            key
            for key in attempted_keys
            if key not in refreshed_by_key or refreshed_by_key[key].changed
        }

    @staticmethod
    def _reconciled_update_is_safe(
        *,
        attempted_count: int,
        remaining_count: int,
        current_query_success: bool,
        verification_attempted: bool,
        verification_mismatches: list[dict],
    ) -> bool:
        """Allow reconciliation recovery only when no read-back safety check failed.

        A fresh comparison can confirm that the target fields changed, but it
        cannot prove that preserved Schedule A records or broker rows remained
        unchanged.  Once the full read-back verifier finds a mismatch, that
        failure must remain visible instead of being overwritten by the target-
        field comparison.
        """
        return bool(
            attempted_count
            and remaining_count == 0
            and current_query_success
            and not verification_attempted
            and not verification_mismatches
        )

    async def _verify_update_readback(self, review: FTWilliamsReview) -> dict:
        all_request_xmls: list[str] = []
        all_response_xmls: list[str] = []
        latest_mismatches: list[dict] = []
        # FT Williams can acknowledge an update before every Schedule A and
        # nested broker row is visible to subsequent query calls. Give the
        # vendor read model enough time to converge before declaring a real
        # verification failure.
        max_attempts = 8
        for attempt in range(max_attempts):
            result = await self._verify_update_readback_once(review)
            all_request_xmls.extend(result["request_xmls"])
            all_response_xmls.extend(result["response_xmls"])
            latest_mismatches = result["mismatches"]
            if not latest_mismatches:
                return {
                    "success": True,
                    "mismatches": [],
                    "request_xml": "\n\n".join(all_request_xmls) or None,
                    "response_xml": "\n\n".join(all_response_xmls) or None,
                }
            if attempt < max_attempts - 1:
                await asyncio.sleep(min(2**attempt, 5))
        return {
            "success": False,
            "mismatches": latest_mismatches,
            "request_xml": "\n\n".join(all_request_xmls) or None,
            "response_xml": "\n\n".join(all_response_xmls) or None,
        }

    @staticmethod
    def _schedule_a_verification_confirms_corruption(mismatches: list[dict]) -> bool:
        """Restore only when read-back proves the replacement set was damaged.

        A rejected or normalized field is not evidence that a Schedule A was
        deleted. Replacing the full set again in that situation creates more
        risk. Missing schedules and changed broker-row counts are structural
        failures and are safe reasons to restore the pre-send snapshot.
        """
        for mismatch in mismatches:
            if str(mismatch.get("form") or "") != "DOLScheduleAData":
                continue
            if str(mismatch.get("category") or "").upper() == "STRUCTURE":
                return True
            tag = str(mismatch.get("tag") or "")
            if tag in {"DOLScheduleAData", "DOLSubPartData/Broker"}:
                return True
        return False

    async def _verify_update_readback_once(self, review: FTWilliamsReview) -> dict:
        identity = self._current_query_identity_from_review(review)
        request_xmls: list[str] = []
        response_xmls: list[str] = []
        mismatches: list[dict] = []
        form_documents = self._update_documents(review.update_xml_5500, "DOL5500Data")
        schedule_documents = self._schedule_update_documents_with_sequences(review)

        if form_documents:
            response = await self.ftwilliams.run_query(
                FTWilliamsQueryRequest(operation="query_5500", send=True, **identity)
            )
            request_xmls.append(response.request_xml)
            if response.raw_response:
                response_xmls.append(response.raw_response)
            status = next(
                (
                    item
                    for item in response.statuses
                    if str(item.error_code or "") == "0" and item.query_results
                ),
                None,
            )
            if not response.success or not status:
                mismatches.append(
                    {
                        "form": "DOL5500Data",
                        "tag": "DOL5500Data",
                        "reason": response.error or self._status_error(response.statuses) or "Current Form 5500 could not be read back.",
                    }
                )
            else:
                mismatches.extend(
                    self._compare_readback_document(
                        FormType.FORM_5500,
                        form_documents[0],
                        status.query_results,
                    )
                )

        if schedule_documents:
            statuses, schedule_requests, schedule_responses, schedule_error = await self._query_schedule_a_readback(
                review,
                identity,
                require_full_scan=len(schedule_documents) > len(review.schedule_a_records or []),
            )
            request_xmls.extend(schedule_requests)
            response_xmls.extend(schedule_responses)
            unused_statuses = list(statuses)
            for document in schedule_documents:
                matched = self._match_readback_schedule_status(document, unused_statuses)
                if not matched:
                    mismatches.append(
                        {
                            "form": "DOLScheduleAData",
                            "tag": "DOLScheduleAData",
                            "category": "STRUCTURE",
                            "expected": document.get("InsContractNum") or document.get("InsCarrierName") or "Schedule A",
                            "reason": schedule_error or "The sent Schedule A record could not be found during read-back.",
                        }
                    )
                    continue
                unused_statuses.remove(matched)
                mismatches.extend(
                    self._compare_readback_document(
                        FormType.SCHEDULE_A,
                        document,
                        matched.query_results,
                        actual_subparts=matched.query_subparts,
                    )
                )

        if not form_documents and not schedule_documents:
            mismatches.append(
                {
                    "form": "FTW",
                    "tag": "DataBatch",
                    "reason": "No sent FT Williams documents were available for read-back verification.",
                }
            )
        return {
            "mismatches": mismatches,
            "request_xmls": request_xmls,
            "response_xmls": response_xmls,
        }

    def _schedule_update_documents_with_sequences(self, review: FTWilliamsReview) -> list[dict]:
        documents = self._update_documents(review.update_xml_schedule_a, "DOLScheduleAData")
        # Replace-style XML is built in schedule_a_records order. Keep the
        # original FTW sequence beside each parsed document so identity-poor
        # legacy rows can be matched unambiguously during read-back without
        # adding an unsupported element to the outbound XML.
        for index, document in enumerate(documents):
            if index >= len(review.schedule_a_records or []):
                break
            sequence = str((review.schedule_a_records[index] or {}).get("ftw_seq_no") or "").strip()
            if sequence:
                document["__ftw_seq_no"] = sequence
        return documents

    async def _query_schedule_a_readback(
        self,
        review: FTWilliamsReview,
        identity: dict,
        *,
        require_full_scan: bool,
    ) -> tuple[list[FTWilliamsStatusItem], list[str], list[str], str | None]:
        known_sequences = sorted(
            {
                str(record.get("ftw_seq_no") or "").strip()
                for record in review.schedule_a_records or []
                if str(record.get("ftw_seq_no") or "").strip()
            },
            key=self._sequence_sort_key,
        )
        if require_full_scan or not known_sequences:
            return await self._query_schedule_a_statuses(identity)

        responses = await asyncio.gather(
            *(
                self.ftwilliams.run_query(
                    FTWilliamsQueryRequest(
                        operation="query_schedule_a",
                        send=True,
                        ftw_seq_no=sequence,
                        **identity,
                    )
                )
                for sequence in known_sequences
            )
        )
        statuses: list[FTWilliamsStatusItem] = []
        request_xmls: list[str] = []
        response_xmls: list[str] = []
        errors: list[str] = []
        for sequence, response in zip(known_sequences, responses):
            request_xmls.append(response.request_xml)
            if response.raw_response:
                response_xmls.append(response.raw_response)
            if response.success:
                for status in response.statuses:
                    if not status.ftw_seq_no:
                        status.ftw_seq_no = sequence
                    if str(status.error_code or "") == "0" and status.query_results:
                        statuses.append(status)
            else:
                error = response.error or self._status_error(response.statuses, ignore_error_codes={"59"})
                if error:
                    errors.append(error)
        returned_sequences = {
            str(status.ftw_seq_no or "").strip()
            for status in statuses
            if str(status.ftw_seq_no or "").strip()
        }
        if set(known_sequences) - returned_sequences:
            scanned, scan_requests, scan_responses, scan_error = await self._query_schedule_a_statuses(identity)
            statuses = self._merge_schedule_statuses(statuses, scanned)
            request_xmls.extend(scan_requests)
            response_xmls.extend(scan_responses)
            if scan_error:
                errors.append(scan_error)
        return statuses, request_xmls, response_xmls, "; ".join(errors) or None

    def _update_documents(self, xml: str | None, data_tag: str) -> list[dict]:
        if not xml:
            return []
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []
        metadata = {
            "TransactionType",
            "EditCheck",
            "CustomerID",
            "PlanID",
            "FTWCustomerID",
            "FTWPlanID",
            "FTWSeqNo",
            "Year",
        }
        documents: list[dict] = []
        for element in root.findall(f".//{data_tag}"):
            ftw_seq_no = str(element.findtext("FTWSeqNo") or "").strip()
            values = {
                child.tag: str(child.text or "").strip()
                for child in list(element)
                if child.tag not in metadata and str(child.text or "").strip()
            }
            subparts: dict[str, list[dict[str, str]]] = {}
            for container in element.findall("./DOLSubPartData"):
                for record in list(container):
                    row = {
                        child.tag: str(child.text or "").strip()
                        for child in list(record)
                        if str(child.text or "").strip()
                    }
                    if row:
                        subparts.setdefault(record.tag, []).append(row)
            if subparts:
                values["__subparts__"] = subparts
            if ftw_seq_no:
                values["__ftw_seq_no"] = ftw_seq_no
            if values:
                documents.append(values)
        return documents

    def _match_readback_schedule_status(
        self,
        expected: dict[str, str],
        statuses: list[FTWilliamsStatusItem],
    ) -> FTWilliamsStatusItem | None:
        if not statuses:
            return None
        expected_seq_no = str(expected.get("__ftw_seq_no") or "").strip()
        if expected_seq_no:
            exact_sequence_matches = [
                status
                for status in statuses
                if str(status.ftw_seq_no or "").strip() == expected_seq_no
            ]
            if len(exact_sequence_matches) == 1:
                _, conflicts = self._readback_schedule_identity_score(expected, exact_sequence_matches[0])
                if not conflicts:
                    return exact_sequence_matches[0]
        scored: list[tuple[int, FTWilliamsStatusItem]] = []
        for status in statuses:
            score, conflicts = self._readback_schedule_identity_score(expected, status)
            if not conflicts and score > 0:
                scored.append((score, status))
        if not scored:
            return statuses[0] if not expected_seq_no and len(statuses) == 1 else None
        scored.sort(key=lambda item: (-item[0], self._sequence_sort_key(item[1].ftw_seq_no)))
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return None
        return scored[0][1]

    def _readback_schedule_identity_score(
        self,
        expected: dict[str, str],
        status: FTWilliamsStatusItem,
    ) -> tuple[int, int]:
        score = 0
        conflicts = 0
        identity_tags = [
            ("InsContractNum", 8),
            ("InsCarrierEIN", 7),
            ("InsCarrierNAICCode", 6),
            ("InsCarrierName", 4),
            ("ScheduleDesc", 3),
        ]
        for tag, weight in identity_tags:
            expected_value = expected.get(tag)
            actual_value = self._readback_value(status.query_results, FormType.SCHEDULE_A, tag)
            if not expected_value or not actual_value:
                continue
            if self._readback_values_equal(expected_value, actual_value):
                score += weight
            elif tag in {"InsContractNum", "InsCarrierEIN", "InsCarrierNAICCode"}:
                conflicts += 1
        return score, conflicts

    def _compare_readback_document(
        self,
        form_type: FormType,
        expected: dict,
        actual: dict[str, str],
        *,
        actual_subparts: dict[str, list[dict[str, str]]] | None = None,
    ) -> list[dict]:
        mismatches: list[dict] = []
        for tag, expected_value in expected.items():
            if tag in {"__subparts__", "__ftw_seq_no"}:
                continue
            actual_value = self._readback_value(actual, form_type, tag)
            if actual_value is None:
                mismatches.append(
                    {
                        "form": form_type.value,
                        "tag": tag,
                        "expected": expected_value,
                        "actual": None,
                        "reason": "FT Williams did not return this sent field during read-back.",
                    }
                )
                continue
            if not self._readback_values_equal(expected_value, actual_value, tag=tag):
                mismatches.append(
                    {
                        "form": form_type.value,
                        "tag": tag,
                        "expected": expected_value,
                        "actual": actual_value,
                        "reason": "FT Williams returned a different value after the update.",
                    }
                )
        expected_brokers = list((expected.get("__subparts__") or {}).get("Broker") or [])
        if form_type == FormType.SCHEDULE_A and expected_brokers:
            actual_brokers = schedule_a_broker_multipart_rows(
                actual,
                query_subparts=actual_subparts,
            )
            if len(actual_brokers) != len(expected_brokers):
                mismatches.append(
                    {
                        "form": form_type.value,
                        "tag": "DOLSubPartData/Broker",
                        "category": "STRUCTURE",
                        "expected": len(expected_brokers),
                        "actual": len(actual_brokers),
                        "reason": "FT Williams returned a different broker row count after the update.",
                    }
                )
            matched_actual_brokers = self._match_readback_broker_rows(expected_brokers, actual_brokers)
            for index, expected_broker in enumerate(expected_brokers):
                actual_broker = matched_actual_brokers[index] if index < len(matched_actual_brokers) else {}
                for tag, expected_value in expected_broker.items():
                    actual_value = actual_broker.get(tag)
                    if actual_value is None or not self._readback_values_equal(expected_value, actual_value, tag=tag):
                        mismatches.append(
                            {
                                "form": form_type.value,
                                "tag": f"Broker[{index + 1}]/{tag}",
                                "expected": expected_value,
                                "actual": actual_value,
                                "reason": "FT Williams returned a different broker value after the update.",
                            }
                        )
        return mismatches

    def _match_readback_broker_rows(
        self,
        expected_rows: list[dict[str, str]],
        actual_rows: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Match FT Williams broker rows by stable identity, not response order."""
        unused = set(range(len(actual_rows)))
        matched: list[dict[str, str]] = []
        for expected in expected_rows:
            expected_name, expected_address = self._broker_readback_identity(expected)
            candidates: list[int] = []
            if expected_name and expected_address:
                candidates = [
                    index
                    for index in unused
                    if self._broker_readback_identity(actual_rows[index])
                    == (expected_name, expected_address)
                ]
            if expected_name:
                if len(candidates) != 1:
                    candidates = [
                        index
                        for index in unused
                        if self._broker_readback_identity(actual_rows[index])[0] == expected_name
                    ]
            if len(candidates) != 1 and expected_address:
                address_candidates = [
                    index
                    for index in unused
                    if self._broker_readback_identity(actual_rows[index])[1] == expected_address
                ]
                if len(address_candidates) == 1:
                    candidates = address_candidates
            if len(candidates) != 1 and len(unused) == 1 and len(expected_rows) == 1:
                candidates = list(unused)
            if len(candidates) == 1:
                selected = candidates[0]
                unused.remove(selected)
                matched.append(actual_rows[selected])
            else:
                matched.append({})
        return matched

    @staticmethod
    def _broker_readback_identity(row: dict[str, str]) -> tuple[str, str]:
        def normalized(value: object) -> str:
            return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())

        name = normalized(row.get("NameXX"))
        address = normalized(
            " ".join(
                str(row.get(tag) or "")
                for tag in ("AddressLine1XX", "AddressLine2XX", "CityXX", "StateXX", "ZipCodeXX")
            )
        )
        return name, address

    def _readback_value(
        self,
        actual: dict[str, str],
        form_type: FormType,
        update_tag: str,
    ) -> str | None:
        candidates = self._readback_tag_candidates(form_type, update_tag)
        actual_by_key = {str(key or "").casefold(): str(value or "").strip() for key, value in actual.items()}
        for candidate in candidates:
            value = actual_by_key.get(candidate.casefold())
            if value is not None:
                return value
        return None

    def _readback_tag_candidates(self, form_type: FormType, update_tag: str) -> list[str]:
        if form_type == FormType.FORM_5500:
            mapped = next(
                (
                    FORM_5500_CURRENT_TAGS_BY_RULE.get(rule_key)
                    for rule_key, tag in FORM_5500_UPDATE_TAGS_BY_RULE.items()
                    if tag == update_tag
                ),
                None,
            )
        else:
            mapped = next(
                (
                    SCHEDULE_A_CURRENT_TAGS_BY_RULE.get(rule_key)
                    for rule_key, tag in SCHEDULE_A_TAGS_BY_RULE.items()
                    if tag == update_tag
                ),
                None,
            )
        candidates = [update_tag]
        if mapped and mapped not in candidates:
            candidates.append(mapped)
        dynamic = re.fullmatch(r"(CommPdAmt|FeesPdAmt|FeesPdText|Code)(\d+)", update_tag)
        if dynamic:
            indexed = f"{dynamic.group(1)}0{dynamic.group(2)}"
            if indexed not in candidates:
                candidates.append(indexed)
        return candidates

    def _readback_values_equal(
        self,
        expected: object,
        actual: object,
        *,
        tag: str | None = None,
    ) -> bool:
        # FT Williams commonly stores monetary inputs as whole dollars.  Use
        # the same tag-aware normalization as the review table so harmless
        # cents rounding and indicator encodings do not become false failures.
        return not values_meaningfully_different(actual, expected, tag=tag)

    def _approval_blocking_error(self, fields: list[ExtractedField]) -> str | None:
        high_missing = len(
            [
                field
                for field in fields
                if field.status == ExtractedFieldStatus.MISSING and field.priority == FieldPriority.HIGH
            ]
        )
        unmapped = len([field for field in fields if field.status == ExtractedFieldStatus.UNMAPPED])
        if not high_missing and not unmapped:
            return None
        parts = []
        if high_missing:
            parts.append(f"{high_missing} high-priority missing field{'s' if high_missing != 1 else ''}")
        if unmapped:
            parts.append(f"{unmapped} unmapped field{'s' if unmapped != 1 else ''}")
        return f"Resolve {' and '.join(parts)} before approving this filing."

    def _effective_schedule_a_classification(
        self,
        filing,
        computed: ScheduleAClassification,
        *,
        preserve_confirmed: bool = False,
    ) -> ScheduleAClassification:
        if (
            preserve_confirmed
            and filing.schedule_a_contract_type_confirmed
            and filing.schedule_a_contract_type not in {
                ScheduleAContractType.UNKNOWN,
                ScheduleAContractType.NEEDS_REVIEW,
            }
        ):
            return ScheduleAClassification(
                filing.schedule_a_contract_type,
                filing.schedule_a_contract_type_reason or "Preserved during an isolated field decision.",
                filing.schedule_a_contract_type_confidence,
                tuple(filing.schedule_a_contract_type_evidence or []),
            )
        return computed

    def _schedule_a_contract_type_block_reason(
        self,
        contract_type: ScheduleAContractType,
        mismatch: bool,
        confirmed: bool,
    ) -> str | None:
        if contract_type in {ScheduleAContractType.UNKNOWN, ScheduleAContractType.NEEDS_REVIEW}:
            return "Confirm whether this Schedule A is experience-rated or nonexperience-rated before approving or sending."
        if mismatch and not confirmed:
            return "Schedule A contract type differs from FT Williams current data. Confirm the correct classification before approving or sending."
        return None

    def _review_contract_type_block_reason(self, review: FTWilliamsReview) -> str | None:
        return self._schedule_a_contract_type_block_reason(
            review.schedule_a_contract_type,
            bool(review.schedule_a_contract_type_mismatch),
            bool(review.schedule_a_contract_type_confirmed),
        )

    async def _record_update_failure(self, repo, filing_id: str, review: FTWilliamsReview, error_message: str) -> None:
        review.status = FTWilliamsReviewStatus.UPDATE_FAILED
        review.error_message = error_message
        review.client_error = self._normalize_review_error(error_message, review.fields)
        self._activate_failure(review, error_message)
        await repo.upsert_ftwilliams_review(review)
        await repo.update_filing(
            filing_id,
            {
                "status": FilingStatus.FAILED,
                "approved_at": None,
                "error_message": error_message,
            },
        )
        await repo.add_audit(
            AuditLog(
                filing_id=filing_id,
                event="FTWILLIAMS_UPDATE_FAILED",
                message="FT Williams update failed.",
                details={
                    "error": error_message,
                    "error_code": review.active_failure_client_error.code if review.active_failure_client_error else None,
                    "operation_diagnostics": [item.model_dump(mode="json") for item in review.update_diagnostics],
                    "edit_check_issues": [
                        item.model_dump(mode="json")
                        for item in (
                            review.edit_check_final_issues
                            or review.edit_check_baseline_issues
                            or []
                        )
                    ],
                },
            )
        )

    async def _record_ambiguous_update(
        self,
        repo,
        filing_id: str,
        review: FTWilliamsReview,
        attempted_fields: dict,
        attempted_field_keys: set[str],
        responses: list,
        *,
        schedule_a_restore: dict | None = None,
        recovery_responses: list | None = None,
    ) -> FTWilliamsReview:
        schedule_a_restore = schedule_a_restore or {
            "attempted": False,
            "success": None,
            "response_xml": None,
            "verification_request_xml": None,
            "verification_response_xml": None,
            "verification_mismatches": [],
        }
        recovery_responses = list(recovery_responses or [])
        error_details = "; ".join(
            filter(
                None,
                [response.error or self._status_error(response.statuses) for response in responses],
            )
        )
        error_message = (
            "FT Williams received the update request, but its response was empty or malformed, so the update "
            "could not be confirmed. The last valid FT Williams snapshot was preserved; verify the result before retrying."
        )
        if error_details:
            error_message = f"{error_message} {error_details}"
        if schedule_a_restore["attempted"]:
            if schedule_a_restore["success"]:
                error_message += " The original Schedule A records were automatically restored and verified."
            else:
                error_message += " Automatic restoration could not be verified; manual FT Williams recovery is required."

        review = review.model_copy(deep=True)
        review.status = FTWilliamsReviewStatus.UPDATE_UNKNOWN
        review.current_query_success = False
        review.bring_forward_required = False
        review.update_response_xml = "\n\n".join(
            filter(
                None,
                [response.raw_response or response.error or self._status_error(response.statuses) for response in responses],
            )
        ) or None
        review.update_verification_attempted = False
        review.update_verification_success = None
        review.update_verification_mismatches = []
        review.schedule_a_restore_attempted = bool(schedule_a_restore["attempted"])
        review.schedule_a_restore_success = schedule_a_restore["success"]
        review.schedule_a_restore_response_xml = schedule_a_restore["response_xml"]
        review.schedule_a_restore_verification_request_xml = schedule_a_restore["verification_request_xml"]
        review.schedule_a_restore_verification_response_xml = schedule_a_restore["verification_response_xml"]
        review.schedule_a_restore_verification_mismatches = list(
            schedule_a_restore["verification_mismatches"] or []
        )
        review.update_attempted_count = len(attempted_field_keys)
        review.update_confirmed_count = 0
        review.update_remaining_count = len(attempted_field_keys)
        review.update_retry_count = 0
        review.update_diagnostics = self._operation_diagnostics([*responses, *recovery_responses])
        review.update_results = [
            {
                **attempted_fields[key],
                "status": "VERIFICATION_REQUIRED",
                "reason": "FT Williams returned no usable confirmation. Verify the current value before retrying.",
            }
            for key in attempted_field_keys
            if key in attempted_fields
        ]
        review.error_message = error_message
        review.client_error = self._normalize_review_error(error_message, review.fields)
        self._activate_failure(review, error_message)
        await repo.upsert_ftwilliams_review(review)
        await repo.update_filing(
            filing_id,
            {
                "status": FilingStatus.FAILED,
                "approved_at": None,
                "error_message": error_message,
            },
        )
        await repo.add_event(ReviewEvent(filing_id=filing_id, type="APPROVE_AND_FTW_UPDATE", reason=""))
        await repo.add_audit(
            AuditLog(
                filing_id=filing_id,
                event="FTWILLIAMS_UPDATE_UNKNOWN",
                message="FT Williams update outcome requires verification.",
                details={
                    "error": error_message,
                    "update_attempted_count": len(attempted_field_keys),
                    "update_confirmed_count": 0,
                    "update_remaining_count": len(attempted_field_keys),
                    "update_retry_count": 0,
                    "verification_attempted": False,
                    "verification_success": None,
                    "schedule_a_restore_attempted": review.schedule_a_restore_attempted,
                    "schedule_a_restore_success": review.schedule_a_restore_success,
                    "error_code": review.active_failure_client_error.code if review.active_failure_client_error else None,
                    "operation_diagnostics": [item.model_dump(mode="json") for item in review.update_diagnostics],
                },
            )
        )
        return review

    def _activate_failure(self, review: FTWilliamsReview, error_message: str) -> None:
        review.active_failure = True
        review.active_failure_reason = error_message
        review.active_failure_client_error = self._normalize_review_error(error_message, review.fields)
        review.active_failure_type = classify_ftwilliams_failure(review)
        review.active_failure_issue_count = None
        review.active_failure_issue_groups = []
        review.active_failure_issue_count, review.active_failure_issue_groups = failure_issue_groups(review)
        review.active_failure_at = datetime.utcnow()
        review.failure_dismissed_at = None
        review.failure_dismissed_reason = None

    def _operation_diagnostics(self, responses: list) -> list[FTWilliamsOperationDiagnostic]:
        diagnostics: list[FTWilliamsOperationDiagnostic] = []
        for response in responses:
            raw_response = str(response.raw_response or "").strip()
            parse_failed = any(str(status.error_code or "") == "PARSE_ERROR" for status in response.statuses or [])
            edit_check_issues = (
                self.ftwilliams.parse_edit_check_issues(response.statuses or [])
                if str(response.operation or "").startswith("edit_checks")
                else []
            )
            status_error = next(
                (status for status in response.statuses or [] if str(status.error_code or "") not in {"", "0"}),
                None,
            )
            if not response.sent:
                outcome_code = "NOT_SENT"
            elif response.http_status is None:
                outcome_code = "NO_RESPONSE"
            elif not 200 <= response.http_status < 300:
                outcome_code = "HTTP_ERROR"
            elif not raw_response:
                outcome_code = "EMPTY_RESPONSE"
            elif parse_failed:
                outcome_code = "MALFORMED_RESPONSE"
            elif edit_check_issues:
                outcome_code = "EDIT_CHECK_FAILED"
            elif response.success:
                outcome_code = "ACCEPTED"
            else:
                outcome_code = "REJECTED"
            headers = response.response_headers or {}
            excerpt = re.sub(r"\s+", " ", self.ftwilliams.mask_key_id(raw_response)).strip()[:1200] or None
            diagnostics.append(
                FTWilliamsOperationDiagnostic(
                    operation=response.operation,
                    sent=response.sent,
                    http_status=response.http_status,
                    outcome_code=outcome_code,
                    response_received=bool(raw_response),
                    response_content_type=headers.get("content-type"),
                    response_content_length=headers.get("content-length"),
                    request_id=(
                        headers.get("x-request-id")
                        or headers.get("x-amzn-requestid")
                        or headers.get("x-amz-request-id")
                        or headers.get("cf-ray")
                    ),
                    elapsed_ms=response.elapsed_ms,
                    error_code=(
                        str(status_error.error_code)
                        if status_error and status_error.error_code
                        else ", ".join(dict.fromkeys(item.code for item in edit_check_issues)) or None
                    ),
                    error_description=(
                        status_error.error_desc
                        if status_error and status_error.error_desc
                        else response.error
                        or (
                            f"FT Williams returned {len(edit_check_issues)} Edit Check "
                            f"issue{'s' if len(edit_check_issues) != 1 else ''}."
                            if edit_check_issues
                            else None
                        )
                    ),
                    response_excerpt=excerpt,
                )
            )
        return diagnostics

    def _normalize_review_error(
        self,
        error_message: str | None,
        fields: list[FTWilliamsComparisonField] | None = None,
    ) -> ClientFacingError | None:
        error = normalize_client_error(error_message)
        if not error or not error.rejected_fields or not fields:
            return error

        by_tag = {field.ftw_tag: field for field in fields if field.ftw_tag}
        by_field_id = {field.field_id: field for field in fields if field.field_id}
        for field in fields:
            if not field.rule_key:
                continue
            if field.rule_key == "form_5500_part_i_1d_plan_sponsor_name":
                # Preserve labels for errors stored before the FTW schema typo
                # was corrected from SPONS_DFE_NAME0 to SPONSOR_DFE_NAME0.
                by_tag.setdefault("SPONS_DFE_NAME0", field)
            for mapping in (
                FORM_5500_TAGS_BY_RULE,
                FORM_5500_CURRENT_TAGS_BY_RULE,
                FORM_5500_UPDATE_TAGS_BY_RULE,
                SCHEDULE_A_CURRENT_TAGS_BY_RULE,
                SCHEDULE_A_TAGS_BY_RULE,
            ):
                alias_tag = mapping.get(field.rule_key)
                if alias_tag:
                    by_tag.setdefault(alias_tag, field)
        for rejected in error.rejected_fields:
            comparison = by_tag.get(rejected.tag) or by_field_id.get(rejected.field_id)
            if not comparison:
                continue
            rejected.label = rejected.label or comparison.label
            rejected.field_id = rejected.field_id or comparison.field_id
            rejected.form_type = rejected.form_type or comparison.form_type
            rejected.value = rejected.value or comparison.proposed_value
        return error

    def _missing_required_schedule_a_payload(self, review: FTWilliamsReview) -> str | None:
        if not self._schedule_a_payload_required(review):
            return None
        if review.update_xml_schedule_a and "DOLScheduleAData" in review.update_xml_schedule_a:
            return None
        validation_error = self._friendly_payload_validation_error(review.error_message)
        if validation_error:
            return validation_error
        return "Schedule A payload is required before sending this Form 5500 update because Schedule A is attached."

    @staticmethod
    def _friendly_payload_validation_error(error_message: str | None) -> str | None:
        message = str(error_message or "")
        if "FT Williams pre-send validation failed:" not in message:
            return None
        match = re.search(
            r"\b(Name|AddressLine1|AddressLine2|City|State|ZipCode|CommPdAmt|FeesPdAmt|FeesPdText|Code)(\d+)"
            r":(.*?) \(([^()]*)\)(?:;|$)",
            message,
        )
        if not match:
            return message[message.index("FT Williams pre-send validation failed:"):].strip()
        field, row_number, value, reason = match.groups()
        labels = {
            "Name": "Broker name",
            "AddressLine1": "Address line 1",
            "AddressLine2": "Address line 2",
            "City": "City",
            "State": "State",
            "ZipCode": "ZIP code",
            "CommPdAmt": "Commission amount",
            "FeesPdAmt": "Fee amount",
            "FeesPdText": "Fee purpose",
            "Code": "Organization code",
        }
        return f"Broker row {row_number} - {labels[field]}: {reason}. Current value: {value.strip()}"

    @staticmethod
    def _review_plan_year_block_reason(review: FTWilliamsReview) -> str | None:
        if not review.plan_year_conflict or review.plan_year_resolution:
            return None
        return (
            "Resolve the plan year conflict before approval: choose the Plan Worksheet dates "
            "or keep the current FT Williams dates for Form 5500 and every attached Schedule A."
        )

    def _schedule_a_payload_required(self, review: FTWilliamsReview) -> bool:
        return any(
            field.form_type == FormType.SCHEDULE_A
            and field.changed
            and field.update_included
            for field in review.fields or []
        )

    def _comparison_fields(
        self,
        fields: list[ExtractedField],
        form_5500_current: dict[str, str],
        schedule_a_current: dict[str, str],
        update_fields: list[ExtractedField] | None = None,
        schedule_a_contract_type: ScheduleAContractType | None = None,
    ) -> list[FTWilliamsComparisonField]:
        comparison: list[FTWilliamsComparisonField] = []
        update_field_ids = {id(field) for field in update_fields} if update_fields is not None else None
        for field in sorted(fields, key=lambda item: (str(item.form_type or ""), item.mapped_label or item.source_field_name)):
            if field.priority == FieldPriority.IGNORE:
                continue
            tag = resolve_ftw_current_tag(field)
            current_values = form_5500_current if field.form_type == FormType.FORM_5500 else schedule_a_current
            current_value = resolve_ftw_current_value(field, current_values)
            extracted_proposed_value = str(field.proposed_value or "")
            # A blank extraction must never visually suggest that an existing FTW
            # value will be erased. Show the retained current value in the review
            # column while still excluding blank extraction fields from updates.
            proposed_value = extracted_proposed_value if extracted_proposed_value.strip() else current_value
            update_allowed = update_field_ids is None or id(field) in update_field_ids
            update_exclusion_reason = (
                self._unsafe_form_5500_field_reason(field, current_values)
                if field.form_type == FormType.FORM_5500 and not update_allowed
                else None
            )
            contract_type_allowed = (
                field.form_type != FormType.SCHEDULE_A
                or schedule_a_contract_type is None
                or schedule_a_contract_type_allows_rule(schedule_a_contract_type, field.mapped_rule_key)
            )
            changed = values_meaningfully_different(current_value, proposed_value, tag=tag) and contract_type_allowed
            comparison.append(
                FTWilliamsComparisonField(
                    field_id=field.id,
                    rule_key=field.mapped_rule_key,
                    label=field.mapped_label or field.source_field_name,
                    form_type=field.form_type,
                    source_document_type=field.source_document_type,
                    ftw_tag=tag,
                    current_value=current_value,
                    extracted_value=field.value,
                    proposed_value=proposed_value,
                    confidence=field.confidence,
                    priority=field.priority,
                    extraction_status=field.status,
                    changed=changed,
                    update_included=bool(
                        tag
                        and extracted_proposed_value.strip()
                        and update_allowed
                        and contract_type_allowed
                    ),
                    update_exclusion_reason=update_exclusion_reason,
                )
            )
        return comparison

    def _safe_update_fields(
        self,
        fields: list[ExtractedField],
        form_type: FormType,
        current_values: dict[str, str],
        *,
        schedule_update_blocked: bool = False,
        has_structured_schedule_a_brokers: bool = False,
    ) -> list[ExtractedField]:
        safe_fields: list[ExtractedField] = []
        for field in fields:
            if field.form_type != form_type:
                continue
            if not resolve_ftw_update_tag(field):
                continue
            if form_type != FormType.SCHEDULE_A:
                if self._unsafe_form_5500_field_reason(field, current_values):
                    continue
                safe_fields.append(field)
                continue
            if schedule_update_blocked:
                continue
            if (
                has_structured_schedule_a_brokers
                and self._is_schedule_a_broker_flat_field(field)
                and field.status != ExtractedFieldStatus.EDITED
            ):
                continue
            if self._unsafe_schedule_a_field_reason(field, fields, current_values):
                continue
            safe_fields.append(field)
        return safe_fields

    @staticmethod
    def _unsafe_form_5500_field_reason(
        field: ExtractedField,
        current_values: dict[str, str],
    ) -> str | None:
        if str(field.mapped_rule_key or "") != "form_5500_part_i_2a_plan_administrator_name":
            return None
        if str(current_values.get("AdminNameSameAsPlanSponsInd") or "").strip() != "1":
            return None
        sponsor_name = str(current_values.get("SDName") or "").strip()
        proposed_name = str(field.proposed_value or "").strip()
        if not sponsor_name or not proposed_name:
            return None
        if not values_meaningfully_different(sponsor_name, proposed_name, tag="ADMINName"):
            return None
        return (
            "Changing the administrator from the plan sponsor requires the complete FT Williams "
            "administrator contact block, including a phone number. This filing only provides the name."
        )

    def _is_schedule_a_broker_flat_field(self, field: ExtractedField) -> bool:
        return str(field.mapped_rule_key or "") in {
            "schedule_a_part_i_3a_name_of_agent_broker_person",
            "schedule_a_part_i_3b_amount_of_commissions",
            "schedule_a_part_i_3c_amount_of_fees",
            "schedule_a_part_i_3d_purpose",
            "schedule_a_part_i_3e_organizational_code",
        }

    def _mark_structured_broker_comparisons(self, comparisons: list[FTWilliamsComparisonField], rows: list) -> None:
        if not rows:
            return
        broker_rules = {
            "schedule_a_part_i_3a_name_of_agent_broker_person",
            "schedule_a_part_i_3b_amount_of_commissions",
            "schedule_a_part_i_3c_amount_of_fees",
            "schedule_a_part_i_3d_purpose",
            "schedule_a_part_i_3e_organizational_code",
        }
        for comparison in comparisons:
            if comparison.rule_key in broker_rules and not comparison.update_included:
                comparison.update_exclusion_reason = (
                    "Managed in the Schedule A broker rows section so each broker is matched and updated separately."
                )

    def _normalized_schedule_a_broker_rows(self, rows) -> list:
        normalized: list[ScheduleABrokerRow] = []
        for row in rows or []:
            if isinstance(row, ScheduleABrokerRow):
                normalized.append(row)
            elif isinstance(row, dict):
                normalized.append(ScheduleABrokerRow.model_validate(row))
        return merge_schedule_a_broker_rows(normalized, [])

    def _resolve_schedule_a_brokers(
        self,
        rows: list,
        records: list[dict],
        ftw_seq_no: object,
        previous_matches: list[ScheduleABrokerMatch],
        *,
        create_new: bool,
    ) -> tuple[list[ScheduleABrokerMatch], list[ScheduleABrokerRow | None]]:
        extracted_rows = [
            row if isinstance(row, ScheduleABrokerRow) else ScheduleABrokerRow.model_validate(row)
            for row in rows or []
        ]
        if not extracted_rows:
            return [], []
        sequence = str(ftw_seq_no or "").strip()
        selected_record = next(
            (
                record for record in records or []
                if sequence and str(record.get("ftw_seq_no") or "").strip() == sequence
            ),
            None,
        )
        current_rows = [] if create_new else current_schedule_a_broker_rows(selected_record)
        decisions: dict[int, dict[str, object]] = {}
        for match in previous_matches or []:
            if match.status == "CONFIRMED_NEW":
                decisions[match.extracted_index] = {"create_new": True}
            elif match.status == "CONFIRMED" and match.ftw_index is not None:
                decisions[match.extracted_index] = {"ftw_index": match.ftw_index}
        if create_new:
            decisions = {index: {"create_new": True} for index in range(len(extracted_rows))}
        matches = match_schedule_a_brokers(extracted_rows, current_rows, decisions=decisions)
        if not all(match.resolved for match in matches):
            return matches, []
        return matches, resolved_schedule_a_broker_rows(extracted_rows, current_rows, matches)

    def _same_schedule_a_selection(self, previous: dict | None, current: dict | None) -> bool:
        if bool((previous or {}).get("create_new")) != bool((current or {}).get("create_new")):
            return False
        return str((previous or {}).get("ftw_seq_no") or "").strip() == str(
            (current or {}).get("ftw_seq_no") or ""
        ).strip()

    def _normalized_schedule_a_worksheet_summaries(self, rows) -> list:
        normalized = []
        for row in rows or []:
            if hasattr(row, "model_dump"):
                normalized.append(row)
            elif isinstance(row, dict):
                normalized.append(row)
        return normalized

    def _fields_with_schedule_a_summary_override(
        self,
        fields: list[ExtractedField],
        summaries: list,
        schedule_desc: object,
    ) -> list[ExtractedField]:
        summary = self._matching_standard_schedule_a_summary(summaries, schedule_desc) or self._matching_united_omaha_schedule_a_summary(summaries, schedule_desc)
        if not summary:
            return fields
        replacements = self._standard_schedule_a_summary_values(summary)
        if not replacements:
            return fields

        now = datetime.utcnow()
        updated: list[ExtractedField] = []
        for field in fields:
            if field.form_type != FormType.SCHEDULE_A or field.mapped_label not in replacements:
                updated.append(field)
                continue
            value = str(replacements.get(field.mapped_label) or "").strip()
            if not value:
                updated.append(field)
                continue
            updated.append(
                field.model_copy(
                    update={
                        "value": value,
                        "proposed_value": value,
                        "confidence": max(float(field.confidence or 0), 0.98),
                        "status": ExtractedFieldStatus.MATCHED,
                        "status_reason": "Matched to selected Schedule A benefit section.",
                        "source_text": f"{self._summary_attr(summary, 'source') or 'Schedule A'} {self._summary_attr(summary, 'coverage') or ''} {self._summary_attr(summary, 'account_number') or ''}".strip(),
                        "updated_at": now,
                    }
                )
            )
        return updated

    def _matching_standard_schedule_a_summary(self, summaries: list, schedule_desc: object):
        standard_summaries = [
            summary
            for summary in summaries or []
            if "standard" in str(self._summary_attr(summary, "source") or "").lower()
        ]
        if not standard_summaries:
            return None
        desc_key = self._standard_schedule_desc_key(str(schedule_desc or ""))
        if not desc_key and len(standard_summaries) == 1:
            return standard_summaries[0]
        if not desc_key:
            return None
        for summary in standard_summaries:
            if self._standard_schedule_desc_key(str(self._summary_attr(summary, "coverage") or "")) == desc_key:
                return summary
        return None

    def _matching_united_omaha_schedule_a_summary(self, summaries: list, schedule_desc: object):
        omaha_summaries = [
            summary
            for summary in summaries or []
            if "united of omaha" in str(self._summary_attr(summary, "source") or "").lower()
        ]
        if not omaha_summaries:
            return None
        desc_key = self._united_omaha_schedule_desc_key(str(schedule_desc or ""))
        if not desc_key and len(omaha_summaries) == 1:
            return omaha_summaries[0]
        if not desc_key:
            return None
        for summary in omaha_summaries:
            coverage_key = self._united_omaha_schedule_desc_key(str(self._summary_attr(summary, "coverage") or ""))
            account_key = self._united_omaha_schedule_desc_key(str(self._summary_attr(summary, "account_number") or ""))
            if coverage_key == desc_key or account_key == desc_key:
                return summary
        return None

    def _standard_schedule_a_summary_values(self, summary) -> dict[str, str]:
        values_by_label: dict[str, str] = {}
        for value in self._summary_attr(summary, "values") or []:
            label = self._summary_attr(value, "label")
            val = self._summary_attr(value, "value")
            if label and val is not None:
                values_by_label[str(label)] = str(val)
        return {
            "1a. Name of Insurance Company": self._summary_attr(summary, "carrier_name"),
            "1b. Insurance Carrier EIN": self._summary_attr(summary, "ein"),
            "1c. NAIC Code": self._summary_attr(summary, "naic_code"),
            "1d. Contract/Policy Number": self._summary_attr(summary, "account_number"),
            "1e. Persons Covered (End of Policy Year)": values_by_label.get("Persons covered"),
            "1f. Policy Year Beginning Date": self._summary_attr(summary, "period_begin"),
            "1g. Policy Year Ending Date": self._summary_attr(summary, "period_end"),
            "3a. Name of Agent/Broker/Person": values_by_label.get("Broker name"),
            "3b. Amount of Commissions": values_by_label.get("3b. Amount of Commissions"),
            "3c. Amount of Fees": values_by_label.get("3c. Amount of Fees"),
            "3d. Purpose": values_by_label.get("3d. Purpose"),
            "3e. Organizational Code": values_by_label.get("3e. Organizational Code"),
            "10a. Total premiums or subscription charges paid to carrier": values_by_label.get("10a. Total premiums or subscription charges paid to carrier"),
            "9a. Premiums: (1) Amount Received": values_by_label.get("9a. Premiums: (1) Amount Received"),
            "9a(2). Increase (decrease) in amount due but unpaid": values_by_label.get("9a(2). Increase (decrease) in amount due but unpaid"),
            "9a(3). Increase (decrease) in unearned premium reserve": values_by_label.get("9a(3). Increase (decrease) in unearned premium reserve"),
            "9a(4). Earned ((1) + (2) - (3))": values_by_label.get("9a(4). Earned ((1) + (2) - (3))"),
            "9b(1). Benefit Charges (1) Claims paid": values_by_label.get("9b(1). Benefit Charges (1) Claims paid"),
            "9b(2). Increase (decrease) in claim reserves": values_by_label.get("9b(2). Increase (decrease) in claim reserves"),
            "9b(3). Incurred claims (add(1) and (2))": values_by_label.get("9b(3). Incurred claims (add(1) and (2))"),
            "9b(4). Claims Charged": values_by_label.get("9b(4). Claims Charged"),
            "9c(1)(A). Commissions": values_by_label.get("9c(1)(A). Commissions"),
            "9c(1)(B). Administrative service or other fees": values_by_label.get("9c(1)(B). Administrative service or other fees"),
            "9c(1)(C). Other Specific acquisition costs": values_by_label.get("9c(1)(C). Other Specific acquisition costs"),
            "9c(1)(D). Other expenses": values_by_label.get("9c(1)(D). Other expenses"),
            "9c(1)(E). Taxes": values_by_label.get("9c(1)(E). Taxes"),
            "9c(1)(F). Charges for risks or other contingencies": values_by_label.get("9c(1)(F). Charges for risks or other contingencies"),
            "9c(1)(G). Other retention charges": values_by_label.get("9c(1)(G). Other retention charges"),
            "9c(1)(H). Total retention": values_by_label.get("9c(1)(H). Total retention"),
            "9c(2). Dividends or retroactive rate refunds": values_by_label.get("9c(2). Dividends or retroactive rate refunds"),
            "9d(1). Status of policyholder reserves at end of year: (1) Amount held to provide benefits after retirement": values_by_label.get(
                "9d(1). Status of policyholder reserves at end of year: (1) Amount held to provide benefits after retirement"
            ),
            "9d(2). Claim reserves": values_by_label.get("9d(2). Claim reserves"),
            "9d(3). Other reserves": values_by_label.get("9d(3). Other reserves"),
            "9e. Dividends or retroactive rate refunds due": values_by_label.get("9e. Dividends or retroactive rate refunds due"),
        }

    def _summary_attr(self, value, key: str):
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)

    def _standard_schedule_desc_key(self, value: str) -> str:
        key = re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
        if key in {"LTD", "LONGTERMDISABILITY"} or "LTD" in key or "LONGTERMDISABILITY" in key:
            return "LTD"
        if "DENTAL" in key:
            return "DENTAL"
        if "VISION" in key:
            return "VISION"
        if "LIFE" in key:
            return "LIFE"
        return key

    def _united_omaha_schedule_desc_key(self, value: str) -> str:
        key = re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
        raw = str(value or "").upper()
        if "LONGTERMDISABILITY" in key or "LTD" in key:
            return "LTD"
        if "SHORTTERMDISABILITY" in key or "STD" in key:
            return "STD"
        if "LIFE" in key:
            return "LIFE"
        if "ADANDD" in key or "ADAD" in key or "AD&D" in raw or "ACCIDENT" in key:
            return "AD&D"
        return key

    def _schedule_a_update_block_reason(self, fields: list[ExtractedField], schedule_a_current: dict[str, str]) -> str | None:
        if not schedule_a_current:
            return None
        worksheet_begin = self._field_value_by_rule(fields, "form_5500_part_i_6_plan_year_beginning_date")
        worksheet_end = self._field_value_by_rule(fields, "form_5500_part_i_7_plan_year_ending_date")
        current_begin = schedule_a_current.get("PlanYearBeginDate")
        current_end = schedule_a_current.get("PlanYearEndDate")
        if worksheet_begin and current_begin and not self._same_date(worksheet_begin, current_begin):
            return "Schedule A updates blocked: FTW Schedule A plan year does not match the Plan Worksheet year."
        if worksheet_end and current_end and not self._same_date(worksheet_end, current_end):
            return "Schedule A updates blocked: FTW Schedule A plan year does not match the Plan Worksheet year."
        return None

    def _plan_year_conflict(
        self,
        fields: list[ExtractedField],
        form_5500_current: dict[str, str],
        schedule_a_current: dict[str, str],
    ) -> dict | None:
        worksheet_begin = self._field_value_by_rule(fields, "form_5500_part_i_6_plan_year_beginning_date")
        worksheet_end = self._field_value_by_rule(fields, "form_5500_part_i_7_plan_year_ending_date")
        form_begin = form_5500_current.get("PlanYearBeginDate")
        form_end = form_5500_current.get("PlanYearEndDate")
        schedule_begin = schedule_a_current.get("PlanYearBeginDate")
        schedule_end = schedule_a_current.get("PlanYearEndDate")
        mismatched = any(
            left and right and not self._same_date(left, right)
            for left, right in (
                (worksheet_begin, form_begin),
                (worksheet_end, form_end),
                (worksheet_begin, schedule_begin),
                (worksheet_end, schedule_end),
            )
        )
        if not mismatched:
            return None
        return {
            "worksheet_begin": worksheet_begin,
            "worksheet_end": worksheet_end,
            "ftw_form_begin": form_begin,
            "ftw_form_end": form_end,
            "ftw_schedule_a_begin": schedule_begin,
            "ftw_schedule_a_end": schedule_end,
        }

    def _effective_plan_year_resolution(
        self,
        review: FTWilliamsReview | None,
        fields: list[ExtractedField],
        form_5500_current: dict[str, str],
        schedule_a_current: dict[str, str],
    ) -> FTWilliamsPlanYearResolution | None:
        if not review or not review.plan_year_resolution:
            return None
        begin = self._field_value_by_rule(fields, "form_5500_part_i_6_plan_year_beginning_date")
        end = self._field_value_by_rule(fields, "form_5500_part_i_7_plan_year_ending_date")
        if review.plan_year_resolution == FTWilliamsPlanYearResolution.USE_WORKSHEET:
            if (
                self._same_date(begin, review.plan_year_resolution_begin)
                and self._same_date(end, review.plan_year_resolution_end)
            ):
                return review.plan_year_resolution
            return None
        current_begin = form_5500_current.get("PlanYearBeginDate") or schedule_a_current.get("PlanYearBeginDate")
        current_end = form_5500_current.get("PlanYearEndDate") or schedule_a_current.get("PlanYearEndDate")
        if (
            self._same_date(begin, current_begin)
            and self._same_date(end, current_end)
            and self._same_date(review.plan_year_resolution_begin, current_begin)
            and self._same_date(review.plan_year_resolution_end, current_end)
        ):
            return review.plan_year_resolution
        return None

    def _form_5500_update_block_reason(self, fields: list[ExtractedField], form_5500_current: dict[str, str]) -> str | None:
        if not form_5500_current:
            return None
        worksheet_begin = self._field_value_by_rule(fields, "form_5500_part_i_6_plan_year_beginning_date")
        worksheet_end = self._field_value_by_rule(fields, "form_5500_part_i_7_plan_year_ending_date")
        current_begin = form_5500_current.get("PlanYearBeginDate")
        current_end = form_5500_current.get("PlanYearEndDate")
        if worksheet_begin and current_begin and not self._same_date(worksheet_begin, current_begin):
            return "Form 5500 updates blocked: FTW Form 5500 plan year does not match the Plan Worksheet year."
        if worksheet_end and current_end and not self._same_date(worksheet_end, current_end):
            return "Form 5500 updates blocked: FTW Form 5500 plan year does not match the Plan Worksheet year."
        return None

    def _unsafe_schedule_a_field_reason(
        self,
        field: ExtractedField,
        fields: list[ExtractedField],
        current_values: dict[str, str],
    ) -> str | None:
        proposed = str(field.proposed_value or "").strip()
        if not proposed:
            return None
        rule_key = str(field.mapped_rule_key or "")
        current = resolve_ftw_current_value(field, current_values)

        if rule_key == "schedule_a_part_iv_4c_sponsor_ein":
            carrier_ein = current_values.get("InsCarrierEIN") or self._field_value_by_rule(fields, "schedule_a_part_i_1b_insurance_carrier_ein")
            if self._normalize_ein_digits(proposed) and self._normalize_ein_digits(proposed) == self._normalize_ein_digits(carrier_ein):
                return "sponsor EIN equals carrier EIN"
            if current and self._normalize_ein_digits(current) != self._normalize_ein_digits(proposed):
                return "sponsor EIN differs from current FTW value"

        if rule_key == "schedule_a_part_i_3a_name_of_agent_broker_person":
            sponsor_name = current_values.get("PlanSponsorName") or self._field_value_by_rule(fields, "form_5500_part_i_1d_plan_sponsor_name")
            plan_name = current_values.get("PlanName") or self._field_value_by_rule(fields, "form_5500_part_i_1a_plan_name")
            if self._text_overlaps(proposed, sponsor_name) or self._text_overlaps(proposed, plan_name):
                return "broker name looks like plan sponsor or plan name"
            if self._broker_name_contains_address(proposed):
                return "broker name contains address text"

        if rule_key == "schedule_a_part_i_3d_purpose":
            if re.search(r"\d", proposed):
                return "broker purpose contains line/table noise"

        if rule_key == "schedule_a_part_i_3e_organizational_code":
            if current and normalize_compare_value(current) != normalize_compare_value(proposed):
                return "organization code differs from current FTW value"

        if rule_key == "schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier":
            current_number = self._money_number(current)
            proposed_number = self._money_number(proposed)
            if current_number is not None and proposed_number is not None:
                larger = max(abs(current_number), abs(proposed_number), 1.0)
                if abs(current_number - proposed_number) / larger > 0.2:
                    return "premium differs by more than 20 percent from current FTW value"

        return None

    def _field_value_by_rule(self, fields: list[ExtractedField], rule_key: str) -> str | None:
        field = next((item for item in fields if item.mapped_rule_key == rule_key), None)
        return self._value_for_field(field)

    @staticmethod
    def _schedule_a_plan_year_fields(fields: list[ExtractedField]) -> list[ExtractedField]:
        plan_year_rules = {
            "schedule_a_part_iv_4d_plan_year_beginning_date",
            "schedule_a_part_iv_4e_plan_year_ending_date",
        }
        return [field for field in fields if field.mapped_rule_key in plan_year_rules]

    def _same_date(self, left: str | None, right: str | None) -> bool:
        normalized_left = self._normalize_date_for_compare(left)
        normalized_right = self._normalize_date_for_compare(right)
        if normalized_left and normalized_right:
            return normalized_left == normalized_right
        return normalize_compare_value(left) == normalize_compare_value(right)

    def _normalize_date_for_compare(self, value: str | None) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        iso = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
        if iso:
            year, month, day = iso.groups()
            return f"{int(year):04d}{int(month):02d}{int(day):02d}"
        us = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", text)
        if us:
            month, day, year = us.groups()
            return f"{int(year):04d}{int(month):02d}{int(day):02d}"
        digits = re.sub(r"\D", "", text)
        return digits if len(digits) == 8 else ""

    def _money_number(self, value: str | None) -> float | None:
        text = str(value or "").strip().replace("$", "").replace(",", "")
        if not text:
            return None
        if text.startswith("(") and text.endswith(")"):
            text = f"-{text[1:-1]}"
        try:
            return float(text)
        except ValueError:
            return None

    def _text_overlaps(self, value: str | None, reference: str | None) -> bool:
        normalized_value = normalize_compare_value(value)
        normalized_reference = normalize_compare_value(reference)
        if not normalized_value or not normalized_reference:
            return False
        return normalized_value in normalized_reference or normalized_reference in normalized_value

    def _broker_name_contains_address(self, value: str | None) -> bool:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(text) > 80:
            return True
        upper = text.upper()
        address_tokens = [
            " ATTN:",
            " PO BOX ",
            " P.O. BOX ",
            " STREET",
            " ST ",
            " AVE",
            " BLVD",
            " ROAD",
            " RD ",
            " SUITE",
            " STE ",
            " PKWY",
        ]
        if any(token in f" {upper} " for token in address_tokens):
            return True
        return bool(re.search(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b", upper))

    def _should_build_update_payload(self, current_query_sent: bool, current_values: dict[str, str]) -> bool:
        if not current_query_sent:
            return True
        return bool(current_values)

    @staticmethod
    def _comparison_has_updates(
        fields: list[FTWilliamsComparisonField],
        form_type: FormType,
    ) -> bool:
        return any(
            field.form_type == form_type
            and field.changed
            and field.update_included
            for field in fields
        )

    def _prune_noop_update_payloads(self, review: FTWilliamsReview) -> None:
        if not self._comparison_has_updates(review.fields, FormType.FORM_5500):
            review.update_xml_5500 = ""
        if not (
            self._comparison_has_updates(review.fields, FormType.SCHEDULE_A)
            or self._review_has_broker_updates(review)
        ):
            review.update_xml_schedule_a = ""

    def _review_has_broker_updates(self, review: FTWilliamsReview) -> bool:
        extracted_rows = [
            row if isinstance(row, ScheduleABrokerRow) else ScheduleABrokerRow.model_validate(row)
            for row in review.schedule_a_broker_rows or []
        ]
        for match in review.schedule_a_broker_matches or []:
            if not match.resolved:
                continue
            if match.status == "CONFIRMED_NEW":
                return True
            if match.extracted_index < 0 or match.extracted_index >= len(extracted_rows):
                continue
            if not match.current_row:
                return True
            extracted = extracted_rows[match.extracted_index]
            for attribute, tag in (
                ("commission_total", "CommPdAmtXX"),
                ("fee_total", "FeesPdAmtXX"),
                ("organization_code", "CodeXX"),
            ):
                proposed = getattr(extracted, attribute)
                current = getattr(match.current_row, attribute)
                if str(proposed or "").strip() and values_meaningfully_different(current, proposed, tag=tag):
                    return True
        return False

    def _trusted_schedule_a_preserved_values(
        self, review: FTWilliamsReview
    ) -> set[tuple[str, str]]:
        """Return exact FT-originated values that a replace payload may echo.

        These pairs are preservation evidence, not writable mappings. A changed
        value remains subject to the static FT contract, while repeatable broker
        rows are converted to the documented ``*XX`` multipart field names used
        in outgoing Schedule A XML.
        """
        trusted: set[tuple[str, str]] = set()
        for record in review.schedule_a_records or []:
            current_values = record.get("query_results") or record.get("values") or {}
            for tag, value in current_values.items():
                text = str(value or "").strip()
                if text:
                    trusted.add((str(tag), text))
            for broker in schedule_a_broker_multipart_rows(current_values):
                for tag, value in broker.items():
                    text = str(value or "").strip()
                    if text:
                        trusted.add((str(tag), text))
        return trusted

    def _ftw_editability_status(self, current_values: dict[str, str]) -> dict[str, object]:
        values_by_key = {
            str(key or "").strip().casefold(): str(value or "").strip()
            for key, value in (current_values or {}).items()
        }
        locked_status = values_by_key.get("lockedstatus") or None
        signed_status = values_by_key.get("signedstatus") or None
        filing_status = values_by_key.get("filingstatus") or None
        normalized_lock = str(locked_status or "").strip().casefold()
        editable: bool | None = None
        if normalized_lock in {"locked", "1", "yes", "y", "true"}:
            editable = False
        elif normalized_lock in {"unlocked", "0", "no", "n", "false"}:
            editable = True
        return {
            "editable": editable,
            "locked_status": locked_status,
            "signed_status": signed_status,
            "filing_status": filing_status,
        }

    def _match_schedule_a_status(
        self,
        fields: list[ExtractedField],
        statuses: list[FTWilliamsStatusItem],
        preferred_ftw_seq_no: str | None = None,
    ) -> FTWilliamsStatusItem | None:
        candidates = [status for status in statuses if status.query_results]
        if not candidates:
            return statuses[0] if statuses else None
        scored = sorted(
            ((self._schedule_match_details(fields, status), status) for status in candidates),
            key=lambda item: (item[0]["score"], item[0]["strong_matches"], item[0]["filled"]),
            reverse=True,
        )
        top_match, top_status = scored[0]
        if preferred_ftw_seq_no:
            preferred_item = next(
                (
                    (details, status)
                    for details, status in scored
                    if str(status.ftw_seq_no or "") == str(preferred_ftw_seq_no)
                ),
                None,
            )
            if preferred_item:
                preferred_match, preferred_status = preferred_item
                preferred_is_current_best = (
                    preferred_match["score"] >= top_match["score"]
                    and preferred_match["strong_matches"] >= top_match["strong_matches"]
                    and not self._schedule_identity_conflicts(fields, preferred_status)
                )
                if preferred_is_current_best:
                    return preferred_status
        safe_identity_match = top_match["strong_matches"] > 0
        if (
            top_match["score"] <= 0
            or not safe_identity_match
            or self._schedule_identity_conflicts(fields, top_status)
        ):
            return None
        if (
            len(scored) > 1
            and scored[1][0]["score"] == top_match["score"]
            and scored[1][0]["strong_matches"] == top_match["strong_matches"]
        ):
            return None
        if len(scored) > 1:
            runner_up = scored[1][0]
            exact_contract_breaks_normalization_tie = bool(
                "Exact contract" in top_match["reasons"]
                and "Contract" in runner_up["reasons"]
                and top_match["strong_matches"] >= runner_up["strong_matches"]
            )
            if (
                top_match["score"] - runner_up["score"]
                < self._SCHEDULE_A_AUTO_MATCH_MIN_SCORE_MARGIN
                and not exact_contract_breaks_normalization_tie
            ):
                return None
        return top_status

    def _schedule_identity_conflicts(
        self,
        fields: list[ExtractedField],
        status: FTWilliamsStatusItem,
    ) -> bool:
        extracted_by_tag = {
            resolve_ftw_tag(field): field.proposed_value or field.value
            for field in fields
            if field.form_type == FormType.SCHEDULE_A
        }
        query_results = status.query_results or {}
        identity_pairs = [
            (
                self._normalize_contract(extracted_by_tag.get("InsContractNum")),
                self._normalize_contract(query_results.get("InsContractNum") or query_results.get("INS_CONTRACT_NUM")),
            ),
            (
                self._normalize_ein_digits(extracted_by_tag.get("InsCarrierEIN")),
                self._normalize_ein_digits(query_results.get("InsCarrierEIN") or query_results.get("INS_CARRIER_EIN")),
            ),
            (
                self._normalize_identifier_digits(extracted_by_tag.get("InsCarrierNAICCode")),
                self._normalize_identifier_digits(
                    query_results.get("InsCarrierNAICCode")
                    or query_results.get("INS_CARRIER_NAIC_CODE")
                    or query_results.get("NAICCode")
                    or query_results.get("NAIC_CODE")
                ),
            ),
        ]
        return any(extracted and current and extracted != current for extracted, current in identity_pairs)

    def _merge_schedule_statuses(
        self,
        primary: list[FTWilliamsStatusItem],
        fallback: list[FTWilliamsStatusItem],
    ) -> list[FTWilliamsStatusItem]:
        merged: dict[str, FTWilliamsStatusItem] = {}
        anonymous: list[FTWilliamsStatusItem] = []
        safe_fallback = [
            status
            for status in fallback
            if status.query_result_record_count == 1
        ]
        for status in [*primary, *safe_fallback]:
            seq = str(status.ftw_seq_no or "").strip()
            if not seq:
                anonymous.append(status)
                continue
            existing = merged.get(seq)
            if not existing or (
                not self._schedule_status_identity_conflicts(existing, status)
                and self._schedule_status_richness(status) >= self._schedule_status_richness(existing)
            ):
                merged[seq] = status
        return [
            *sorted(merged.values(), key=lambda item: self._sequence_sort_key(item.ftw_seq_no)),
            *anonymous,
        ]

    def _schedule_status_identity_conflicts(
        self,
        existing: FTWilliamsStatusItem,
        incoming: FTWilliamsStatusItem,
    ) -> bool:
        existing_values = existing.query_results or {}
        incoming_values = incoming.query_results or {}
        pairs = [
            (
                self._normalize_contract(existing_values.get("InsContractNum") or existing_values.get("INS_CONTRACT_NUM")),
                self._normalize_contract(incoming_values.get("InsContractNum") or incoming_values.get("INS_CONTRACT_NUM")),
            ),
            (
                self._normalize_ein_digits(existing_values.get("InsCarrierEIN") or existing_values.get("INS_CARRIER_EIN")),
                self._normalize_ein_digits(incoming_values.get("InsCarrierEIN") or incoming_values.get("INS_CARRIER_EIN")),
            ),
            (
                self._normalize_identifier_digits(
                    existing_values.get("InsCarrierNAICCode") or existing_values.get("INS_CARRIER_NAIC_CODE")
                ),
                self._normalize_identifier_digits(
                    incoming_values.get("InsCarrierNAICCode") or incoming_values.get("INS_CARRIER_NAIC_CODE")
                ),
            ),
        ]
        if any(left and right and left != right for left, right in pairs):
            return True

        existing_description = normalize_compare_value(
            existing_values.get("ScheduleDesc") or existing_values.get("SCHEDULE_DESC")
        )
        incoming_description = normalize_compare_value(
            incoming_values.get("ScheduleDesc") or incoming_values.get("SCHEDULE_DESC")
        )
        return bool(existing_description and incoming_description and existing_description != incoming_description)

    def _schedule_status_richness(self, status: FTWilliamsStatusItem) -> int:
        values = status.query_results or {}
        important = [
            "InsCarrierName",
            "INS_CARRIER_NAME",
            "InsCarrierEIN",
            "INS_CARRIER_EIN",
            "InsCarrierNAICCode",
            "INS_CARRIER_NAIC_CODE",
            "InsContractNum",
            "INS_CONTRACT_NUM",
            "ScheduleDesc",
            "PlanYearBeginDate",
            "PlanYearEndDate",
            "InsPolicyFromDate",
            "InsPolicyToDate",
        ]
        important_count = len([key for key in important if str(values.get(key) or "").strip()])
        filled_count = len([value for value in values.values() if str(value or "").strip()])
        return important_count * 10 + filled_count

    def _schedule_match_score(self, fields: list[ExtractedField], status: FTWilliamsStatusItem) -> tuple[int, int]:
        details = self._schedule_match_details(fields, status)
        return int(details["score"]), int(details["filled"])

    def _schedule_match_details(self, fields: list[ExtractedField], status: FTWilliamsStatusItem) -> dict:
        extracted_by_tag = {resolve_ftw_tag(field): field.proposed_value or field.value for field in fields if field.form_type == FormType.SCHEDULE_A}
        score = 0
        strong_matches = 0
        reasons: list[str] = []
        query_results = status.query_results or {}
        extracted_contract_value = extracted_by_tag.get("InsContractNum")
        current_contract_value = query_results.get("InsContractNum") or query_results.get("INS_CONTRACT_NUM")
        extracted_contract_exact = self._contract_text(extracted_contract_value)
        current_contract_exact = self._contract_text(current_contract_value)
        extracted_contract = self._normalize_contract(extracted_contract_value)
        current_contract = self._normalize_contract(current_contract_value)
        if (
            extracted_contract_exact
            and current_contract_exact
            and extracted_contract_exact == current_contract_exact
        ):
            score += 12
            strong_matches += 1
            reasons.append("Exact contract")
        elif extracted_contract and current_contract and extracted_contract == current_contract:
            score += 8
            strong_matches += 1
            reasons.append("Contract")

        extracted_carrier_ein = self._normalize_ein_digits(extracted_by_tag.get("InsCarrierEIN"))
        current_carrier_ein = self._normalize_ein_digits(query_results.get("InsCarrierEIN") or query_results.get("INS_CARRIER_EIN"))
        if extracted_carrier_ein and current_carrier_ein and extracted_carrier_ein == current_carrier_ein:
            score += 7
            strong_matches += 1
            reasons.append("Carrier EIN")

        extracted_naic = self._normalize_identifier_digits(extracted_by_tag.get("InsCarrierNAICCode"))
        current_naic = self._normalize_identifier_digits(
            query_results.get("InsCarrierNAICCode")
            or query_results.get("INS_CARRIER_NAIC_CODE")
            or query_results.get("InsCarrierNAIC")
            or query_results.get("INS_CARRIER_NAIC")
            or query_results.get("NAICCode")
            or query_results.get("NAIC_CODE")
        )
        if extracted_naic and current_naic and extracted_naic == current_naic:
            score += 6
            strong_matches += 1
            reasons.append("NAIC")

        extracted_carrier = normalize_compare_value(extracted_by_tag.get("InsCarrierName"))
        current_carrier = normalize_compare_value(query_results.get("InsCarrierName") or query_results.get("INS_CARRIER_NAME"))
        if extracted_carrier and current_carrier:
            if extracted_carrier == current_carrier:
                score += 4
                reasons.append("Carrier name")
            elif extracted_carrier in current_carrier or current_carrier in extracted_carrier:
                score += 2
                reasons.append("Carrier name partial")

        date_pairs = [
            ("InsPolicyFromDate", query_results.get("InsPolicyFromDate") or query_results.get("INS_POLICY_FROM_DATE")),
            ("InsPolicyToDate", query_results.get("InsPolicyToDate") or query_results.get("INS_POLICY_TO_DATE")),
            ("PlanYearBeginDate", query_results.get("PlanYearBeginDate")),
            ("PlanYearEndDate", query_results.get("PlanYearEndDate")),
        ]
        for extracted_tag, current_value in date_pairs:
            extracted_value = extracted_by_tag.get(extracted_tag)
            if extracted_value and current_value and self._same_date(str(extracted_value), str(current_value)):
                score += 1
                reasons.append("Policy date")
        filled = len([value for value in status.query_results.values() if str(value or "").strip()])
        return {
            "score": score,
            "strong_matches": strong_matches,
            "filled": filled,
            "reasons": reasons,
        }

    def _normalize_contract(self, value: object) -> str:
        text = self._contract_text(value)
        if not text:
            return ""
        # FT Williams normalizes policy identifiers by dropping leading zeroes
        # from numeric runs even when they follow an alphabetic prefix (for
        # example, ``LK 0751856`` is returned as ``LK 751856``). Treat those
        # representations as the same contract while preserving all letters.
        return re.sub(r"\d+", lambda match: match.group(0).lstrip("0") or "0", text)

    @staticmethod
    def _contract_text(value: object) -> str:
        return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()

    def _normalize_identifier_digits(self, value: object) -> str:
        text = re.sub(r"\D", "", str(value or ""))
        return text.lstrip("0") or text

    def _schedule_match_payload(self, status: FTWilliamsStatusItem, fields: list[ExtractedField]) -> dict:
        match_details = self._schedule_match_details(fields, status)
        return {
            "ftw_seq_no": status.ftw_seq_no,
            "score": match_details["score"],
            "strong_matches": match_details["strong_matches"],
            "match_reasons": match_details["reasons"],
            "carrier": status.query_results.get("InsCarrierName") or status.query_results.get("INS_CARRIER_NAME"),
            "carrier_ein": status.query_results.get("InsCarrierEIN") or status.query_results.get("INS_CARRIER_EIN"),
            "naic": status.query_results.get("InsCarrierNAICCode") or status.query_results.get("INS_CARRIER_NAIC_CODE"),
            "contract": status.query_results.get("InsContractNum") or status.query_results.get("INS_CONTRACT_NUM"),
            "description": status.query_results.get("ScheduleDesc") or status.query_results.get("SCHEDULE_DESC"),
            "has_current_data": bool(status.query_results),
        }

    def _schedule_match_payload_preserving_decision(
        self,
        status: FTWilliamsStatusItem,
        fields: list[ExtractedField],
        existing_review: FTWilliamsReview | None,
    ) -> dict:
        payload = self._schedule_match_payload(status, fields)
        existing_match = existing_review.schedule_a_match if existing_review else None
        if (
            existing_match
            and str(existing_match.get("source") or "").upper() == "MANUAL"
            and str(existing_match.get("ftw_seq_no") or "").strip() == str(status.ftw_seq_no or "").strip()
        ):
            payload["source"] = "MANUAL"
        return payload

    def _schedule_candidate_payloads(self, statuses: list[FTWilliamsStatusItem], fields: list[ExtractedField]) -> list[dict]:
        candidates: list[dict] = []
        seen: set[str] = set()
        for status in statuses:
            seq = str(status.ftw_seq_no or "").strip()
            if not seq or seq in seen:
                continue
            seen.add(seq)
            match_details = self._schedule_match_details(fields, status)
            candidates.append(
                {
                    "ftw_seq_no": seq,
                    "score": match_details["score"],
                    "strong_matches": match_details["strong_matches"],
                    "match_reasons": match_details["reasons"],
                    "carrier": status.query_results.get("InsCarrierName") or status.query_results.get("INS_CARRIER_NAME"),
                    "carrier_ein": status.query_results.get("InsCarrierEIN") or status.query_results.get("INS_CARRIER_EIN"),
                    "naic": status.query_results.get("InsCarrierNAICCode") or status.query_results.get("INS_CARRIER_NAIC_CODE"),
                    "contract": status.query_results.get("InsContractNum") or status.query_results.get("INS_CONTRACT_NUM"),
                    "description": status.query_results.get("ScheduleDesc") or status.query_results.get("SCHEDULE_DESC"),
                    "plan_year_begin": status.query_results.get("PlanYearBeginDate"),
                    "plan_year_end": status.query_results.get("PlanYearEndDate"),
                    "has_current_data": bool(status.query_results),
                    "status": "Current data loaded" if status.query_results else "Sequence found, details unavailable",
                }
            )
        return sorted(candidates, key=lambda item: (-int(item.get("score") or 0), self._sequence_sort_key(item.get("ftw_seq_no"))))

    def _schedule_record_payloads(self, statuses: list[FTWilliamsStatusItem]) -> list[dict]:
        records: list[dict] = []
        seen: set[str] = set()
        for status in statuses:
            seq = str(status.ftw_seq_no or "").strip()
            if not seq or seq in seen or not status.query_results:
                continue
            seen.add(seq)
            records.append(
                {
                    "ftw_seq_no": seq,
                    "carrier": status.query_results.get("InsCarrierName") or status.query_results.get("INS_CARRIER_NAME"),
                    "carrier_ein": status.query_results.get("InsCarrierEIN") or status.query_results.get("INS_CARRIER_EIN"),
                    "contract": status.query_results.get("InsContractNum") or status.query_results.get("INS_CONTRACT_NUM"),
                    "query_results": dict(status.query_results),
                    "query_subparts": {
                        name: [dict(row) for row in rows]
                        for name, rows in (status.query_subparts or {}).items()
                    },
                }
            )
        return sorted(records, key=lambda item: self._sequence_sort_key(item.get("ftw_seq_no")))

    def _merge_schedule_candidate_payloads(
        self,
        candidates: list[dict],
        selected_status: FTWilliamsStatusItem,
        fields: list[ExtractedField],
    ) -> list[dict]:
        selected_candidate = self._schedule_match_payload(selected_status, fields)
        selected_seq = str(selected_candidate.get("ftw_seq_no") or "").strip()
        if not candidates:
            return [selected_candidate] if selected_seq else []

        merged: list[dict] = []
        seen_selected = False
        for candidate in candidates:
            seq = str(candidate.get("ftw_seq_no") or "").strip()
            if selected_seq and seq == selected_seq:
                merged.append({**candidate, **selected_candidate})
                seen_selected = True
            else:
                merged.append(candidate)
        if selected_seq and not seen_selected:
            merged.append(selected_candidate)
        return sorted(merged, key=lambda item: (-int(item.get("score") or 0), self._sequence_sort_key(item.get("ftw_seq_no"))))

    def _merge_schedule_record_payloads(self, records: list[dict], selected_status: FTWilliamsStatusItem) -> list[dict]:
        selected_records = self._schedule_record_payloads([selected_status])
        if not selected_records:
            return records
        selected_record = selected_records[0]
        selected_seq = str(selected_record.get("ftw_seq_no") or "").strip()
        merged: list[dict] = []
        replaced = False
        for record in records:
            if selected_seq and str(record.get("ftw_seq_no") or "").strip() == selected_seq:
                merged.append(selected_record)
                replaced = True
            else:
                merged.append(record)
        if not replaced:
            merged.append(selected_record)
        return sorted(merged, key=lambda item: self._sequence_sort_key(item.get("ftw_seq_no")))

    def _build_schedule_a_update_xml(
        self,
        safe_schedule_a_fields: list[ExtractedField],
        schedule_a_records: list[dict],
        matched_ftw_seq_no: str | None,
        identity: dict,
        *,
        all_record_fields: list[ExtractedField] | None = None,
        schedule_update_blocked: bool = False,
        add_new_schedule_a: bool = False,
        new_schedule_desc: str | None = None,
        schedule_a_broker_rows: list | None = None,
    ) -> str:
        if schedule_update_blocked:
            return ""
        if add_new_schedule_a:
            if not schedule_a_records:
                return ""
            return build_schedule_a_records_update_xml(
                schedule_a_records,
                None,
                [],
                all_record_fields=all_record_fields,
                add_new_fields=safe_schedule_a_fields,
                new_schedule_desc=new_schedule_desc,
                transaction_type="2",
                schedule_a_broker_rows=schedule_a_broker_rows,
                **{key: value for key, value in identity.items() if key != "ftw_seq_no"},
            )
        matched_seq = str(matched_ftw_seq_no or "").strip()
        matched_record = next(
            (record for record in schedule_a_records if matched_seq and str(record.get("ftw_seq_no") or "").strip() == matched_seq),
            None,
        )
        matched_current = (matched_record or {}).get("query_results") or {}
        if not schedule_a_records or not matched_current:
            return ""
        return build_schedule_a_records_update_xml(
            schedule_a_records,
            matched_ftw_seq_no,
            safe_schedule_a_fields,
            all_record_fields=all_record_fields,
            transaction_type="2",
            schedule_a_broker_rows=schedule_a_broker_rows,
            **{key: value for key, value in identity.items() if key != "ftw_seq_no"},
        )

    def _missing_schedule_a_records_for_safe_send(self, review: FTWilliamsReview) -> str | None:
        if review.schedule_a_broker_rows and not review.schedule_a_broker_match_complete:
            return "Every extracted Schedule A broker must be matched to an FT Williams row or confirmed as new before sending."
        has_schedule_xml = bool(review.update_xml_schedule_a and "DOLScheduleAData" in review.update_xml_schedule_a)
        has_schedule_updates = any(
            field.form_type == FormType.SCHEDULE_A and field.update_included
            for field in review.fields or []
        )
        if not has_schedule_xml:
            if has_schedule_updates:
                return "Cannot safely send Schedule A because the replace-style Schedule A XML was not built. Query current FT Williams Schedule A records first."
            return None
        if not review.schedule_a_match:
            return "A current FT Williams Schedule A must be matched before sending a replace-style Schedule A update."
        is_new_schedule = bool(review.schedule_a_match.get("create_new"))
        if not is_new_schedule and not str(review.schedule_a_match.get("ftw_seq_no") or "").strip():
            return "A current FT Williams Schedule A sequence must be selected before sending a Schedule A update."
        candidate_seqs = {
            str(candidate.get("ftw_seq_no") or "").strip()
            for candidate in review.schedule_a_candidates or []
            if str(candidate.get("ftw_seq_no") or "").strip()
        }
        record_seqs = {
            str(record.get("ftw_seq_no") or "").strip()
            for record in review.schedule_a_records or []
            if str(record.get("ftw_seq_no") or "").strip() and record.get("query_results")
        }
        missing = sorted(candidate_seqs - record_seqs, key=self._sequence_sort_key)
        if missing:
            action = "add" if is_new_schedule else "send"
            return f"Cannot safely {action} Schedule A because existing FT Williams Schedule A records were not fully fetched: {', '.join(missing)}."
        xml_schedule_count = str(review.update_xml_schedule_a or "").count("<DOLScheduleAData>")
        expected_schedule_count = len(record_seqs) + (1 if is_new_schedule else 0)
        if xml_schedule_count != expected_schedule_count:
            return (
                f"Cannot safely send Schedule A because XML contains {xml_schedule_count} Schedule A record(s) "
                f"but exactly {expected_schedule_count} record(s) are expected from the fresh snapshot."
            )
        replacement_gaps = schedule_a_replacement_data_gaps(
            list(review.schedule_a_records or []),
            review.update_xml_schedule_a,
            matched_ftw_seq_no=None if is_new_schedule else review.schedule_a_match.get("ftw_seq_no"),
        )
        if replacement_gaps:
            details = "; ".join(replacement_gaps[:5])
            if len(replacement_gaps) > 5:
                details += f"; and {len(replacement_gaps) - 5} more"
            return f"Cannot safely send Schedule A because replacement data is incomplete: {details}."
        if is_new_schedule:
            return None
        selected_seq = str(review.schedule_a_match.get("ftw_seq_no") or "").strip()
        if selected_seq not in record_seqs:
            return f"Cannot safely send Schedule A because selected FT Williams Schedule A sequence {selected_seq} was not fetched."
        selected_record = next(
            (
                record
                for record in review.schedule_a_records or []
                if str(record.get("ftw_seq_no") or "").strip() == selected_seq
            ),
            None,
        )
        identity_error = self._schedule_a_match_identity_error(
            review.schedule_a_match,
            selected_record or {},
        )
        if identity_error:
            return identity_error
        return None

    def _schedule_a_match_identity_error(self, selected: dict, record: dict) -> str | None:
        current = record.get("query_results") or {}
        comparisons = [
            (
                "carrier name",
                normalize_compare_value(selected.get("carrier")),
                normalize_compare_value(record.get("carrier") or current.get("InsCarrierName") or current.get("INS_CARRIER_NAME")),
            ),
            (
                "carrier EIN",
                self._normalize_ein_digits(selected.get("carrier_ein")),
                self._normalize_ein_digits(record.get("carrier_ein") or current.get("InsCarrierEIN") or current.get("INS_CARRIER_EIN")),
            ),
            (
                "policy number",
                self._contract_text(selected.get("contract")),
                self._contract_text(record.get("contract") or current.get("InsContractNum") or current.get("INS_CONTRACT_NUM")),
            ),
        ]
        conflicts = [label for label, expected, actual in comparisons if expected and actual and expected != actual]
        if not conflicts:
            return None
        return (
            "Cannot safely send Schedule A because the selected record identity changed after refresh "
            f"({', '.join(conflicts)}). Re-select the Schedule A and review the latest FT Williams values."
        )

    def _sequence_sort_key(self, value: object) -> tuple[int, str]:
        text = str(value or "").strip()
        if text.isdigit():
            return int(text), text
        return 10_000, text

    def _schedule_desc_from_payload_or_fields(
        self,
        payload: FTWilliamsScheduleAMatchRequest,
        fields: list[ExtractedField],
        records: list[dict],
    ) -> str:
        existing = {
            str((record.get("query_results") or {}).get("ScheduleDesc") or "").strip().upper()
            for record in records
            if isinstance(record.get("query_results"), dict)
        }
        source = (
            payload.schedule_desc
            or payload.carrier
            or self._field_value_by_rule(fields, "schedule_a_part_i_1a_name_of_insurance_company")
            or "SCHEDULE"
        )
        base = re.sub(r"[^A-Z0-9]", "", str(source).upper())[:8] or "SCHEDULE"
        if base not in existing:
            return base
        stem = base[:7] or "SCHEDUL"
        for index in range(1, 10):
            candidate = f"{stem}{index}"[:8]
            if candidate not in existing:
                return candidate
        return base[:6] + "99"

    def _extract_plan_lookup_identifiers(self, fields: list[ExtractedField], filing=None) -> dict:
        sponsor_ein = self._normalize_ein(
            self._first_field_value(
                fields,
                rule_keys=[
                    "form_5500_part_i_1e_plan_sponsor_ein",
                    "schedule_a_part_iv_4c_sponsor_ein",
                ],
                ftw_tags=["SPONS_DFE_EIN", "SCH_A_EIN"],
                label_needles=["sponsor ein", "employer identification"],
            )
        )
        plan_number = self._normalize_plan_number(
            self._first_field_value(
                fields,
                rule_keys=[
                    "form_5500_part_i_1b_plan_number_pn",
                    "schedule_a_part_iv_4b_plan_number_pn",
                ],
                ftw_tags=["SPONS_DFE_PN", "SCH_A_PLAN_NUM"],
                label_needles=["plan number", "pn"],
            )
        )
        package_filing_year = self._filing_year_from_filing(filing)
        year = package_filing_year or self._normalize_year(
            self._first_field_value(
                fields,
                rule_keys=[
                    "form_5500_part_i_7_plan_year_ending_date",
                    "schedule_a_part_iv_4e_plan_year_ending_date",
                ],
                ftw_tags=["FORM_TAX_PRD", "SCH_A_TAX_PRD"],
                label_needles=["plan year ending", "tax period"],
            )
        )
        plan_name = self._first_field_value(
            fields,
            rule_keys=["form_5500_part_i_1a_plan_name", "schedule_a_part_iv_4a_plan_name"],
            ftw_tags=["PLAN_NAME0", "SCH_A_PLAN_NAME"],
            label_needles=["plan name"],
        )
        sponsor_name = self._first_field_value(
            fields,
            rule_keys=["form_5500_part_i_1d_plan_sponsor_name"],
            ftw_tags=["SPONSOR_DFE_NAME0", "SCH_A_SPONSOR_NAME"],
            label_needles=["plan sponsor name", "sponsor name"],
        )
        sponsor_address = self._first_field_value(
            fields,
            rule_keys=["form_5500_part_i_1f_plan_sponsor_address"],
            ftw_tags=["SPONS_DFE_MAIL_STR_ADDRESS"],
            label_needles=["plan sponsor address", "sponsor address", "mailing address"],
        )
        company_state = self._state_from_address(sponsor_address)
        company_name_candidates = self._filing_company_name_candidates(filing)
        for value in [sponsor_name, plan_name]:
            cleaned = self._clean_company_name_candidate(value)
            if cleaned and cleaned not in company_name_candidates:
                company_name_candidates.append(cleaned)
        return {
            "company_employer_id": sponsor_ein,
            "plan_number": plan_number,
            "year": year or get_settings().ftwlink_sandbox_year,
            "plan_name": plan_name,
            "sponsor_name": sponsor_name,
            "company_state": company_state,
            "company_name_candidates": company_name_candidates,
        }

    def _filing_company_name_candidates(self, filing) -> list[str]:
        if not filing:
            return []
        candidates: list[str] = []
        package_documents = getattr(filing, "package_documents", None) or []
        for document in package_documents:
            for key in ["client_name", "client", "company_name", "folder_name", "sharefile_folder_name"]:
                cleaned = self._clean_company_name_candidate(document.get(key))
                if cleaned and cleaned not in candidates:
                    candidates.append(cleaned)
            for key in ["sharefile_path", "folder_path"]:
                for part in re.split(r"[\\/>\|]+", str(document.get(key) or "")):
                    cleaned = self._clean_company_name_candidate(part)
                    if self._looks_like_company_name(cleaned) and cleaned not in candidates:
                        candidates.append(cleaned)
        return candidates

    def _clean_company_name_candidate(self, value: object) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        if re.search(r"\.(pdf|docx?|xlsx?|csv|txt)$", text, flags=re.IGNORECASE):
            return None
        text = re.sub(r"\s*\((?:test|prod|production|sandbox|dev|development)\)\s*$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip(" -_/")
        return text or None

    def _state_from_address(self, value: object) -> str | None:
        text = str(value or "").upper()
        if not text:
            return None
        states = {
            "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "IA", "ID", "IL", "IN", "KS",
            "KY", "LA", "MA", "MD", "ME", "MI", "MN", "MO", "MS", "MT", "NC", "ND", "NE", "NH", "NJ", "NM",
            "NV", "NY", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VA", "VT", "WA", "WI",
            "WV", "WY", "DC",
        }
        tokens = re.findall(r"\b[A-Z]{2}\b", text)
        for token in reversed(tokens):
            if token in states:
                return token
        return None

    def _looks_like_company_name(self, value: str | None) -> bool:
        if not value:
            return False
        lowered = value.lower()
        if lowered in {"shared folders", "5500 filing", "schedule a", "items", "people"}:
            return False
        if re.fullmatch(r"\d{4}\s+filing", lowered):
            return False
        company_tokens = [" inc", " llc", " corp", " company", " center", " council", " health", " dental", " group"]
        return any(token in lowered for token in company_tokens)

    def _company_name_candidates(self, lookup: FTWilliamsPlanLookup) -> list[str]:
        candidates: list[str] = []
        for value in [lookup.sponsor_name, *lookup.company_name_candidates, lookup.plan_name]:
            cleaned = self._clean_company_name_candidate(value)
            if not cleaned:
                continue
            for candidate in self._company_name_variants(cleaned):
                if candidate not in candidates:
                    candidates.append(candidate)
        return candidates[:8]

    def _company_name_variants(self, value: str) -> list[str]:
        variants = [value]
        no_punctuation = re.sub(r"[,.]", "", value)
        no_punctuation = re.sub(r"\s+", " ", no_punctuation).strip()
        if no_punctuation and no_punctuation not in variants:
            variants.append(no_punctuation)
        without_suffix = re.sub(
            r"\s+(?:incorporated|inc|llc|l\.l\.c|corp|corporation|co|company)\.?$",
            "",
            value,
            flags=re.IGNORECASE,
        ).strip(" ,.")
        if without_suffix and without_suffix not in variants:
            variants.append(without_suffix)
        return variants

    def _filing_year_from_filing(self, filing) -> str | None:
        if not filing:
            return None
        package_documents = getattr(filing, "package_documents", None) or []
        for document in package_documents:
            year = self._normalize_year(str(document.get("filing_year") or ""))
            if year:
                return year
        for document in package_documents:
            path_parts = [
                str(document.get("sharefile_path") or ""),
                str(document.get("file_name") or ""),
            ]
            for part in path_parts:
                year = self._normalize_year(part)
                if year:
                    return year
        return None

    def _first_field_value(
        self,
        fields: list[ExtractedField],
        *,
        rule_keys: list[str],
        ftw_tags: list[str],
        label_needles: list[str],
    ) -> str | None:
        for rule_key in rule_keys:
            value = self._value_for_field(next((field for field in fields if field.mapped_rule_key == rule_key), None))
            if value:
                return value
        for tag in ftw_tags:
            value = self._value_for_field(next((field for field in fields if resolve_ftw_tag(field) == tag), None))
            if value:
                return value
        for field in fields:
            haystack = " ".join(
                [
                    field.mapped_label or "",
                    field.source_field_name or "",
                    field.ftw_field or "",
                    field.normalized_field_name or "",
                ]
            ).lower()
            if any(needle in haystack for needle in label_needles):
                value = self._value_for_field(field)
                if value:
                    return value
        return None

    def _value_for_field(self, field: ExtractedField | None) -> str | None:
        value = str((field.proposed_value if field else "") or (field.value if field else "") or "").strip()
        return value or None

    def _normalize_ein(self, value: str | None) -> str | None:
        digits = re.sub(r"\D", "", value or "")
        if len(digits) == 9:
            return f"{digits[:2]}-{digits[2:]}"
        return str(value or "").strip() or None

    def _normalize_ein_digits(self, value: str | None) -> str:
        return re.sub(r"\D", "", value or "")

    def _normalize_plan_number(self, value: str | None) -> str | None:
        match = re.search(r"\d{1,3}", value or "")
        if not match:
            return None
        return match.group(0).zfill(3)

    def _normalize_year(self, value: str | None) -> str | None:
        match = re.search(r"(19|20)\d{2}", value or "")
        return match.group(0) if match else None

    def _derived_customer_plan_identity(self, lookup: FTWilliamsPlanLookup) -> dict[str, str]:
        customer_id = self._normalize_ein(lookup.company_employer_id)
        plan_number = self._normalize_plan_number(lookup.plan_number)
        if not customer_id or not plan_number:
            return {}
        return {
            "customer_id": customer_id,
            "plan_id": f"{customer_id}{plan_number}",
        }

    def _plan_status_match(
        self,
        status: FTWilliamsStatusItem,
        lookup: FTWilliamsPlanLookup,
        derived_identity: dict[str, str],
    ) -> dict[str, str]:
        query_results = status.query_results or {}
        values = {
            "CustomerID": status.customer_id or derived_identity.get("customer_id") or "",
            "PlanID": status.plan_id or derived_identity.get("plan_id") or "",
            "FTWCustomerID": status.ftw_customer_id or "",
            "FTWPlanID": status.ftw_plan_id or "",
            "CompanyEmployerID": lookup.company_employer_id or "",
            "PlanNumber": query_results.get("PlanNumber") or lookup.plan_number or "",
            "PlanLine1": query_results.get("PlanLine1") or status.plan_name or lookup.plan_name or "",
            "CompanyName": lookup.sponsor_name or "",
        }
        return {key: value for key, value in values.items() if value}

    def _identity_from_mapping(self, mapping: FTWilliamsPlanMapping) -> dict[str, str]:
        identity = {
            "customer_id": mapping.customer_id,
            "plan_id": mapping.plan_id,
            "ftw_customer_id": mapping.ftw_customer_id,
            "ftw_plan_id": mapping.ftw_plan_id,
        }
        return {key: value for key, value in identity.items() if value}

    def _mapping_match(self, mapping: FTWilliamsPlanMapping) -> dict[str, str]:
        values = {
            "CustomerID": mapping.customer_id or "",
            "PlanID": mapping.plan_id or "",
            "FTWCustomerID": mapping.ftw_customer_id or "",
            "FTWPlanID": mapping.ftw_plan_id or "",
            "CompanyEmployerID": mapping.company_employer_id,
            "PlanNumber": mapping.plan_number,
            "PlanLine1": mapping.plan_name or "",
            "CompanyName": mapping.sponsor_name or "",
            "Source": mapping.source,
        }
        return {key: value for key, value in values.items() if value}

    def _manual_identity(self, payload: FTWilliamsManualMatchRequest) -> dict[str, str]:
        values = {
            "customer_id": payload.customer_id,
            "plan_id": payload.plan_id,
            "ftw_customer_id": payload.ftw_customer_id,
            "ftw_plan_id": payload.ftw_plan_id,
        }
        return {key: value.strip() for key, value in values.items() if value and value.strip()}

    def _identity_from_review(self, review: FTWilliamsReview) -> dict[str, str]:
        identity = {
            "customer_id": review.customer_id,
            "plan_id": review.plan_id,
            "ftw_customer_id": review.ftw_customer_id,
            "ftw_plan_id": review.ftw_plan_id,
            "year": review.year,
        }
        if review.plan_lookup and review.plan_lookup.matched_identity:
            identity = {**identity, **review.plan_lookup.matched_identity}
        return {key: str(value) for key, value in identity.items() if value}

    def _current_query_identity_from_review(self, review: FTWilliamsReview) -> dict[str, str]:
        identity = self._identity_from_review(review)
        comparison_year = str(review.comparison_year or "").strip()
        if comparison_year:
            identity["year"] = comparison_year
        return identity

    def _review_current_values(self, review: FTWilliamsReview, form_type: FormType) -> dict[str, str]:
        values = {
            field.ftw_tag: field.current_value
            for field in review.fields
            if field.form_type == form_type and field.ftw_tag and field.current_value
        }
        if form_type != FormType.FORM_5500:
            return values

        # Older stored reviews only contain display summaries, not the complete
        # raw FTW response. Rehydrate the composite Form 5500 values so a local
        # field decision cannot make current values appear blank or create false
        # updates before the next explicit FTW refresh.
        for field in review.fields:
            if field.form_type != FormType.FORM_5500 or not field.current_value:
                continue
            rule_key = str(field.rule_key or "")
            selected = {
                item.strip().casefold()
                for item in str(field.current_value).split(",")
                if item.strip()
            }
            if rule_key == "form_5500_part_i_1f_plan_sponsor_address":
                values["SDAddressLine1"] = field.current_value
            elif rule_key == "form_5500_part_ii_9_plan_funding_arrangement":
                self._restore_indicator_summary(
                    values,
                    selected,
                    [
                        ("FundingInsuranceInd", "Insurance"),
                        ("FundingCdSection412Ind", "Code section 412(e)(3) insurance contracts"),
                        ("FundingTrustInd", "Trust"),
                        ("FundingGeneralAssetInd", "General assets of the sponsor"),
                    ],
                )
            elif rule_key == "form_5500_part_ii_10a_plan_benefit_arrangement":
                self._restore_indicator_summary(
                    values,
                    selected,
                    [
                        ("BenefitInsuranceInd", "Insurance"),
                        ("BenefitCdSection412Ind", "Code section 412(e)(3) insurance contracts"),
                        ("BenefitTrustInd", "Trust"),
                        ("BenefitGeneralAssetInd", "General assets of the sponsor"),
                    ],
                )
            elif rule_key == "form_5500_part_ii_10b_schedules_attached":
                self._restore_indicator_summary(
                    values,
                    selected,
                    [
                        ("SchRAttachedInd", "R"),
                        ("SchMBAttachedInd", "MB"),
                        ("SchSBAttachedInd", "SB"),
                        ("SchDCGAttachedInd", "DCG"),
                        ("SchMEPAttachedInd", "MEP"),
                        ("SchHAttachedInd", "H"),
                        ("SchIAttachedInd", "I"),
                        ("SchAAttachedInd", "A"),
                        ("SchCAttachedInd", "C"),
                        ("SchDAttachedInd", "D"),
                        ("SchGAttachedInd", "G"),
                    ],
                )
        return values

    @staticmethod
    def _restore_indicator_summary(
        values: dict[str, str],
        selected: set[str],
        indicators: list[tuple[str, str]],
    ) -> None:
        for tag, label in indicators:
            values[tag] = "1" if label.casefold() in selected else "0"

    def _preferred_schedule_a_sequence(self, review: FTWilliamsReview | None) -> str | None:
        if not review or not review.schedule_a_match:
            return None
        if str(review.schedule_a_match.get("source") or "").upper() != "MANUAL":
            return None
        value = review.schedule_a_match.get("ftw_seq_no")
        return str(value) if value else None

    def _plan_lookup_matches(self, matches: list[dict[str, str]], lookup: FTWilliamsPlanLookup) -> list[dict[str, str]]:
        if not matches:
            return []
        scored = [(self._plan_lookup_score(match, lookup), match) for match in matches]
        exact_matches = [match for score, match in scored if score >= 8]
        if exact_matches:
            return exact_matches
        partial_matches = [match for score, match in scored if score > 0]
        if partial_matches:
            return partial_matches if len(partial_matches) > 1 else [partial_matches[0]]
        non_conflicting = [match for score, match in scored if score >= 0]
        return non_conflicting if len(non_conflicting) == 1 else []

    def _plan_lookup_score(self, match: dict[str, str], lookup: FTWilliamsPlanLookup) -> int:
        score = 0
        match_year = self._normalize_year(
            match.get("PlanYear")
            or match.get("Year")
            or match.get("PlanYearEndDate")
            or match.get("FORM_TAX_PRD")
            or match.get("SCH_A_TAX_PRD")
        )
        lookup_year = self._normalize_year(lookup.year)
        if match_year and lookup_year:
            if match_year != lookup_year:
                return -100
            score += 2

        match_ein = self._normalize_ein_digits(
            match.get("CompanyEmployerID")
            or match.get("SPONS_DFE_EIN")
            or match.get("SCH_A_EIN")
        )
        lookup_ein = self._normalize_ein_digits(lookup.company_employer_id)
        if match_ein and lookup_ein and match_ein == lookup_ein:
            score += 4

        match_plan_number = self._normalize_plan_number(
            match.get("PlanNumber")
            or match.get("SPONS_DFE_PN")
            or match.get("SCH_A_PLAN_NUM")
        )
        if match_plan_number and lookup.plan_number and match_plan_number == lookup.plan_number:
            score += 4

        match_plan_name = normalize_compare_value(
            match.get("PlanLine1")
            or match.get("PlanName")
            or match.get("PLAN_NAME0")
            or match.get("SCH_A_PLAN_NAME")
        )
        lookup_plan_name = normalize_compare_value(lookup.plan_name)
        if lookup_plan_name and match_plan_name and (
            lookup_plan_name in match_plan_name or match_plan_name in lookup_plan_name
        ):
            score += 1

        match_company_name = normalize_compare_value(
            match.get("CompanyName")
            or match.get("CompanyLine1")
            or match.get("SponsorName")
            or match.get("SPONSOR_DFE_NAME0")
            or match.get("SCH_A_SPONSOR_NAME")
        )
        for candidate in self._company_name_candidates(lookup):
            candidate_name = normalize_compare_value(candidate)
            if candidate_name and match_company_name and (
                candidate_name in match_company_name or match_company_name in candidate_name
            ):
                score += 2
                break
        return score

    def _plan_ids_probe_score(self, match: dict[str, str], lookup: FTWilliamsPlanLookup) -> int:
        """Rank batch identifiers using only metadata available before a plan query."""
        score = self._plan_lookup_score(match, lookup)
        candidate_text = normalize_compare_value(
            " ".join(
                str(match.get(key) or "")
                for key in ["CustomerID", "PlanID", "CompanyName", "CompanyLine1", "PlanName", "PlanLine1"]
            )
        )
        if not candidate_text:
            return score

        generic_words = {
            "AND", "BENEFIT", "BENEFITS", "COMPANY", "CORP", "CORPORATION", "HEALTH",
            "INC", "INSURANCE", "LLC", "LTD", "PLAN", "SERVICES", "THE", "WELFARE",
        }
        lookup_text = " ".join(
            filter(
                None,
                [lookup.plan_name, lookup.sponsor_name, *self._company_name_candidates(lookup)],
            )
        ).upper()
        lookup_words = {
            word.casefold()
            for word in re.findall(r"[A-Z0-9]+", lookup_text)
            if len(word) >= 4 and word not in generic_words
        }
        score += 2 * sum(1 for word in lookup_words if word in candidate_text)
        return score

    def _identity_from_lookup_match(self, match: dict[str, str]) -> dict:
        identity = {
            "customer_id": match.get("CustomerID"),
            "plan_id": match.get("PlanID"),
            "ftw_customer_id": match.get("FTWCustomerID"),
            "ftw_plan_id": match.get("FTWPlanID"),
        }
        return {key: value for key, value in identity.items() if value}

    def _merge_plan_lookup_identity(self, base: dict, lookup: FTWilliamsPlanLookup) -> dict:
        merged: dict[str, str] = {}
        if base.get("customer_id") and base.get("plan_id"):
            merged["customer_id"] = base["customer_id"]
            merged["plan_id"] = base["plan_id"]
        if base.get("ftw_customer_id") and base.get("ftw_plan_id"):
            merged["ftw_customer_id"] = base["ftw_customer_id"]
            merged["ftw_plan_id"] = base["ftw_plan_id"]
        if base.get("year"):
            merged["year"] = base["year"]
        match_identity = lookup.matched_identity or {}
        if match_identity.get("customer_id") and match_identity.get("plan_id"):
            merged["customer_id"] = match_identity["customer_id"]
            merged["plan_id"] = match_identity["plan_id"]
        if match_identity.get("ftw_customer_id") and match_identity.get("ftw_plan_id"):
            merged["ftw_customer_id"] = match_identity["ftw_customer_id"]
            merged["ftw_plan_id"] = match_identity["ftw_plan_id"]
        if lookup.year:
            merged["year"] = lookup.year
        return merged

    def _current_query_payload_identity(self, base: dict, lookup: FTWilliamsPlanLookup) -> dict:
        merged: dict[str, str] = {}
        if base.get("customer_id") and base.get("plan_id"):
            merged["customer_id"] = base["customer_id"]
            merged["plan_id"] = base["plan_id"]
        if base.get("ftw_customer_id") and base.get("ftw_plan_id"):
            merged["ftw_customer_id"] = base["ftw_customer_id"]
            merged["ftw_plan_id"] = base["ftw_plan_id"]
        if base.get("year"):
            merged["year"] = base["year"]

        if lookup.status == FTWilliamsPlanLookupStatus.MATCHED:
            match_identity = lookup.matched_identity or {}
            if match_identity.get("customer_id") and match_identity.get("plan_id"):
                merged["customer_id"] = match_identity["customer_id"]
                merged["plan_id"] = match_identity["plan_id"]
            if match_identity.get("ftw_customer_id") and match_identity.get("ftw_plan_id"):
                merged["ftw_customer_id"] = match_identity["ftw_customer_id"]
                merged["ftw_plan_id"] = match_identity["ftw_plan_id"]

        if lookup.year:
            merged["year"] = lookup.year
        return merged

    def _has_current_query_inputs(self, identity: dict) -> bool:
        return bool(identity.get("year")) and self._has_plan_identity(identity)

    def _has_plan_identity(self, identity: dict) -> bool:
        return bool(identity.get("customer_id") and identity.get("plan_id")) or bool(
            identity.get("ftw_customer_id") and identity.get("ftw_plan_id")
        )

    def _ftw_plan_page_url(self, identity: dict, target_year: str | None) -> str:
        default_template = (
            "https://ftwilliam.com/cgi-bin/index.cgi?"
            "#go=iframe&page=/cgi-bin/PlanDoc2.cgi&PerformDoc5500=1&"
            "plan={ftw_customer_id},{ftw_plan_id}&Year={year}"
        )
        template = (get_settings().ftw_plan_page_url_template or default_template).strip()
        values = {
            "customer_id": quote(str(identity.get("customer_id") or ""), safe=""),
            "plan_id": quote(str(identity.get("plan_id") or ""), safe=""),
            "ftw_customer_id": quote(str(identity.get("ftw_customer_id") or ""), safe=""),
            "ftw_plan_id": quote(str(identity.get("ftw_plan_id") or ""), safe=""),
            "year": quote(str(target_year or identity.get("year") or ""), safe=""),
        }
        required_placeholders = {"{ftw_customer_id}", "{ftw_plan_id}", "{year}"}
        if not required_placeholders.issubset(set(re.findall(r"\{[^{}]+\}", template))):
            return ""
        if not (values["ftw_customer_id"] and values["ftw_plan_id"] and values["year"]):
            return ""
        try:
            url = template.format(**values)
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower().rstrip(".")
            fragment = parse_qs(parsed.fragment, keep_blank_values=True)
            expected_plan = f"{values['ftw_customer_id']},{values['ftw_plan_id']}"
            if (
                parsed.scheme != "https"
                or (host != "ftwilliam.com" and not host.endswith(".ftwilliam.com"))
                or parsed.username
                or parsed.password
                or parsed.port not in {None, 443}
                or parsed.path != "/cgi-bin/index.cgi"
                or fragment.get("go") != ["iframe"]
                or fragment.get("page") != ["/cgi-bin/PlanDoc2.cgi"]
                or fragment.get("PerformDoc5500") != ["1"]
                or fragment.get("plan") != [expected_plan]
                or fragment.get("Year") != [values["year"]]
            ):
                return ""
            return url
        except (KeyError, ValueError):
            return ""

    def plan_page_url_for_review(self, review: FTWilliamsReview) -> str:
        return self._ftw_plan_page_url(self._identity_from_review(review), review.year)

    def _query_payload_base(self) -> dict:
        settings = get_settings()
        return {
            "customer_id": settings.ftwlink_sandbox_customer_id,
            "plan_id": settings.ftwlink_sandbox_plan_id,
            "year": settings.ftwlink_sandbox_year,
            "ftw_customer_id": settings.ftwlink_sandbox_ftw_customer_id,
            "ftw_plan_id": settings.ftwlink_sandbox_ftw_plan_id,
        }

    def _identity_from_status(self, status: FTWilliamsStatusItem | None) -> dict:
        if not status:
            return {}
        identity = {
            "ftw_customer_id": status.ftw_customer_id,
            "ftw_plan_id": status.ftw_plan_id,
            "ftw_seq_no": status.ftw_seq_no,
            "customer_id": status.customer_id,
            "plan_id": status.plan_id,
        }
        return {key: value for key, value in identity.items() if value}

    def _has_fatal_plan_query_error(self, statuses: list[FTWilliamsStatusItem]) -> bool:
        fatal_error_codes = {"54", "56"}
        return any(str(status.error_code or "") in fatal_error_codes for status in statuses)

    def _status_error(self, statuses: list[FTWilliamsStatusItem], ignore_error_codes: set[str] | None = None) -> str | None:
        ignored = ignore_error_codes or set()
        counts: dict[str, int] = {}
        ordered_messages: list[str] = []
        for status in statuses:
            error_code = str(status.error_code or "")
            if not error_code or error_code == "0" or error_code in ignored:
                continue
            message = f"{status.type or 'FTW'} error {status.error_code}: {status.error_desc}"
            if message not in counts:
                ordered_messages.append(message)
                counts[message] = 0
            counts[message] += 1
        if not ordered_messages:
            return None
        return "; ".join(
            f"{message} (x{counts[message]})" if counts[message] > 1 else message
            for message in ordered_messages
        )
