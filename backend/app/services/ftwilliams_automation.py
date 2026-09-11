import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from app.config import Settings, get_settings
from app.models import (
    ExtractedField,
    ExtractedFieldStatus,
    FieldPriority,
    Filing,
    FTWAutomationDecision,
    FTWAutomationStatus,
    FTWilliamsPlanLookupStatus,
    FTWilliamsQueryState,
    FTWilliamsReview,
    FTWilliamsReviewStatus,
    FTWilliamsScheduleAMatchRequest,
    FormType,
    AuditLog,
)
from app.repositories import Repository, get_repository
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.ftwilliams_tags import resolve_ftw_tag


_AUTOMATION_LOCKS: dict[str, asyncio.Lock] = {}


def automation_reset_values(settings: Settings, reason: str) -> dict:
    """Invalidate any prior automation result after source data changes."""
    enabled = settings.ftw_automation_enabled
    return {
        "automation_status": (
            FTWAutomationStatus.PROCESSING if enabled else FTWAutomationStatus.DISABLED
        ),
        "automation_reasons": [reason] if enabled else [],
        "automation_next_action": "QUERY_FTW_CURRENT" if enabled else None,
        "automation_policy_version": settings.ftw_automation_policy_version if enabled else None,
        "automation_last_evaluated_at": None,
        "automation_completed_at": None,
        "automation_run_id": None,
        "automation_bring_forward_approved_target_key": None,
        "automation_bring_forward_approved_at": None,
    }


@dataclass(frozen=True)
class FTWBringForwardResult:
    success: bool
    state: str
    message: str
    audit_path: str | None = None


class FTWAutomationPolicy:
    """One fail-closed decision point for straight-through FTW processing."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def evaluate(
        self,
        filing: Filing,
        review: FTWilliamsReview | None,
        fields: list[ExtractedField],
    ) -> FTWAutomationDecision:
        if not self.settings.ftw_automation_enabled:
            return FTWAutomationDecision(
                status=FTWAutomationStatus.DISABLED,
                eligible=False,
                reasons=["FT Williams automation is disabled; the established manual workflow remains active."],
                next_action="MANUAL_REVIEW",
                policy_version=self.settings.ftw_automation_policy_version,
            )

        if filing.automation_status == FTWAutomationStatus.COMPLETED and filing.automation_completed_at:
            return self._decision(
                FTWAutomationStatus.COMPLETED,
                True,
                ["This filing already completed a verified automation run."],
                None,
            )

        if (
            review
            and review.status == FTWilliamsReviewStatus.UPDATE_SENT
            and review.update_verification_attempted
            and review.update_verification_success is True
            and review.update_remaining_count == 0
        ):
            return self._decision(FTWAutomationStatus.COMPLETED, True, [], None)

        if review and review.status in {
            FTWilliamsReviewStatus.UPDATE_FAILED,
            FTWilliamsReviewStatus.UPDATE_UNKNOWN,
        }:
            return self._decision(
                FTWAutomationStatus.FAILED,
                False,
                [review.error_message or "The FT Williams update did not complete safely."],
                "RETRY_AFTER_CURRENT_QUERY",
            )

        if review is None or not review.current_query_sent:
            return self._decision(
                FTWAutomationStatus.PROCESSING,
                False,
                ["FT Williams current data has not been queried yet."],
                "QUERY_FTW_CURRENT",
            )

        target_error = self._test_target_error(review)
        if target_error:
            return self._decision(
                FTWAutomationStatus.DISABLED,
                False,
                [f"{target_error} The established manual workflow remains active."],
                "MANUAL_REVIEW",
            )

        if (
            not review.browser_mapping_confirmed
            or not str(review.ftw_browser_customer_id or "").strip()
            or not str(review.ftw_browser_plan_id or "").strip()
        ):
            return self._action_needed(
                "This plan needs a one-time confirmed FT Williams browser mapping before automatic processing.",
                "MAP_FTW_BROWSER_PLAN",
            )

        if review.bring_forward_required or not review.current_year_exists:
            target_key = self.bring_forward_target_key(review)
            if (
                filing.automation_bring_forward_submitted_at
                and filing.automation_bring_forward_target_key == target_key
            ):
                return self._action_needed(
                    "Bring Forward was submitted, but the current-year Schedule A is not visible through ftwLink yet. Re-query current data before attempting another browser action.",
                    "RETRY_AFTER_CURRENT_QUERY",
                )
            if self.settings.ftw_automation_bring_forward_enabled:
                if (
                    filing.automation_bring_forward_approved_target_key != target_key
                    or filing.automation_bring_forward_approved_at is None
                ):
                    return self._action_needed(
                        "Confirm the exact FT Williams plan and year before Bring Forward runs.",
                        "CONFIRM_BRING_FORWARD",
                    )
                return self._decision(
                    FTWAutomationStatus.BRING_FORWARD_REQUIRED,
                    True,
                    ["The current-year Schedule A is missing and must be brought forward."],
                    "AUTOMATE_BRING_FORWARD",
                )
            return self._action_needed(
                "The current-year Schedule A is missing and automatic Bring Forward is disabled.",
                "MANUAL_BRING_FORWARD",
            )

        reasons: list[str] = []
        if not review.configured:
            reasons.append("FT Williams is not configured.")
        if not review.current_query_success or review.current_query_complete is not True:
            reasons.append("The current FT Williams query is incomplete.")
        if review.query_state != FTWilliamsQueryState.MATCHED:
            reasons.append("The FT Williams client, plan, and year are not confirmed.")
        if not review.plan_lookup or review.plan_lookup.status != FTWilliamsPlanLookupStatus.MATCHED:
            reasons.append("The FT Williams plan lookup is not uniquely matched.")
        if review.ftw_editable is not True:
            reasons.append("FT Williams has not confirmed that the filing is editable.")
        if review.plan_year_conflict and not review.plan_year_resolution:
            reasons.append("The plan-year conflict needs a user decision.")
        if review.schedule_a_contract_type_mismatch or not review.schedule_a_contract_type_confirmed:
            reasons.append("The Schedule A contract type is not safely confirmed.")
        if not review.schedule_a_broker_match_complete:
            reasons.append("One or more broker rows need a matching decision.")

        schedule_match_error = self._schedule_match_error(review)
        if schedule_match_error:
            reasons.append(schedule_match_error)

        extracted_by_id = {str(field.id): field for field in fields if field.id}
        threshold = min(1.0, max(0.0, self.settings.ftw_automation_confidence_threshold))
        for comparison in review.fields:
            if comparison.validation_blocking:
                reasons.append(f"{comparison.label} has a blocking validation error.")
            if (
                comparison.changed
                and comparison.update_included
                and comparison.extraction_status in {
                    ExtractedFieldStatus.LOW_CONFIDENCE,
                    ExtractedFieldStatus.UNMAPPED,
                }
            ):
                reasons.append(f"{comparison.label} requires review.")
            if (
                comparison.priority == FieldPriority.HIGH
                and comparison.extraction_status == ExtractedFieldStatus.MISSING
            ):
                reasons.append(f"{comparison.label} requires review.")
            if comparison.changed and comparison.update_included and comparison.confidence < threshold:
                reasons.append(
                    f"{comparison.label} confidence {comparison.confidence:.0%} is below the {threshold:.0%} automation threshold."
                )
            if comparison.changed and comparison.update_included:
                extracted = extracted_by_id.get(str(comparison.field_id or ""))
                if not extracted or not (str(extracted.source_text or "").strip() or extracted.page is not None):
                    reasons.append(f"{comparison.label} has no source evidence.")

        if filing.missing_high_priority_count:
            reasons.append("One or more high-priority fields are missing.")

        changed_fields = [field for field in review.fields if field.changed and field.update_included]
        changed_forms = {field.form_type for field in changed_fields}
        if FormType.FORM_5500 in changed_forms and not review.update_xml_5500:
            reasons.append("The safe Form 5500 update payload was not generated.")
        if FormType.SCHEDULE_A in changed_forms and not review.update_xml_schedule_a:
            reasons.append("The safe Schedule A replacement payload was not generated.")
        if FormType.SCHEDULE_A in changed_forms and not self.settings.ftwlink_schedule_a_updates_enabled:
            reasons.append(
                "Automatic Schedule A updates are disabled until full replacement preservation is verified."
            )
        if (
            FormType.SCHEDULE_A in changed_forms
            and self.settings.ftwlink_schedule_a_single_record_only
            and len(
                {
                    str(record.get("ftw_seq_no") or "").strip()
                    for record in review.schedule_a_records or []
                    if str(record.get("ftw_seq_no") or "").strip() and record.get("query_results")
                }
            ) != 1
        ):
            reasons.append(
                "Automatic Schedule A updates currently require exactly one current Schedule A record."
            )

        reasons = list(dict.fromkeys(reasons))
        if reasons:
            next_action = (
                "MANUAL_SCHEDULE_A_UPDATE"
                if FormType.SCHEDULE_A in changed_forms
                and not self.settings.ftwlink_schedule_a_updates_enabled
                else "RESOLVE_ISSUES"
            )
            return self._decision(
                FTWAutomationStatus.ACTION_NEEDED,
                False,
                reasons,
                next_action,
            )
        if not changed_fields:
            return self._decision(
                FTWAutomationStatus.COMPLETED,
                True,
                ["FT Williams already matches the verified extracted values; no update is needed."],
                None,
            )
        return self._decision(FTWAutomationStatus.SAFE_TO_SEND, True, [], "AUTO_SEND")

    def _schedule_match_error(self, review: FTWilliamsReview) -> str | None:
        selected = review.schedule_a_match or {}
        if not selected:
            return "A Schedule A has not been safely matched or confirmed as new."
        if selected.get("create_new"):
            return None
        score = int(selected.get("score") or 0)
        strong_matches = int(selected.get("strong_matches") or 0)
        if score < 12 or strong_matches < 1:
            return "The selected Schedule A match is not strong enough for automatic sending."
        selected_seq = str(selected.get("ftw_seq_no") or "").strip()
        runner_scores = [
            int(candidate.get("score") or 0)
            for candidate in review.schedule_a_candidates
            if str(candidate.get("ftw_seq_no") or "").strip() != selected_seq
        ]
        if runner_scores and score - max(runner_scores) < 4:
            return "The Schedule A match is too close to another candidate."
        return None

    def _test_target_error(self, review: FTWilliamsReview) -> str | None:
        settings = self.settings
        configured_targets: list[tuple[str, str, str]] = []
        try:
            allowed_targets = json.loads(settings.ftw_automation_allowed_targets_json or "[]")
        except (TypeError, ValueError):
            allowed_targets = []
        if isinstance(allowed_targets, list):
            for target in allowed_targets:
                if not isinstance(target, dict):
                    continue
                customer_id = str(target.get("ftw_customer_id") or "").strip()
                plan_id = str(target.get("ftw_plan_id") or "").strip()
                if customer_id and plan_id:
                    configured_targets.append(
                        (customer_id, plan_id, self._year(target.get("year")))
                    )
        if settings.ftwlink_sandbox_ftw_customer_id and settings.ftwlink_sandbox_ftw_plan_id:
            configured_targets.append(
                (
                    str(settings.ftwlink_sandbox_ftw_customer_id),
                    str(settings.ftwlink_sandbox_ftw_plan_id),
                    self._year(settings.ftwlink_sandbox_year_end),
                )
            )
        if settings.ftwlink_sandbox_customer_id and settings.ftwlink_sandbox_plan_id:
            configured_targets.append(
                (
                    str(settings.ftwlink_sandbox_customer_id),
                    str(settings.ftwlink_sandbox_plan_id),
                    self._year(settings.ftwlink_sandbox_year),
                )
            )
        if not configured_targets:
            return "No FT Williams demo customer, plan, and year allowlist is configured."

        review_targets = [
            (str(review.ftw_customer_id or ""), str(review.ftw_plan_id or ""), self._year(review.year)),
            (str(review.customer_id or ""), str(review.plan_id or ""), self._year(review.year)),
            (
                str(review.ftw_browser_customer_id or ""),
                str(review.ftw_browser_plan_id or ""),
                self._year(review.year),
            ),
        ]
        for expected_customer, expected_plan, expected_year in configured_targets:
            for customer, plan, year in review_targets:
                if (
                    customer.casefold() == expected_customer.casefold()
                    and plan.casefold() == expected_plan.casefold()
                    and (not expected_year or year == expected_year)
                ):
                    return None
        return "This filing is outside the configured FT Williams demo-plan allowlist."

    @classmethod
    def bring_forward_target_key(cls, review: FTWilliamsReview) -> str:
        return "|".join(
            [
                str(review.ftw_browser_customer_id or "").strip().casefold(),
                str(review.ftw_browser_plan_id or "").strip().casefold(),
                cls._year(review.year),
            ]
        )

    @staticmethod
    def _year(value: object) -> str:
        matches = re.findall(r"(?:19|20)\d{2}", str(value or ""))
        return matches[-1] if matches else str(value or "").strip()

    def _action_needed(self, reason: str, next_action: str = "RESOLVE_ISSUES") -> FTWAutomationDecision:
        return self._decision(FTWAutomationStatus.ACTION_NEEDED, False, [reason], next_action)

    def _decision(
        self,
        status: FTWAutomationStatus,
        eligible: bool,
        reasons: list[str],
        next_action: str | None,
    ) -> FTWAutomationDecision:
        return FTWAutomationDecision(
            status=status,
            eligible=eligible,
            reasons=reasons,
            next_action=next_action,
            policy_version=self.settings.ftw_automation_policy_version,
        )


class FTWAutomationService:
    """Coordinates optional automation while delegating all writes to the proven FTW service."""

    def __init__(
        self,
        *,
        repo: Repository | None = None,
        review_service: FTWilliamsReviewService | None = None,
        bring_forward_agent=None,
        local_agent_service=None,
        settings: Settings | None = None,
    ):
        self.repo = repo or get_repository()
        self.settings = settings or get_settings()
        self.policy = FTWAutomationPolicy(self.settings)
        self.review_service = review_service or FTWilliamsReviewService()
        if local_agent_service is None and self.settings.ftw_local_agent_enabled:
            from app.services.ftwilliams_local_agent_jobs import FTWLocalAgentService

            local_agent_service = FTWLocalAgentService(repo=self.repo, settings=self.settings)
        self.local_agent_service = local_agent_service
        if (
            bring_forward_agent is None
            and self.settings.ftw_automation_bring_forward_enabled
            and not self.settings.ftw_local_agent_enabled
        ):
            from app.services.ftwilliams_browser import PlaywrightFTWBringForwardAgent

            bring_forward_agent = PlaywrightFTWBringForwardAgent(self.settings)
        self.bring_forward_agent = bring_forward_agent

    async def run(
        self,
        filing_id: str,
        *,
        review: FTWilliamsReview | None = None,
    ) -> FTWAutomationDecision:
        lease_id = str(uuid4())
        acquired = await self.repo.try_acquire_automation_lease(
            filing_id,
            lease_id,
            self.settings.ftw_automation_lease_seconds,
        )
        if not acquired:
            return self.policy._decision(
                FTWAutomationStatus.PROCESSING,
                False,
                ["Another automation worker is already processing this filing."],
                "WAIT_FOR_ACTIVE_RUN",
            )
        try:
            return await self._run_with_process_lock(filing_id, review=review)
        finally:
            await self.repo.release_automation_lease(filing_id, lease_id)

    async def _run_with_process_lock(
        self,
        filing_id: str,
        *,
        review: FTWilliamsReview | None = None,
    ) -> FTWAutomationDecision:
        lock = _AUTOMATION_LOCKS.setdefault(str(filing_id), asyncio.Lock())
        async with lock:
            filing = await self.repo.get_filing(filing_id)
            if not filing:
                raise ValueError("Filing not found")
            fields = await self.repo.list_fields(filing_id)
            active_review = review or await self.repo.get_ftwilliams_review(filing_id)
            active_review = await self._auto_select_new_schedule_a(
                filing_id,
                active_review,
                fields,
            )
            decision = self.policy.evaluate(filing, active_review, fields)
            run_id = filing.automation_run_id or str(uuid4())
            await self._persist_decision(filing_id, decision, run_id=run_id)
            await self.repo.add_audit(
                AuditLog(
                    filing_id=filing_id,
                    event="FTW_AUTOMATION_EVALUATED",
                    message="FT Williams automation policy evaluated the filing.",
                    details={
                        "status": decision.status.value,
                        "eligible": decision.eligible,
                        "reasons": decision.reasons,
                        "next_action": decision.next_action,
                        "policy_version": decision.policy_version,
                        "run_id": run_id,
                    },
                )
            )

            if decision.status == FTWAutomationStatus.BRING_FORWARD_REQUIRED:
                if self.settings.ftw_local_agent_enabled and self.local_agent_service is not None:
                    before_record_ids = self._schedule_a_record_ids(active_review)
                    try:
                        job = await self.local_agent_service.enqueue_bring_forward(
                            filing,
                            active_review,
                            run_id=run_id,
                            before_record_ids=before_record_ids,
                        )
                        job_workspace_id = getattr(job, "workspace_id", None)
                        agent_status = (
                            await self.local_agent_service.status(workspace_ids={job_workspace_id})
                            if job_workspace_id
                            else await self.local_agent_service.status()
                        )
                    except Exception as exc:
                        return await self._stop_safely(
                            filing_id,
                            run_id=run_id,
                            reason=f"The local FT Williams agent job could not be created: {exc}",
                            next_action="MANUAL_BRING_FORWARD",
                            audit_event="FTW_LOCAL_AGENT_JOB_FAILED",
                        )
                    waiting = FTWAutomationDecision(
                        status=(
                            FTWAutomationStatus.PROCESSING
                            if agent_status.connected
                            else FTWAutomationStatus.ACTION_NEEDED
                        ),
                        eligible=agent_status.connected,
                        reasons=[
                            "The local FT Williams agent is processing Bring Forward."
                            if agent_status.connected
                            else "Start or sign in to the local FT Williams agent; manual Bring Forward remains available."
                        ],
                        next_action=("WAIT_FOR_LOCAL_AGENT" if agent_status.connected else "START_LOCAL_AGENT"),
                        policy_version=self.settings.ftw_automation_policy_version,
                    )
                    await self._persist_decision(filing_id, waiting, run_id=run_id)
                    await self.repo.add_audit(
                        AuditLog(
                            filing_id=filing_id,
                            event="FTW_LOCAL_AGENT_JOB_QUEUED",
                            message="Bring Forward was queued for the paired client-local FT Williams agent.",
                            details={
                                "job_id": job.id,
                                "run_id": run_id,
                                "agent_connected": agent_status.connected,
                                "before_record_ids": before_record_ids,
                            },
                        )
                    )
                    return waiting
                if self.bring_forward_agent is None:
                    unavailable = FTWAutomationDecision(
                        status=FTWAutomationStatus.ACTION_NEEDED,
                        eligible=False,
                        reasons=["Automatic Bring Forward is enabled but no browser worker is available."],
                        next_action="LOGIN_TO_FTW",
                        policy_version=self.settings.ftw_automation_policy_version,
                    )
                    await self._persist_decision(filing_id, unavailable, run_id=run_id)
                    return unavailable
                await self._persist_decision(
                    filing_id,
                    FTWAutomationDecision(
                        status=FTWAutomationStatus.PROCESSING,
                        eligible=True,
                        reasons=["FT Williams Bring Forward is running for the configured demo plan."],
                        next_action="VERIFY_BRING_FORWARD",
                        policy_version=self.settings.ftw_automation_policy_version,
                    ),
                    run_id=run_id,
                )
                before_record_ids = self._schedule_a_record_ids(active_review)
                try:
                    result = await self.bring_forward_agent.bring_forward(filing_id, active_review)
                except Exception as exc:
                    return await self._stop_safely(
                        filing_id,
                        run_id=run_id,
                        reason=f"FT Williams Bring Forward stopped unexpectedly: {exc}",
                        next_action="RETRY",
                        audit_event="FTW_AUTOMATION_BRING_FORWARD_FAILED",
                    )
                await self.repo.add_audit(
                    AuditLog(
                        filing_id=filing_id,
                        event="FTW_AUTOMATION_BRING_FORWARD_COMPLETED" if result.success else "FTW_AUTOMATION_BRING_FORWARD_FAILED",
                        message=result.message,
                        details={
                            "run_id": run_id,
                            "state": result.state,
                            "audit_path": result.audit_path,
                            "before_record_ids": before_record_ids,
                        },
                    )
                )
                if not result.success:
                    failed_status = (
                        FTWAutomationStatus.ACTION_NEEDED
                        if result.state.upper() == "LOGIN_REQUIRED"
                        else FTWAutomationStatus.FAILED
                    )
                    failed = FTWAutomationDecision(
                        status=failed_status,
                        eligible=False,
                        reasons=[result.message],
                        next_action="LOGIN_TO_FTW" if failed_status == FTWAutomationStatus.ACTION_NEEDED else "RETRY",
                        policy_version=self.settings.ftw_automation_policy_version,
                    )
                    await self._persist_decision(filing_id, failed, run_id=run_id)
                    return failed
                await self.repo.update_filing(
                    filing_id,
                    {
                        "automation_bring_forward_target_key": self.policy.bring_forward_target_key(active_review),
                        "automation_bring_forward_submitted_at": datetime.utcnow(),
                        "automation_bring_forward_verified_at": None,
                        "automation_bring_forward_before_record_ids": before_record_ids,
                        "automation_bring_forward_new_record_ids": [],
                    },
                )
                try:
                    active_review = await self.review_service.prepare_review(
                        filing_id,
                        send_queries=True,
                        reuse_current_snapshot=False,
                    )
                except Exception as exc:
                    return await self._stop_safely(
                        filing_id,
                        run_id=run_id,
                        reason=f"Bring Forward was submitted, but the ftwLink re-query failed: {exc}",
                        next_action="RETRY_AFTER_CURRENT_QUERY",
                        audit_event="FTW_AUTOMATION_POST_BRING_FORWARD_QUERY_FAILED",
                    )
                await self.repo.upsert_ftwilliams_review(active_review)
                filing = await self.repo.get_filing(filing_id) or filing
                fields = await self.repo.list_fields(filing_id)
                active_review = await self._auto_select_new_schedule_a(
                    filing_id,
                    active_review,
                    fields,
                )
                after_record_ids = self._schedule_a_record_ids(active_review)
                new_record_ids = sorted(set(after_record_ids) - set(before_record_ids))
                if active_review.current_year_exists and not active_review.bring_forward_required:
                    await self.repo.update_filing(
                        filing_id,
                        {
                            "automation_bring_forward_verified_at": datetime.utcnow(),
                            "automation_bring_forward_new_record_ids": new_record_ids,
                        },
                    )
                    filing = await self.repo.get_filing(filing_id) or filing
                decision = self.policy.evaluate(filing, active_review, fields)
                await self._persist_decision(filing_id, decision, run_id=run_id)
                await self.repo.add_audit(
                    AuditLog(
                        filing_id=filing_id,
                        event="FTW_AUTOMATION_POST_BRING_FORWARD_QUERY",
                        message="FT Williams current data was queried again after Bring Forward.",
                        details={
                            "run_id": run_id,
                            "status": decision.status.value,
                            "reasons": decision.reasons,
                            "before_record_ids": before_record_ids,
                            "after_record_ids": after_record_ids,
                            "new_record_ids": new_record_ids,
                        },
                    )
                )

            if (
                decision.status != FTWAutomationStatus.SAFE_TO_SEND
                or not self.settings.ftw_automation_auto_send_enabled
            ):
                return decision

            processing = FTWAutomationDecision(
                status=FTWAutomationStatus.PROCESSING,
                eligible=True,
                reasons=["The verified demo filing is being sent through the guarded FT Williams workflow."],
                next_action="VERIFY_FTW_UPDATE",
                policy_version=self.settings.ftw_automation_policy_version,
            )
            await self._persist_decision(filing_id, processing, run_id=run_id)
            await self.repo.add_audit(
                AuditLog(
                    filing_id=filing_id,
                    event="FTW_AUTOMATION_SEND_STARTED",
                    message="Safe demo-plan filing entered the existing guarded FT Williams send workflow.",
                    details={"run_id": run_id, "policy_version": decision.policy_version},
                )
            )
            try:
                updated_review = await self.review_service.approve_and_update(
                    filing_id,
                    reason=f"Automated demo-plan update ({decision.policy_version})",
                    send_to_ftw=True,
                    refresh_current_before_update=True,
                    run_edit_checks=True,
                    override_blockers=False,
                )
                if updated_review is None:
                    raise ValueError("FT Williams did not return an update result.")
                await self.repo.upsert_ftwilliams_review(updated_review)
                refreshed_filing = await self.repo.get_filing(filing_id) or filing
                completed = self.policy.evaluate(refreshed_filing, updated_review, fields)
                if completed.status != FTWAutomationStatus.COMPLETED:
                    completed = FTWAutomationDecision(
                        status=FTWAutomationStatus.FAILED,
                        eligible=False,
                        reasons=[updated_review.error_message or "FT Williams read-back verification did not complete."],
                        next_action="RETRY_AFTER_CURRENT_QUERY",
                        policy_version=self.settings.ftw_automation_policy_version,
                    )
                await self._persist_decision(filing_id, completed, run_id=run_id)
                await self.repo.add_audit(
                    AuditLog(
                        filing_id=filing_id,
                        event=(
                            "FTW_AUTOMATION_COMPLETED"
                            if completed.status == FTWAutomationStatus.COMPLETED
                            else "FTW_AUTOMATION_FAILED"
                        ),
                        message=(
                            "Automated FT Williams update was verified by read-back."
                            if completed.status == FTWAutomationStatus.COMPLETED
                            else "Automated FT Williams update did not pass read-back verification."
                        ),
                        details={"run_id": run_id, "reasons": completed.reasons},
                    )
                )
                return completed
            except Exception as exc:
                failed = FTWAutomationDecision(
                    status=FTWAutomationStatus.FAILED,
                    eligible=False,
                    reasons=[str(exc)],
                    next_action="RETRY_AFTER_CURRENT_QUERY",
                    policy_version=self.settings.ftw_automation_policy_version,
                )
                await self._persist_decision(filing_id, failed, run_id=run_id)
                await self.repo.add_audit(
                    AuditLog(
                        filing_id=filing_id,
                        event="FTW_AUTOMATION_FAILED",
                        message="Automated FT Williams processing stopped safely.",
                        details={"run_id": run_id, "error": str(exc)},
                    )
                )
                return failed

    async def _auto_select_new_schedule_a(
        self,
        filing_id: str,
        review: FTWilliamsReview | None,
        fields: list[ExtractedField],
    ) -> FTWilliamsReview | None:
        if (
            not review
            or self.policy._test_target_error(review)
            or review.schedule_a_match
            or review.bring_forward_required
            or not review.current_year_exists
            or not review.schedule_a_candidates
            or not review.schedule_a_records
        ):
            return review
        if any(int(candidate.get("strong_matches") or 0) > 0 for candidate in review.schedule_a_candidates):
            return review
        candidate_sequences = {
            str(candidate.get("ftw_seq_no") or "").strip()
            for candidate in review.schedule_a_candidates
            if str(candidate.get("ftw_seq_no") or "").strip()
        }
        record_sequences = {
            str(record.get("ftw_seq_no") or "").strip()
            for record in review.schedule_a_records
            if str(record.get("ftw_seq_no") or "").strip() and record.get("query_results")
        }
        if not candidate_sequences or not candidate_sequences.issubset(record_sequences):
            return review

        threshold = min(1.0, max(0.0, self.settings.ftw_automation_confidence_threshold))
        carrier_field = next(
            (
                field
                for field in fields
                if resolve_ftw_tag(field) == "InsCarrierName"
                and field.confidence >= threshold
                and field.status not in {
                    ExtractedFieldStatus.MISSING,
                    ExtractedFieldStatus.LOW_CONFIDENCE,
                    ExtractedFieldStatus.UNMAPPED,
                }
                and (str(field.source_text or "").strip() or field.page is not None)
                and str(field.proposed_value or field.value or "").strip()
            ),
            None,
        )
        if carrier_field is None:
            return review
        carrier = str(carrier_field.proposed_value or carrier_field.value).strip()
        selected = await self.review_service.select_schedule_a_match(
            filing_id,
            FTWilliamsScheduleAMatchRequest(
                create_new=True,
                carrier=carrier,
                schedule_desc=carrier,
            ),
        )
        await self.repo.upsert_ftwilliams_review(selected)
        await self.repo.add_audit(
            AuditLog(
                filing_id=filing_id,
                event="FTW_AUTOMATION_SCHEDULE_A_ADD_NEW_SELECTED",
                message="Automation selected Add as new because no existing Schedule A had a strong identity match.",
                details={
                    "candidate_count": len(review.schedule_a_candidates),
                    "carrier": carrier,
                    "policy_version": self.settings.ftw_automation_policy_version,
                },
            )
        )
        return selected

    @staticmethod
    def _schedule_a_record_ids(review: FTWilliamsReview | None) -> list[str]:
        if not review:
            return []
        values: set[str] = set()
        for record in review.schedule_a_records or []:
            value = str(
                record.get("ftw_seq_no")
                or record.get("FTWSeqNo")
                or record.get("SeqNo")
                or ""
            ).strip()
            if value:
                values.add(value)
        return sorted(values)

    async def _persist_decision(
        self,
        filing_id: str,
        decision: FTWAutomationDecision,
        *,
        run_id: str,
    ) -> None:
        values = {
            "automation_status": decision.status,
            "automation_reasons": list(decision.reasons),
            "automation_next_action": decision.next_action,
            "automation_policy_version": decision.policy_version,
            "automation_last_evaluated_at": decision.evaluated_at,
            "automation_run_id": run_id,
            "automation_completed_at": None,
        }
        if decision.status == FTWAutomationStatus.COMPLETED:
            values["automation_completed_at"] = datetime.utcnow()
        await self.repo.update_filing(filing_id, values)

    async def _stop_safely(
        self,
        filing_id: str,
        *,
        run_id: str,
        reason: str,
        next_action: str,
        audit_event: str,
    ) -> FTWAutomationDecision:
        decision = FTWAutomationDecision(
            status=FTWAutomationStatus.FAILED,
            eligible=False,
            reasons=[reason],
            next_action=next_action,
            policy_version=self.settings.ftw_automation_policy_version,
        )
        await self._persist_decision(filing_id, decision, run_id=run_id)
        await self.repo.add_audit(
            AuditLog(
                filing_id=filing_id,
                event=audit_event,
                message="Automated FT Williams processing stopped safely.",
                details={"run_id": run_id, "error": reason},
            )
        )
        return decision
