import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import app.repositories as repositories
from app.api.filings import confirm_ftwilliams_bring_forward
from app.repositories import MemoryRepository

from app.config import Settings
from app.models import (
    ExtractedField,
    ExtractedFieldStatus,
    FieldPriority,
    Filing,
    FilingStatus,
    FormType,
    FTWAutomationStatus,
    FTWAutomationDecision,
    FTWilliamsComparisonField,
    FTWilliamsPlanLookup,
    FTWilliamsPlanLookupStatus,
    FTWilliamsQueryState,
    FTWilliamsReview,
    FTWilliamsReviewStatus,
    FTWLocalAgentDeviceStatus,
    FTWLocalAgentStatusResponse,
)
from app.services.ftwilliams_automation import (
    FTWAutomationPolicy,
    FTWAutomationService,
    FTWBringForwardResult,
    automation_reset_values,
)
from app.services.filing_pipeline import auto_query_ftw_current
from app.services.ftwilliams_browser import PlaywrightFTWBringForwardAgent


def run_async(coro):
    return asyncio.run(coro)


class FakeAutomationReviewService:
    def __init__(self, review: FTWilliamsReview, refreshed_review: FTWilliamsReview | None = None):
        self.review = review
        self.refreshed_review = refreshed_review
        self.send_calls: list[dict] = []
        self.query_calls: list[dict] = []
        self.schedule_match_calls: list[dict] = []

    async def prepare_review(self, filing_id: str, send_queries: bool = False, **kwargs):
        self.query_calls.append({"filing_id": filing_id, "send_queries": send_queries, **kwargs})
        return self.refreshed_review or self.review

    async def approve_and_update(self, filing_id: str, **kwargs):
        self.send_calls.append({"filing_id": filing_id, **kwargs})
        completed = self.review.model_copy(deep=True)
        completed.status = FTWilliamsReviewStatus.UPDATE_SENT
        completed.update_verification_attempted = True
        completed.update_verification_success = True
        completed.update_remaining_count = 0
        completed.update_confirmed_count = 1
        return completed

    async def select_schedule_a_match(self, filing_id: str, payload):
        self.schedule_match_calls.append({"filing_id": filing_id, "payload": payload})
        selected = (self.refreshed_review or self.review).model_copy(deep=True)
        selected.schedule_a_match = {
            "create_new": True,
            "source": "NEW_SCHEDULE_A",
            "schedule_desc": payload.schedule_desc or payload.carrier,
        }
        return selected


class FailedReadbackReviewService(FakeAutomationReviewService):
    async def approve_and_update(self, filing_id: str, **kwargs):
        self.send_calls.append({"filing_id": filing_id, **kwargs})
        failed = self.review.model_copy(deep=True)
        failed.status = FTWilliamsReviewStatus.UPDATE_SENT
        failed.update_verification_attempted = True
        failed.update_verification_success = False
        failed.update_remaining_count = 1
        failed.update_verification_mismatches = [{"field": "InsContractNum"}]
        failed.error_message = "FT Williams read-back did not match the attempted update."
        return failed


class FakeBringForwardAgent:
    def __init__(self, result: FTWBringForwardResult):
        self.result = result
        self.calls: list[dict] = []

    async def bring_forward(self, filing_id: str, review: FTWilliamsReview):
        self.calls.append({"filing_id": filing_id, "review": review})
        return self.result


class RaisingBringForwardAgent:
    async def bring_forward(self, filing_id: str, review: FTWilliamsReview):
        raise RuntimeError("FT Williams browser worker stopped unexpectedly.")


class RaisingRequeryReviewService(FakeAutomationReviewService):
    async def prepare_review(self, filing_id: str, send_queries: bool = False, **kwargs):
        raise RuntimeError("ftwLink re-query timed out after Bring Forward.")


class FakeLocalAgentService:
    def __init__(self, *, connected: bool):
        self.connected = connected
        self.enqueue_calls: list[dict] = []

    async def enqueue_bring_forward(self, filing, review, **kwargs):
        self.enqueue_calls.append({"filing": filing, "review": review, **kwargs})
        return type("LocalJob", (), {"id": "local-job-1"})()

    async def status(self):
        return FTWLocalAgentStatusResponse(
            enabled=True,
            connected=self.connected,
            status=(
                FTWLocalAgentDeviceStatus.CONNECTED
                if self.connected
                else FTWLocalAgentDeviceStatus.OFFLINE
            ),
        )


class FTWAutomationPolicyTests(unittest.TestCase):
    @staticmethod
    def safe_case():
        filing = Filing(
            id="filing-1",
            file_name="schedule-a.pdf",
            content_type="application/pdf",
            file_size=100,
            s3_key="schedule-a.pdf",
            status=FilingStatus.READY_FOR_APPROVAL,
        )
        extracted = ExtractedField(
            id="field-1",
            filing_id="filing-1",
            source_field_name="Contract number",
            normalized_field_name="contract_number",
            mapped_rule_key="schedule_a_1d",
            priority=FieldPriority.HIGH,
            value="HL-100",
            proposed_value="HL-100",
            confidence=0.99,
            source_text="Contract number HL-100",
            form_type=FormType.SCHEDULE_A,
            status=ExtractedFieldStatus.MATCHED,
        )
        review = FTWilliamsReview(
            filing_id="filing-1",
            configured=True,
            current_query_sent=True,
            current_query_success=True,
            current_query_complete=True,
            current_year_exists=True,
            query_state=FTWilliamsQueryState.MATCHED,
            ftw_editable=True,
            year="2025",
            ftw_customer_id="highland-demo",
            ftw_plan_id="001",
            ftw_browser_customer_id="highland-demo",
            ftw_browser_plan_id="001",
            browser_mapping_confirmed=True,
            plan_lookup=FTWilliamsPlanLookup(status=FTWilliamsPlanLookupStatus.MATCHED),
            schedule_a_match={
                "ftw_seq_no": "1",
                "score": 20,
                "strong_matches": 2,
                "match_reasons": ["Exact contract", "Carrier EIN"],
            },
            schedule_a_candidates=[
                {"ftw_seq_no": "1", "score": 20, "strong_matches": 2},
                {"ftw_seq_no": "2", "score": 4, "strong_matches": 0},
            ],
            schedule_a_records=[{"ftw_seq_no": "1", "query_results": {"InsContractNum": "HL-100"}}],
            schedule_a_broker_match_complete=True,
            schedule_a_contract_type_confirmed=True,
            update_xml_schedule_a="<DOLScheduleAData><InsContractNum>HL-100</InsContractNum></DOLScheduleAData>",
            fields=[
                FTWilliamsComparisonField(
                    field_id="field-1",
                    label="Contract number",
                    form_type=FormType.SCHEDULE_A,
                    priority=FieldPriority.HIGH,
                    confidence=0.99,
                    extraction_status=ExtractedFieldStatus.MATCHED,
                    current_value="HL-099",
                    extracted_value="HL-100",
                    proposed_value="HL-100",
                    changed=True,
                    update_included=True,
                    validation_status="VALID",
                    validation_blocking=False,
                )
            ],
        )
        settings = Settings(
            ftw_automation_enabled=True,
            ftw_automation_auto_send_enabled=True,
            ftwlink_schedule_a_updates_enabled=True,
            ftwlink_sandbox_ftw_customer_id="highland-demo",
            ftwlink_sandbox_ftw_plan_id="001",
            ftwlink_sandbox_year_end="2025",
        )
        return filing, review, extracted, settings

    @staticmethod
    def approve_bring_forward_target(filing: Filing, review: FTWilliamsReview) -> None:
        filing.automation_bring_forward_approved_target_key = FTWAutomationPolicy.bring_forward_target_key(review)
        filing.automation_bring_forward_approved_at = datetime.utcnow()

    def test_automation_requires_confirmed_browser_plan_mapping(self):
        filing, review, extracted, settings = self.safe_case()
        review.browser_mapping_confirmed = False

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.next_action, "MAP_FTW_BROWSER_PLAN")
        self.assertIn("one-time", decision.reasons[0].lower())

    def test_disabled_automation_keeps_filing_on_manual_workflow(self):
        filing = Filing(
            id="filing-1",
            file_name="schedule-a.pdf",
            content_type="application/pdf",
            file_size=100,
            s3_key="schedule-a.pdf",
        )
        settings = Settings(ftw_automation_enabled=False)

        decision = FTWAutomationPolicy(settings).evaluate(filing, None, [])

        self.assertEqual(decision.status, FTWAutomationStatus.DISABLED)
        self.assertFalse(decision.eligible)
        self.assertIn("disabled", decision.reasons[0].lower())

    def test_source_change_clears_completed_result_before_requery(self):
        settings = Settings(ftw_automation_enabled=True)

        values = automation_reset_values(settings, "Source changed.")

        self.assertEqual(values["automation_status"], FTWAutomationStatus.PROCESSING)
        self.assertEqual(values["automation_next_action"], "QUERY_FTW_CURRENT")
        self.assertIsNone(values["automation_completed_at"])
        self.assertIsNone(values["automation_run_id"])
        self.assertIsNone(values["automation_bring_forward_approved_target_key"])
        self.assertIsNone(values["automation_bring_forward_approved_at"])

    def test_repository_automation_lease_prevents_parallel_workers(self):
        repo = MemoryRepository()
        filing, _review, _extracted, _settings = self.safe_case()
        saved = run_async(repo.create_filing(filing))

        self.assertTrue(run_async(repo.try_acquire_automation_lease(saved.id, "worker-1", 600)))
        self.assertFalse(run_async(repo.try_acquire_automation_lease(saved.id, "worker-2", 600)))
        run_async(repo.release_automation_lease(saved.id, "worker-2"))
        self.assertFalse(run_async(repo.try_acquire_automation_lease(saved.id, "worker-2", 600)))
        run_async(repo.release_automation_lease(saved.id, "worker-1"))
        self.assertTrue(run_async(repo.try_acquire_automation_lease(saved.id, "worker-2", 600)))

    def test_verified_demo_filing_is_safe_to_send(self):
        filing, review, extracted, settings = self.safe_case()

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.SAFE_TO_SEND)
        self.assertTrue(decision.eligible)
        self.assertEqual(decision.reasons, [])

    def test_single_record_release_stops_automation_for_multiple_schedule_as(self):
        filing, review, extracted, settings = self.safe_case()
        review.schedule_a_records.append(
            {"ftw_seq_no": "2", "query_results": {"InsContractNum": "OTHER-2"}}
        )

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertFalse(decision.eligible)
        self.assertTrue(any("exactly one current Schedule A" in reason for reason in decision.reasons))

    def test_low_confidence_required_field_stops_automatic_send(self):
        filing, review, extracted, settings = self.safe_case()
        extracted.confidence = 0.90
        review.fields[0].confidence = 0.90

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertFalse(decision.eligible)
        self.assertTrue(any("95%" in reason for reason in decision.reasons))

    def test_low_confidence_changed_medium_priority_field_stops_automatic_send(self):
        filing, review, extracted, settings = self.safe_case()
        extracted.priority = FieldPriority.MEDIUM
        extracted.confidence = 0.80
        review.fields[0].priority = FieldPriority.MEDIUM
        review.fields[0].confidence = 0.80

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertFalse(decision.eligible)
        self.assertTrue(any("95%" in reason for reason in decision.reasons))

    def test_reviewer_confirmed_low_confidence_field_is_safe_to_send(self):
        filing, review, extracted, settings = self.safe_case()
        extracted.status = ExtractedFieldStatus.EDITED
        extracted.confidence = 0.50
        extracted.source_text = ""
        review.fields[0].extraction_status = ExtractedFieldStatus.LOW_CONFIDENCE
        review.fields[0].confidence = 0.50

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.SAFE_TO_SEND)
        self.assertTrue(decision.eligible)
        self.assertEqual(decision.reasons, [])

    def test_safe_filing_uses_manual_send_action_when_auto_send_is_disabled(self):
        filing, review, extracted, settings = self.safe_case()
        settings.ftw_automation_auto_send_enabled = False

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.SAFE_TO_SEND)
        self.assertEqual(decision.next_action, "MANUAL_SEND")

    def test_missing_required_field_stops_automatic_send(self):
        filing, review, extracted, settings = self.safe_case()
        extracted.status = ExtractedFieldStatus.MISSING
        extracted.value = extracted.proposed_value = ""
        review.fields[0].extraction_status = ExtractedFieldStatus.MISSING
        review.fields[0].extracted_value = review.fields[0].proposed_value = ""
        filing.missing_high_priority_count = 1

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertTrue(any("requires review" in reason for reason in decision.reasons))

    def test_unresolved_broker_match_stops_automatic_send(self):
        filing, review, extracted, settings = self.safe_case()
        review.schedule_a_broker_match_complete = False

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertTrue(any("broker" in reason.lower() for reason in decision.reasons))

    def test_locked_ftw_filing_stops_automatic_send(self):
        filing, review, extracted, settings = self.safe_case()
        review.ftw_editable = False
        review.ftw_locked_status = "Locked"

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertTrue(any("editable" in reason.lower() for reason in decision.reasons))

    def test_ambiguous_schedule_match_stops_automatic_send(self):
        filing, review, extracted, settings = self.safe_case()
        review.schedule_a_candidates[1]["score"] = 18
        review.schedule_a_candidates[1]["strong_matches"] = 2

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertTrue(any("too close" in reason.lower() for reason in decision.reasons))

    def test_missing_current_year_requires_manual_action_when_browser_automation_is_off(self):
        filing, review, extracted, settings = self.safe_case()
        review.current_year_exists = False
        review.bring_forward_required = True
        settings.ftw_automation_bring_forward_enabled = False

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertEqual(decision.next_action, "MANUAL_BRING_FORWARD")

    def test_missing_current_year_requires_exact_target_confirmation_before_automation(self):
        filing, review, extracted, settings = self.safe_case()
        review.current_year_exists = False
        review.bring_forward_required = True
        settings.ftw_automation_bring_forward_enabled = True

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.next_action, "CONFIRM_BRING_FORWARD")

        filing.automation_bring_forward_approved_target_key = FTWAutomationPolicy.bring_forward_target_key(review)
        filing.automation_bring_forward_approved_at = datetime.utcnow()
        confirmed = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])
        self.assertEqual(confirmed.status, FTWAutomationStatus.BRING_FORWARD_REQUIRED)
        self.assertTrue(confirmed.eligible)

    def test_confirmation_endpoint_records_target_bound_approval_and_audit(self):
        filing, review, _extracted, settings = self.safe_case()
        review.current_year_exists = False
        review.bring_forward_required = True
        settings.ftw_automation_bring_forward_enabled = True
        repo = MemoryRepository()
        repositories._repository = repo
        try:
            saved = run_async(repo.create_filing(filing))
            review.filing_id = str(saved.id)
            run_async(repo.upsert_ftwilliams_review(review))
            queued = FTWAutomationDecision(
                status=FTWAutomationStatus.PROCESSING,
                eligible=True,
                reasons=["Queued safely."],
                next_action="WAIT_FOR_LOCAL_AGENT",
                policy_version=settings.ftw_automation_policy_version,
            )
            with (
                patch("app.api.filings.get_settings", return_value=settings),
                patch("app.api.filings.FTWAutomationService.run", new=AsyncMock(return_value=queued)),
            ):
                result = run_async(confirm_ftwilliams_bring_forward(str(saved.id)))

            updated = run_async(repo.get_filing(str(saved.id)))
            self.assertEqual(
                updated.automation_bring_forward_approved_target_key,
                FTWAutomationPolicy.bring_forward_target_key(review),
            )
            self.assertIsNotNone(updated.automation_bring_forward_approved_at)
            audits = run_async(repo.list_audit_logs(str(saved.id)))
            self.assertTrue(any(audit.event == "FTW_AUTOMATION_BRING_FORWARD_APPROVED" for audit in audits))
            self.assertEqual(result["automation_next_action"], "WAIT_FOR_LOCAL_AGENT")
        finally:
            repositories._repository = None

    def test_schedule_a_automation_stays_manual_while_replacement_updates_are_disabled(self):
        filing, review, extracted, settings = self.safe_case()
        settings.ftwlink_schedule_a_updates_enabled = False

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.next_action, "MANUAL_SCHEDULE_A_UPDATE")

    def test_filing_outside_demo_allowlist_stays_on_existing_manual_workflow(self):
        filing, review, extracted, settings = self.safe_case()
        review.ftw_customer_id = "production-customer"
        review.ftw_browser_customer_id = "production-browser-customer"
        review.ftw_browser_plan_id = "production-browser-plan"

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.DISABLED)
        self.assertFalse(decision.eligible)
        self.assertIn("outside", decision.reasons[0].lower())
        self.assertEqual(decision.next_action, "MANUAL_REVIEW")

    def test_json_allowlist_supports_multiple_demo_plans(self):
        filing, review, extracted, settings = self.safe_case()
        settings.ftwlink_sandbox_ftw_customer_id = None
        settings.ftwlink_sandbox_ftw_plan_id = None
        settings.ftw_automation_allowed_targets_json = """[
          {"ftw_customer_id":"demo","ftw_plan_id":"001","year":"2025"},
          {"ftw_customer_id":"highland-demo","ftw_plan_id":"001","year":"2025"},
          {"ftw_customer_id":"demo","ftw_plan_id":"003","year":"2025"},
          {"ftw_customer_id":"demo","ftw_plan_id":"004","year":"2025"},
          {"ftw_customer_id":"demo","ftw_plan_id":"005","year":"2025"}
        ]"""

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.SAFE_TO_SEND)

    def test_allowlist_accepts_confirmed_browser_ids_when_ftwlink_ids_differ(self):
        filing, review, extracted, settings = self.safe_case()
        review.ftw_customer_id = "internal-customer"
        review.ftw_plan_id = "internal-plan"
        review.ftw_browser_customer_id = "browser-customer"
        review.ftw_browser_plan_id = "browser-plan"
        review.browser_mapping_confirmed = True
        settings.ftw_automation_allowed_targets_json = """[
            {"ftw_customer_id":"browser-customer","ftw_plan_id":"browser-plan","year":"2025"}
        ]"""

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.SAFE_TO_SEND)

    def test_invalid_json_allowlist_fails_closed(self):
        filing, review, extracted, settings = self.safe_case()
        settings.ftwlink_sandbox_ftw_customer_id = None
        settings.ftwlink_sandbox_ftw_plan_id = None
        settings.ftw_automation_allowed_targets_json = "not-json"

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.DISABLED)
        self.assertIn("allowlist", decision.reasons[0].lower())
        self.assertEqual(decision.next_action, "MANUAL_REVIEW")

    def test_safe_demo_filing_uses_guarded_sender_and_completes_after_readback(self):
        filing, review, extracted, settings = self.safe_case()
        repo = MemoryRepository()
        saved_filing = run_async(repo.create_filing(filing))
        extracted.filing_id = saved_filing.id
        review.filing_id = saved_filing.id
        run_async(repo.add_fields([extracted]))
        review.fields[0].field_id = extracted.id
        run_async(repo.upsert_ftwilliams_review(review))
        review_service = FakeAutomationReviewService(review)
        service = FTWAutomationService(
            repo=repo,
            review_service=review_service,
            settings=settings,
        )

        decision = run_async(service.run(saved_filing.id, review=review))

        self.assertEqual(decision.status, FTWAutomationStatus.COMPLETED)
        self.assertEqual(len(review_service.send_calls), 1)
        self.assertTrue(review_service.send_calls[0]["send_to_ftw"])
        self.assertTrue(review_service.send_calls[0]["refresh_current_before_update"])
        self.assertTrue(review_service.send_calls[0]["run_edit_checks"])
        updated = run_async(repo.get_filing(saved_filing.id))
        self.assertEqual(updated.automation_status, FTWAutomationStatus.COMPLETED)
        self.assertIsNotNone(updated.automation_completed_at)

    def test_readback_mismatch_marks_automation_failed_without_second_send(self):
        filing, review, extracted, settings = self.safe_case()
        repo = MemoryRepository()
        saved_filing = run_async(repo.create_filing(filing))
        extracted.filing_id = saved_filing.id
        review.filing_id = saved_filing.id
        run_async(repo.add_fields([extracted]))
        review.fields[0].field_id = extracted.id
        review_service = FailedReadbackReviewService(review)
        service = FTWAutomationService(repo=repo, review_service=review_service, settings=settings)

        decision = run_async(service.run(saved_filing.id, review=review))

        self.assertEqual(decision.status, FTWAutomationStatus.FAILED)
        self.assertEqual(decision.next_action, "RETRY_AFTER_CURRENT_QUERY")
        self.assertEqual(len(review_service.send_calls), 1)

    def test_post_extraction_query_evaluates_enabled_automation_without_sending(self):
        filing, review, extracted, settings = self.safe_case()
        settings.ftw_automation_auto_send_enabled = False
        repo = MemoryRepository()
        repositories._repository = repo
        try:
            saved_filing = run_async(repo.create_filing(filing))
            extracted.filing_id = saved_filing.id
            review.filing_id = saved_filing.id
            run_async(repo.add_fields([extracted]))
            review.fields[0].field_id = extracted.id
            review_service = FakeAutomationReviewService(review)

            with patch("app.services.filing_pipeline.get_settings", return_value=settings):
                run_async(auto_query_ftw_current(saved_filing.id, review_service=review_service))

            updated = run_async(repo.get_filing(saved_filing.id))
            self.assertEqual(updated.automation_status, FTWAutomationStatus.SAFE_TO_SEND)
            self.assertEqual(review_service.send_calls, [])
        finally:
            repositories._repository = None

    def test_missing_schedule_a_queues_client_local_agent_without_using_cloud_browser(self):
        filing, review, extracted, settings = self.safe_case()
        settings.ftw_automation_bring_forward_enabled = True
        settings.ftw_automation_auto_send_enabled = False
        settings.ftw_local_agent_enabled = True
        review.current_year_exists = False
        review.bring_forward_required = True
        review.schedule_a_match = None
        review.schedule_a_candidates = []
        review.schedule_a_records = []
        review.fields = []
        review.ftw_plan_url = (
            "https://www.ftwilliam.com/cgi-bin/index.cgi#go=iframe&"
            "plan=highland-demo,001&Year=2025"
        )
        review.plan_lookup = FTWilliamsPlanLookup(
            status=FTWilliamsPlanLookupStatus.MATCHED,
            company_employer_id="12-3456789",
            plan_number="501",
            plan_name="Demo Health and Welfare Plan",
            year="2025",
        )
        self.approve_bring_forward_target(filing, review)
        repo = MemoryRepository()
        saved_filing = run_async(repo.create_filing(filing))
        review.filing_id = str(saved_filing.id)
        local_agent = FakeLocalAgentService(connected=True)
        browser_agent = FakeBringForwardAgent(
            FTWBringForwardResult(success=True, state="SUBMITTED", message="must not run")
        )
        service = FTWAutomationService(
            repo=repo,
            review_service=FakeAutomationReviewService(review),
            local_agent_service=local_agent,
            bring_forward_agent=browser_agent,
            settings=settings,
        )

        decision = run_async(service.run(str(saved_filing.id), review=review))

        self.assertEqual(decision.status, FTWAutomationStatus.PROCESSING)
        self.assertEqual(decision.next_action, "WAIT_FOR_LOCAL_AGENT")
        self.assertEqual(len(local_agent.enqueue_calls), 1)
        self.assertEqual(browser_agent.calls, [])

    def test_offline_client_local_agent_preserves_manual_bring_forward_fallback(self):
        filing, review, _extracted, settings = self.safe_case()
        settings.ftw_automation_bring_forward_enabled = True
        settings.ftw_local_agent_enabled = True
        review.current_year_exists = False
        review.bring_forward_required = True
        review.schedule_a_records = []
        review.fields = []
        self.approve_bring_forward_target(filing, review)
        repo = MemoryRepository()
        saved_filing = run_async(repo.create_filing(filing))
        review.filing_id = str(saved_filing.id)
        local_agent = FakeLocalAgentService(connected=False)
        service = FTWAutomationService(
            repo=repo,
            review_service=FakeAutomationReviewService(review),
            local_agent_service=local_agent,
            settings=settings,
        )

        decision = run_async(service.run(str(saved_filing.id), review=review))

        self.assertEqual(decision.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertEqual(decision.next_action, "START_LOCAL_AGENT")
        self.assertIn("manual Bring Forward remains available", decision.reasons[0])

    def test_bring_forward_success_requeries_before_continuing(self):
        filing, safe_review, extracted, settings = self.safe_case()
        settings.ftw_automation_bring_forward_enabled = True
        settings.ftw_automation_auto_send_enabled = False
        missing_review = safe_review.model_copy(deep=True)
        missing_review.current_year_exists = False
        missing_review.bring_forward_required = True
        missing_review.schedule_a_match = None
        missing_review.schedule_a_candidates = []
        missing_review.schedule_a_records = []
        missing_review.fields = []
        self.approve_bring_forward_target(filing, missing_review)
        repo = MemoryRepository()
        saved_filing = run_async(repo.create_filing(filing))
        extracted.filing_id = saved_filing.id
        missing_review.filing_id = saved_filing.id
        safe_review.filing_id = saved_filing.id
        run_async(repo.add_fields([extracted]))
        safe_review.fields[0].field_id = extracted.id
        review_service = FakeAutomationReviewService(missing_review, refreshed_review=safe_review)
        agent = FakeBringForwardAgent(
            FTWBringForwardResult(success=True, state="COMPLETED", message="Bring Forward completed")
        )
        service = FTWAutomationService(
            repo=repo,
            review_service=review_service,
            bring_forward_agent=agent,
            settings=settings,
        )

        decision = run_async(service.run(saved_filing.id, review=missing_review))

        self.assertEqual(decision.status, FTWAutomationStatus.SAFE_TO_SEND)
        self.assertEqual(len(agent.calls), 1)
        self.assertEqual(len(review_service.query_calls), 1)
        self.assertTrue(review_service.query_calls[0]["send_queries"])
        self.assertFalse(review_service.query_calls[0]["reuse_current_snapshot"])
        updated = run_async(repo.get_filing(saved_filing.id))
        self.assertIsNotNone(updated.automation_bring_forward_submitted_at)
        self.assertIsNotNone(updated.automation_bring_forward_verified_at)
        self.assertEqual(updated.automation_bring_forward_before_record_ids, [])
        self.assertEqual(updated.automation_bring_forward_new_record_ids, ["1"])

    def test_bring_forward_requery_automatically_adds_new_when_no_record_matches(self):
        filing, safe_review, extracted, settings = self.safe_case()
        settings.ftw_automation_bring_forward_enabled = True
        settings.ftw_automation_auto_send_enabled = False
        extracted.source_field_name = "Carrier name"
        extracted.normalized_field_name = "carrier_name"
        extracted.xml_tag = "InsCarrierName"
        extracted.value = extracted.proposed_value = "Highland Demo Insurance"
        extracted.source_text = "Insurance carrier: Highland Demo Insurance"

        missing_review = safe_review.model_copy(deep=True)
        missing_review.current_year_exists = False
        missing_review.bring_forward_required = True
        missing_review.schedule_a_match = None
        missing_review.schedule_a_candidates = []
        missing_review.schedule_a_records = []
        missing_review.fields = []
        self.approve_bring_forward_target(filing, missing_review)

        refreshed_review = safe_review.model_copy(deep=True)
        refreshed_review.schedule_a_match = None
        refreshed_review.schedule_a_candidates = [
            {"ftw_seq_no": "7", "score": 2, "strong_matches": 0, "carrier": "Prior Carrier"},
        ]
        refreshed_review.schedule_a_records = [
            {"ftw_seq_no": "7", "query_results": {"InsCarrierName": "Prior Carrier"}},
        ]
        refreshed_review.fields[0].label = "Carrier name"
        refreshed_review.fields[0].ftw_tag = "InsCarrierName"
        refreshed_review.fields[0].extracted_value = "Highland Demo Insurance"
        refreshed_review.fields[0].proposed_value = "Highland Demo Insurance"

        repo = MemoryRepository()
        saved_filing = run_async(repo.create_filing(filing))
        extracted.filing_id = saved_filing.id
        missing_review.filing_id = saved_filing.id
        refreshed_review.filing_id = saved_filing.id
        run_async(repo.add_fields([extracted]))
        refreshed_review.fields[0].field_id = extracted.id
        review_service = FakeAutomationReviewService(missing_review, refreshed_review=refreshed_review)
        agent = FakeBringForwardAgent(
            FTWBringForwardResult(success=True, state="SUBMITTED", message="Bring Forward submitted")
        )
        service = FTWAutomationService(
            repo=repo,
            review_service=review_service,
            bring_forward_agent=agent,
            settings=settings,
        )

        decision = run_async(service.run(saved_filing.id, review=missing_review))

        self.assertEqual(decision.status, FTWAutomationStatus.SAFE_TO_SEND)
        self.assertEqual(len(review_service.schedule_match_calls), 1)
        self.assertTrue(review_service.schedule_match_calls[0]["payload"].create_new)

    def test_submitted_bring_forward_is_not_clicked_twice_when_requery_is_delayed(self):
        filing, missing_review, extracted, settings = self.safe_case()
        settings.ftw_automation_bring_forward_enabled = True
        settings.ftw_automation_auto_send_enabled = False
        missing_review.current_year_exists = False
        missing_review.bring_forward_required = True
        missing_review.schedule_a_match = None
        missing_review.schedule_a_candidates = []
        missing_review.schedule_a_records = []
        missing_review.fields = []
        self.approve_bring_forward_target(filing, missing_review)

        repo = MemoryRepository()
        saved_filing = run_async(repo.create_filing(filing))
        extracted.filing_id = saved_filing.id
        missing_review.filing_id = saved_filing.id
        run_async(repo.add_fields([extracted]))
        review_service = FakeAutomationReviewService(missing_review, refreshed_review=missing_review)
        agent = FakeBringForwardAgent(
            FTWBringForwardResult(success=True, state="SUBMITTED", message="Bring Forward submitted")
        )
        service = FTWAutomationService(
            repo=repo,
            review_service=review_service,
            bring_forward_agent=agent,
            settings=settings,
        )

        first = run_async(service.run(saved_filing.id, review=missing_review))
        second = run_async(service.run(saved_filing.id, review=missing_review))

        self.assertEqual(first.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertEqual(first.next_action, "RETRY_AFTER_CURRENT_QUERY")
        self.assertEqual(second.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertEqual(len(agent.calls), 1)

    def test_bring_forward_worker_exception_marks_automation_failed(self):
        filing, missing_review, extracted, settings = self.safe_case()
        settings.ftw_automation_bring_forward_enabled = True
        settings.ftw_automation_auto_send_enabled = False
        missing_review.current_year_exists = False
        missing_review.bring_forward_required = True
        missing_review.schedule_a_match = None
        missing_review.schedule_a_candidates = []
        missing_review.schedule_a_records = []
        missing_review.fields = []
        self.approve_bring_forward_target(filing, missing_review)

        repo = MemoryRepository()
        saved_filing = run_async(repo.create_filing(filing))
        extracted.filing_id = saved_filing.id
        missing_review.filing_id = saved_filing.id
        run_async(repo.add_fields([extracted]))
        service = FTWAutomationService(
            repo=repo,
            review_service=FakeAutomationReviewService(missing_review),
            bring_forward_agent=RaisingBringForwardAgent(),
            settings=settings,
        )

        decision = run_async(service.run(saved_filing.id, review=missing_review))

        self.assertEqual(decision.status, FTWAutomationStatus.FAILED)
        self.assertEqual(decision.next_action, "RETRY")
        updated = run_async(repo.get_filing(saved_filing.id))
        self.assertEqual(updated.automation_status, FTWAutomationStatus.FAILED)

    def test_post_bring_forward_requery_exception_marks_automation_failed(self):
        filing, missing_review, extracted, settings = self.safe_case()
        settings.ftw_automation_bring_forward_enabled = True
        settings.ftw_automation_auto_send_enabled = False
        missing_review.current_year_exists = False
        missing_review.bring_forward_required = True
        missing_review.schedule_a_match = None
        missing_review.schedule_a_candidates = []
        missing_review.schedule_a_records = []
        missing_review.fields = []
        self.approve_bring_forward_target(filing, missing_review)

        repo = MemoryRepository()
        saved_filing = run_async(repo.create_filing(filing))
        extracted.filing_id = saved_filing.id
        missing_review.filing_id = saved_filing.id
        run_async(repo.add_fields([extracted]))
        review_service = RaisingRequeryReviewService(missing_review)
        agent = FakeBringForwardAgent(
            FTWBringForwardResult(success=True, state="SUBMITTED", message="Bring Forward submitted")
        )
        service = FTWAutomationService(
            repo=repo,
            review_service=review_service,
            bring_forward_agent=agent,
            settings=settings,
        )

        decision = run_async(service.run(saved_filing.id, review=missing_review))

        self.assertEqual(decision.status, FTWAutomationStatus.FAILED)
        self.assertEqual(decision.next_action, "RETRY_AFTER_CURRENT_QUERY")
        updated = run_async(repo.get_filing(saved_filing.id))
        self.assertEqual(updated.automation_status, FTWAutomationStatus.FAILED)
        self.assertIsNotNone(updated.automation_bring_forward_submitted_at)

    def test_browser_bring_forward_requires_a_saved_demo_login_session(self):
        _filing, review, _extracted, settings = self.safe_case()
        review.bring_forward_required = True
        review.ftw_plan_url = (
            "https://ftwilliam.com/cgi-bin/index.cgi?#go=iframe&page=/cgi-bin/PlanDoc2.cgi"
            "&PerformDoc5500=1&plan=highland-demo,001&Year=2025"
        )
        with tempfile.TemporaryDirectory() as directory:
            settings.ftw_browser_storage_state_path = str(Path(directory) / "missing-state.json")
            agent = PlaywrightFTWBringForwardAgent(settings)

            result = run_async(agent.bring_forward("filing-1", review))

        self.assertFalse(result.success)
        self.assertEqual(result.state, "LOGIN_REQUIRED")
        self.assertIn("saved FT Williams login session", result.message)

    def test_browser_worker_accepts_inline_ecs_storage_state(self):
        _filing, _review, _extracted, settings = self.safe_case()
        settings.ftw_browser_storage_state_json = '{"cookies":[],"origins":[]}'
        agent = PlaywrightFTWBringForwardAgent(settings)

        storage_state, error = agent._load_storage_state()

        self.assertEqual(storage_state, {"cookies": [], "origins": []})
        self.assertIsNone(error)

    def test_browser_worker_rejects_invalid_inline_storage_state(self):
        _filing, _review, _extracted, settings = self.safe_case()
        settings.ftw_browser_storage_state_json = "not-json"
        agent = PlaywrightFTWBringForwardAgent(settings)

        storage_state, error = agent._load_storage_state()

        self.assertIsNone(storage_state)
        self.assertIn("invalid", error.lower())

    def test_browser_worker_reuses_refreshed_runtime_session_across_agent_instances(self):
        _filing, _review, _extracted, settings = self.safe_case()
        settings.ftw_browser_storage_state_json = '{"cookies":[],"origins":[]}'
        refreshed = {
            "cookies": [{"name": "fortwilliam", "value": "rotated"}],
            "origins": [],
        }

        class Context:
            kwargs = None

            async def storage_state(self, **kwargs):
                self.kwargs = kwargs
                return refreshed

        PlaywrightFTWBringForwardAgent._runtime_storage_state = None
        context = Context()
        try:
            run_async(PlaywrightFTWBringForwardAgent(settings)._remember_storage_state(context))
            storage_state, error = PlaywrightFTWBringForwardAgent(settings)._load_storage_state()
        finally:
            PlaywrightFTWBringForwardAgent._runtime_storage_state = None

        self.assertEqual(storage_state, refreshed)
        self.assertIsNone(error)
        self.assertEqual(context.kwargs, {"indexed_db": True})

    def test_browser_worker_reuses_one_live_browser_context_across_plans(self):
        _filing, _review, _extracted, settings = self.safe_case()
        settings.ftw_browser_storage_state_json = '{"cookies":[],"origins":[]}'

        class Page:
            closed = False

            def is_closed(self):
                return self.closed

            def on(self, _event, _handler):
                return None

            async def close(self):
                self.closed = True

        class Context:
            new_page_count = 0

            async def new_page(self):
                self.new_page_count += 1
                return Page()

            async def close(self):
                return None

        class Browser:
            connected = True
            new_context_count = 0
            context = Context()

            def is_connected(self):
                return self.connected

            async def new_context(self, **_kwargs):
                self.new_context_count += 1
                return self.context

            async def close(self):
                self.connected = False

        class Chromium:
            launch_count = 0
            browser = Browser()

            async def launch(self, **_kwargs):
                self.launch_count += 1
                return self.browser

        class Playwright:
            chromium = Chromium()

            async def stop(self):
                return None

        class Starter:
            playwright = Playwright()

            async def start(self):
                return self.playwright

        def async_playwright():
            return Starter()

        async def scenario():
            agent = PlaywrightFTWBringForwardAgent(settings)
            PlaywrightFTWBringForwardAgent._runtime_page = None
            PlaywrightFTWBringForwardAgent._runtime_context = None
            PlaywrightFTWBringForwardAgent._runtime_browser = None
            PlaywrightFTWBringForwardAgent._runtime_playwright = None
            first_page, first_context = await agent._persistent_page(
                async_playwright,
                {"cookies": [], "origins": []},
            )
            second_page, second_context = await PlaywrightFTWBringForwardAgent(settings)._persistent_page(
                async_playwright,
                {"cookies": [], "origins": []},
            )
            try:
                return first_page, first_context, second_page, second_context
            finally:
                await PlaywrightFTWBringForwardAgent._close_runtime_browser()

        first_page, first_context, second_page, second_context = run_async(scenario())

        self.assertIs(first_page, second_page)
        self.assertIs(first_context, second_context)
        self.assertEqual(Playwright.chromium.launch_count, 1)
        self.assertEqual(Playwright.chromium.browser.new_context_count, 1)
        self.assertEqual(Playwright.chromium.browser.context.new_page_count, 1)

    def test_browser_worker_restores_ftw_session_storage_before_navigation(self):
        _filing, _review, _extracted, settings = self.safe_case()
        agent = PlaywrightFTWBringForwardAgent(settings)
        saved = {
            "cookies": [],
            "origins": [],
            "_erisapros_user_agent": "Mozilla/5.0 Chrome/151.0.0.0",
            "_erisapros_session_storage": {
                "https://www.ftwilliam.com": [{"name": "ftw-session", "value": "demo-token"}],
            },
        }

        browser_state, session_storage, user_agent = agent._split_storage_state(saved)

        self.assertEqual(browser_state, {"cookies": [], "origins": []})
        self.assertEqual(
            session_storage,
            {"https://www.ftwilliam.com": [{"name": "ftw-session", "value": "demo-token"}]},
        )
        self.assertEqual(user_agent, "Mozilla/5.0 Chrome/151.0.0.0")

        class Context:
            scripts: list[str] = []

            async def add_init_script(self, script):
                self.scripts.append(script)

        context = Context()
        run_async(agent._restore_session_storage(context, session_storage))

        self.assertEqual(len(context.scripts), 1)
        self.assertIn("sessionStorage.setItem", context.scripts[0])
        self.assertIn("https://www.ftwilliam.com", context.scripts[0])

    def test_browser_login_detection_recognizes_ftw_badpage_session_failure(self):
        self.assertIsNotNone(PlaywrightFTWBringForwardAgent._LOGIN_TEXT.search("badpage(error);"))

    def test_browser_bring_forward_rejects_non_ftw_target_before_opening_browser(self):
        _filing, review, _extracted, settings = self.safe_case()
        review.bring_forward_required = True
        review.ftw_plan_url = "https://example.com/highland-demo/001/2025"
        agent = PlaywrightFTWBringForwardAgent(settings)

        result = run_async(agent.bring_forward("filing-1", review))

        self.assertFalse(result.success)
        self.assertEqual(result.state, "INVALID_TARGET")
        self.assertIn("ftwilliam.com", result.message)

    def test_browser_bring_forward_recognizes_year_specific_ftw_link(self):
        self.assertIsNotNone(
            PlaywrightFTWBringForwardAgent._BRING_FORWARD_TEXT.search(
                "Bring forward 2024 data to 2025 for this plan only"
            )
        )

    def test_browser_target_uses_browser_ids_not_ftwlink_ids(self):
        _filing, review, _extracted, settings = self.safe_case()
        review.bring_forward_required = True
        review.ftw_browser_customer_id = "2429100964"
        review.ftw_browser_plan_id = "2986383641"
        review.ftw_plan_url = (
            "https://ftwilliam.com/cgi-bin/index.cgi#go=iframe&page=/cgi-bin/PlanDoc2.cgi"
            "&PerformDoc5500=1&plan=2429100964,2986383641&Year=2025"
        )

        self.assertIsNone(PlaywrightFTWBringForwardAgent(settings)._target_error(review))

    def test_browser_page_identity_requires_exact_plan_name_ein_plan_number_and_year(self):
        _filing, review, _extracted, settings = self.safe_case()
        review.bring_forward_required = True
        review.year = "2025"
        review.plan_lookup = FTWilliamsPlanLookup(
            status=FTWilliamsPlanLookupStatus.MATCHED,
            plan_name="Fgf,Llc Employee Benefits Plan test",
            company_employer_id="32-0561094",
            plan_number="501",
            year="2025",
        )
        agent = PlaywrightFTWBringForwardAgent(settings)
        correct_page = (
            "Fgf,Llc Employee Benefits Plan test\n"
            "Details: EIN: 32-0561094 • PN: 501\n"
            "5500 - 2025\n"
            "Bring forward 2024 data to 2025 for this plan only"
        )
        wrong_page = correct_page.replace("Employee Benefits Plan test", "Employee Benefits Plan")

        self.assertIsNone(agent._page_identity_error(review, correct_page))
        self.assertIn("plan name", agent._page_identity_error(review, wrong_page).lower())

    def test_browser_waits_for_delayed_ftw_plan_frame_before_rejecting_identity(self):
        _filing, review, _extracted, settings = self.safe_case()
        review.bring_forward_required = True
        review.year = "2025"
        review.plan_lookup = FTWilliamsPlanLookup(
            status=FTWilliamsPlanLookupStatus.MATCHED,
            plan_name="American Securities LLC Health And Welfare Plan",
            company_employer_id="27-1486827",
            plan_number="501",
            year="2025",
        )
        loading_page = "HighlandTech Loading..."
        verified_page = (
            "American Securities LLC Health And Welfare Plan\n"
            "Details: EIN: 27-1486827 • PN: 501\n"
            "5500 - 2025\n"
            "Bring forward 2024 data to 2025 for this plan only"
        )

        class DelayedPage:
            def __init__(self):
                self.waits = 0

            async def wait_for_timeout(self, _milliseconds):
                self.waits += 1

        page = DelayedPage()
        agent = PlaywrightFTWBringForwardAgent(settings)
        with patch.object(agent, "_login_required", return_value=False), patch.object(
            agent,
            "_page_text",
            side_effect=[loading_page, verified_page],
        ):
            page_text, identity_error, login_required = run_async(
                agent._wait_for_verified_target(page, review, timeout_ms=2_000)
            )

        self.assertEqual(page_text, verified_page)
        self.assertIsNone(identity_error)
        self.assertFalse(login_required)
        self.assertEqual(page.waits, 1)

    def test_browser_identity_reads_legacy_ftw_input_values(self):
        class Locator:
            def __init__(self, selector):
                self.selector = selector

            async def inner_text(self, **_kwargs):
                return "Details: EIN: 27-1486827 • PN: 501\n5500 - 2025"

            async def evaluate_all(self, _script):
                return ["AMERICAN SECURITIES LLC HEALTH AND WELFARE PLAN"]

        class Frame:
            def locator(self, selector):
                return Locator(selector)

        class Page:
            frames = [Frame()]

        page_text = run_async(PlaywrightFTWBringForwardAgent._page_text(Page()))

        self.assertIn("AMERICAN SECURITIES LLC HEALTH AND WELFARE PLAN", page_text)

    def test_zero_identity_candidates_are_automatically_added_as_new(self):
        filing, review, extracted, settings = self.safe_case()
        settings.ftw_automation_auto_send_enabled = False
        settings.ftwlink_schedule_a_single_record_only = False
        extracted.source_field_name = "Carrier name"
        extracted.normalized_field_name = "carrier_name"
        extracted.xml_tag = "InsCarrierName"
        extracted.value = "Highland Demo Insurance"
        extracted.proposed_value = "Highland Demo Insurance"
        extracted.source_text = "Insurance carrier: Highland Demo Insurance"
        review.fields[0].label = "Carrier name"
        review.fields[0].ftw_tag = "InsCarrierName"
        review.fields[0].extracted_value = "Highland Demo Insurance"
        review.fields[0].proposed_value = "Highland Demo Insurance"
        review.schedule_a_match = None
        review.schedule_a_candidates = [
            {"ftw_seq_no": "1", "score": 2, "strong_matches": 0, "carrier": "Unrelated Carrier"},
            {"ftw_seq_no": "2", "score": 0, "strong_matches": 0, "carrier": "Another Carrier"},
        ]
        review.schedule_a_records = [
            {"ftw_seq_no": "1", "query_results": {"InsCarrierName": "Unrelated Carrier"}},
            {"ftw_seq_no": "2", "query_results": {"InsCarrierName": "Another Carrier"}},
        ]
        repo = MemoryRepository()
        saved_filing = run_async(repo.create_filing(filing))
        extracted.filing_id = saved_filing.id
        review.filing_id = saved_filing.id
        run_async(repo.add_fields([extracted]))
        review.fields[0].field_id = extracted.id
        review_service = FakeAutomationReviewService(review)
        service = FTWAutomationService(repo=repo, review_service=review_service, settings=settings)

        decision = run_async(service.run(saved_filing.id, review=review))

        self.assertEqual(decision.status, FTWAutomationStatus.SAFE_TO_SEND)
        self.assertEqual(len(review_service.schedule_match_calls), 1)
        self.assertTrue(review_service.schedule_match_calls[0]["payload"].create_new)

    def test_partial_identity_candidate_is_never_automatically_added_as_new(self):
        filing, review, extracted, settings = self.safe_case()
        settings.ftw_automation_auto_send_enabled = False
        extracted.xml_tag = "InsCarrierName"
        extracted.value = extracted.proposed_value = "Highland Demo Insurance"
        review.schedule_a_match = None
        review.schedule_a_candidates = [
            {"ftw_seq_no": "1", "score": 7, "strong_matches": 1, "carrier": "Similar Carrier"},
        ]
        review.schedule_a_records = [
            {"ftw_seq_no": "1", "query_results": {"InsCarrierName": "Similar Carrier"}},
        ]
        repo = MemoryRepository()
        saved_filing = run_async(repo.create_filing(filing))
        extracted.filing_id = saved_filing.id
        review.filing_id = saved_filing.id
        run_async(repo.add_fields([extracted]))
        review_service = FakeAutomationReviewService(review)
        service = FTWAutomationService(repo=repo, review_service=review_service, settings=settings)

        decision = run_async(service.run(saved_filing.id, review=review))

        self.assertEqual(decision.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertEqual(review_service.schedule_match_calls, [])

    def test_completed_automation_run_does_not_send_again_from_a_stale_callback(self):
        filing, review, extracted, settings = self.safe_case()
        repo = MemoryRepository()
        saved_filing = run_async(repo.create_filing(filing))
        extracted.filing_id = saved_filing.id
        review.filing_id = saved_filing.id
        run_async(repo.add_fields([extracted]))
        review.fields[0].field_id = extracted.id
        review_service = FakeAutomationReviewService(review)
        service = FTWAutomationService(repo=repo, review_service=review_service, settings=settings)

        first = run_async(service.run(saved_filing.id, review=review))
        second = run_async(service.run(saved_filing.id, review=review))

        self.assertEqual(first.status, FTWAutomationStatus.COMPLETED)
        self.assertEqual(second.status, FTWAutomationStatus.COMPLETED)
        self.assertEqual(len(review_service.send_calls), 1)


if __name__ == "__main__":
    unittest.main()
