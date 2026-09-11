import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.repositories as repositories
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
    FTWilliamsComparisonField,
    FTWilliamsPlanLookup,
    FTWilliamsPlanLookupStatus,
    FTWilliamsQueryState,
    FTWilliamsReview,
    FTWilliamsReviewStatus,
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
            ftwlink_sandbox_ftw_customer_id="highland-demo",
            ftwlink_sandbox_ftw_plan_id="001",
            ftwlink_sandbox_year_end="2025",
        )
        return filing, review, extracted, settings

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

    def test_low_confidence_required_field_stops_automatic_send(self):
        filing, review, extracted, settings = self.safe_case()
        extracted.confidence = 0.90
        review.fields[0].confidence = 0.90

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertFalse(decision.eligible)
        self.assertTrue(any("95%" in reason for reason in decision.reasons))

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

    def test_filing_outside_demo_allowlist_never_sends(self):
        filing, review, extracted, settings = self.safe_case()
        review.ftw_customer_id = "production-customer"

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertFalse(decision.eligible)
        self.assertIn("outside", decision.reasons[0].lower())

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

    def test_invalid_json_allowlist_fails_closed(self):
        filing, review, extracted, settings = self.safe_case()
        settings.ftwlink_sandbox_ftw_customer_id = None
        settings.ftwlink_sandbox_ftw_plan_id = None
        settings.ftw_automation_allowed_targets_json = "not-json"

        decision = FTWAutomationPolicy(settings).evaluate(filing, review, [extracted])

        self.assertEqual(decision.status, FTWAutomationStatus.ACTION_NEEDED)
        self.assertIn("allowlist", decision.reasons[0].lower())

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

    def test_zero_identity_candidates_are_automatically_added_as_new(self):
        filing, review, extracted, settings = self.safe_case()
        settings.ftw_automation_auto_send_enabled = False
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
