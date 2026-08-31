import asyncio
import unittest
from unittest.mock import patch

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.repositories as repositories
from app.models import DocumentType, ExtractedField, ExtractedFieldStatus, FieldPriority, FormType, FTWilliamsReview
from app.services.filing_pipeline import (
    auto_query_ftw_current,
    build_safe_proposed_ftw_xml,
    harmonize_schedule_a_business_rule_fields,
    harmonize_schedule_a_reference_fields,
    process_extraction_batch,
)


def run_async(coro):
    return asyncio.run(coro)


class FakeReviewService:
    def __init__(self, review: FTWilliamsReview | None = None, error: Exception | None = None):
        self.review = review
        self.error = error
        self.calls: list[tuple[str, bool]] = []

    async def prepare_review(self, filing_id: str, send_queries: bool = False):
        self.calls.append((filing_id, send_queries))
        if self.error:
            raise self.error
        return self.review


class FilingPipelineTests(unittest.TestCase):
    def setUp(self):
        repositories._repository = repositories.MemoryRepository()

    def tearDown(self):
        repositories._repository = None

    def test_missing_schedule_a_reference_fields_are_copied_from_worksheet_fields(self):
        schedule_field = ExtractedField(
            filing_id="filing-1",
            source_field_name="4a. Plan Name",
            normalized_field_name="4a plan name",
            mapped_rule_key="schedule_a_part_iv_4a_plan_name",
            mapped_label="4a. Plan Name",
            ftw_field="4a. Plan Name",
            priority=FieldPriority.HIGH,
            value="",
            proposed_value="",
            confidence=0.6,
            source_document_type=DocumentType.SCHEDULE_A,
            form_type=FormType.SCHEDULE_A,
            status=ExtractedFieldStatus.MISSING,
        )
        worksheet_field = ExtractedField(
            filing_id="filing-1",
            source_field_name="1a. Plan Name",
            normalized_field_name="1a plan name",
            mapped_rule_key="form_5500_part_i_1a_plan_name",
            mapped_label="1a. Plan Name",
            ftw_field="1a. Plan Name",
            priority=FieldPriority.HIGH,
            value="MIDWEST HOSE AND SPECIALTY HEALTH AND WELFARE BENEFITS PLAN",
            proposed_value="MIDWEST HOSE AND SPECIALTY HEALTH AND WELFARE BENEFITS PLAN",
            confidence=0.95,
            source_document_type=DocumentType.PLAN_WORKSHEET,
            form_type=FormType.FORM_5500,
            status=ExtractedFieldStatus.MATCHED,
        )

        fields = harmonize_schedule_a_reference_fields([schedule_field, worksheet_field])
        updated = next(field for field in fields if field.mapped_rule_key == "schedule_a_part_iv_4a_plan_name")

        self.assertEqual(updated.proposed_value, worksheet_field.proposed_value)
        self.assertEqual(updated.source_document_type, DocumentType.PLAN_WORKSHEET)
        self.assertEqual(updated.form_type, FormType.SCHEDULE_A)
        self.assertEqual(updated.status, ExtractedFieldStatus.MATCHED)

    def test_conflicting_schedule_a_and_worksheet_identity_values_are_sent_to_review(self):
        schedule_field = ExtractedField(
            filing_id="filing-1",
            source_field_name="4c. Sponsor EIN",
            normalized_field_name="4c sponsor ein",
            mapped_rule_key="schedule_a_part_iv_4c_sponsor_ein",
            mapped_label="4c. Sponsor EIN",
            ftw_field="4c. Sponsor EIN",
            priority=FieldPriority.HIGH,
            value="12-3456789",
            proposed_value="12-3456789",
            confidence=0.98,
            page=2,
            source_text="Sponsor EIN 12-3456789",
            source_document_type=DocumentType.SCHEDULE_A,
            form_type=FormType.SCHEDULE_A,
            status=ExtractedFieldStatus.MATCHED,
        )
        worksheet_field = ExtractedField(
            filing_id="filing-1",
            source_field_name="1e. Plan Sponsor EIN",
            normalized_field_name="1e plan sponsor ein",
            mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
            mapped_label="1e. Plan Sponsor EIN",
            ftw_field="1e. Plan Sponsor EIN",
            priority=FieldPriority.HIGH,
            value="98-7654321",
            proposed_value="98-7654321",
            confidence=0.99,
            page=1,
            source_text="Plan Sponsor EIN 98-7654321",
            source_document_type=DocumentType.PLAN_WORKSHEET,
            form_type=FormType.FORM_5500,
            status=ExtractedFieldStatus.MATCHED,
        )

        fields = harmonize_schedule_a_reference_fields([schedule_field, worksheet_field])

        self.assertEqual(schedule_field.proposed_value, "12-3456789")
        self.assertEqual(schedule_field.status, ExtractedFieldStatus.LOW_CONFIDENCE)
        self.assertEqual(worksheet_field.status, ExtractedFieldStatus.LOW_CONFIDENCE)
        self.assertIn("conflicts", schedule_field.status_reason.lower())
        self.assertEqual(schedule_field.page, 2)
        self.assertEqual(fields, [schedule_field, worksheet_field])

    def test_invalid_xml_preview_routes_field_to_review_without_failing_extraction(self):
        field = ExtractedField(
            filing_id="filing-1",
            source_field_name="1a. Name of Insurance Company",
            normalized_field_name="1a name of insurance company",
            mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
            mapped_label="1a. Name of Insurance Company",
            ftw_field="1a. Name of Insurance Company",
            xml_tag="InsCarrierName",
            priority=FieldPriority.HIGH,
            value='Cigna Health and Life Insurance Company and affiliates ("Cigna")',
            proposed_value='Cigna Health and Life Insurance Company and affiliates ("Cigna")',
            confidence=0.95,
            source_document_type=DocumentType.SCHEDULE_A,
            form_type=FormType.SCHEDULE_A,
            status=ExtractedFieldStatus.MATCHED,
        )

        proposed_xml, issues = build_safe_proposed_ftw_xml([field])

        self.assertIsNone(proposed_xml)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].tag, "InsCarrierName")
        self.assertEqual(field.status, ExtractedFieldStatus.LOW_CONFIDENCE)
        self.assertIn("FT Williams pre-send validation", field.status_reason)

    def test_schedule_a_purpose_is_derived_from_commissions_and_fees(self):
        purpose_field = ExtractedField(
            filing_id="filing-1",
            source_field_name="3d. Purpose",
            normalized_field_name="3d purpose",
            mapped_rule_key="schedule_a_part_i_3d_purpose",
            mapped_label="3d. Purpose",
            ftw_field="3d. Purpose",
            priority=FieldPriority.HIGH,
            value="N/A",
            proposed_value="N/A",
            confidence=0.7,
            source_document_type=DocumentType.SCHEDULE_A,
            form_type=FormType.SCHEDULE_A,
            status=ExtractedFieldStatus.MATCHED,
        )
        commissions_field = ExtractedField(
            filing_id="filing-1",
            source_field_name="3b. Amount of Commissions",
            normalized_field_name="3b amount of commissions",
            mapped_rule_key="schedule_a_part_i_3b_amount_of_commissions",
            mapped_label="3b. Amount of Commissions",
            ftw_field="3b. Amount of Commissions",
            priority=FieldPriority.HIGH,
            value="$111,892.96",
            proposed_value="$111,892.96",
            confidence=0.95,
            source_document_type=DocumentType.SCHEDULE_A,
            form_type=FormType.SCHEDULE_A,
            status=ExtractedFieldStatus.MATCHED,
            page=2,
            source_text="Commissions paid $111,892.96",
        )
        fees_field = ExtractedField(
            filing_id="filing-1",
            source_field_name="3c. Amount of Fees",
            normalized_field_name="3c amount of fees",
            mapped_rule_key="schedule_a_part_i_3c_amount_of_fees",
            mapped_label="3c. Amount of Fees",
            ftw_field="3c. Amount of Fees",
            priority=FieldPriority.HIGH,
            value="$0.00",
            proposed_value="$0.00",
            confidence=0.9,
            source_document_type=DocumentType.SCHEDULE_A,
            form_type=FormType.SCHEDULE_A,
            status=ExtractedFieldStatus.MATCHED,
            page=2,
            source_text="Fees paid $0.00",
        )

        fields = harmonize_schedule_a_business_rule_fields([purpose_field, commissions_field, fees_field])
        updated = next(field for field in fields if field.mapped_rule_key == "schedule_a_part_i_3d_purpose")

        self.assertEqual(updated.proposed_value, "COMMISSIONS")
        self.assertEqual(updated.status, ExtractedFieldStatus.MATCHED)
        self.assertEqual(updated.page, 2)
        self.assertIn("Commissions paid", updated.source_text)

    def test_schedule_a_purpose_from_uncertain_inputs_stays_in_review(self):
        purpose_field = ExtractedField(
            filing_id="filing-1",
            source_field_name="3d. Purpose",
            normalized_field_name="3d purpose",
            mapped_rule_key="schedule_a_part_i_3d_purpose",
            proposed_value="N/A",
            confidence=0.7,
            form_type=FormType.SCHEDULE_A,
            status=ExtractedFieldStatus.LOW_CONFIDENCE,
        )
        commissions_field = ExtractedField(
            filing_id="filing-1",
            source_field_name="3b. Amount of Commissions",
            normalized_field_name="3b amount of commissions",
            mapped_rule_key="schedule_a_part_i_3b_amount_of_commissions",
            proposed_value="100",
            confidence=0.5,
            form_type=FormType.SCHEDULE_A,
            status=ExtractedFieldStatus.LOW_CONFIDENCE,
        )
        fees_field = ExtractedField(
            filing_id="filing-1",
            source_field_name="3c. Amount of Fees",
            normalized_field_name="3c amount of fees",
            mapped_rule_key="schedule_a_part_i_3c_amount_of_fees",
            proposed_value="0",
            confidence=0.5,
            form_type=FormType.SCHEDULE_A,
            status=ExtractedFieldStatus.LOW_CONFIDENCE,
        )

        harmonize_schedule_a_business_rule_fields([purpose_field, commissions_field, fees_field])

        self.assertEqual(purpose_field.proposed_value, "COMMISSIONS")
        self.assertEqual(purpose_field.status, ExtractedFieldStatus.LOW_CONFIDENCE)
        self.assertIn("needs Review", purpose_field.status_reason)

    def test_auto_query_ftw_current_uses_live_queries_and_audits_success(self):
        review = FTWilliamsReview(
            filing_id="filing-1",
            current_query_success=True,
            comparison_year="2025",
            comparison_year_source="filing",
        )
        service = FakeReviewService(review=review)

        run_async(auto_query_ftw_current("filing-1", service))

        repo = repositories.get_repository()
        events = [audit.event for audit in repo.audit]
        self.assertEqual(service.calls, [("filing-1", True)])
        self.assertIn("FTWILLIAMS_CURRENT_AUTO_QUERY_STARTED", events)
        self.assertIn("FTWILLIAMS_CURRENT_AUTO_QUERY_SUCCEEDED", events)
        self.assertNotIn("FTWILLIAMS_CURRENT_AUTO_QUERY_FAILED", events)

    def test_auto_query_ftw_current_audits_failed_query_without_raising(self):
        review = FTWilliamsReview(
            filing_id="filing-1",
            current_query_success=False,
            error_message="FT Williams plan match was not found.",
        )
        service = FakeReviewService(review=review)

        run_async(auto_query_ftw_current("filing-1", service))

        repo = repositories.get_repository()
        failed = next(audit for audit in repo.audit if audit.event == "FTWILLIAMS_CURRENT_AUTO_QUERY_FAILED")
        self.assertEqual(failed.details["error"], "FT Williams plan match was not found.")

    def test_auto_query_ftw_current_audits_exception_without_raising(self):
        service = FakeReviewService(error=RuntimeError("FT Williams timeout"))

        run_async(auto_query_ftw_current("filing-1", service))

        repo = repositories.get_repository()
        failed = next(audit for audit in repo.audit if audit.event == "FTWILLIAMS_CURRENT_AUTO_QUERY_FAILED")
        self.assertEqual(failed.details["error"], "FT Williams timeout")

    def test_bulk_extraction_processes_at_most_four_packages_at_once(self):
        active = 0
        max_active = 0
        started = []

        async def fake_processor(filing_id, job_id, documents):
            nonlocal active, max_active
            started.append(filing_id)
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(0.02)
            finally:
                active -= 1

        batch = [(f"filing-{index}", f"job-{index}", []) for index in range(10)]
        with patch("app.services.filing_pipeline.process_package_extraction_job", side_effect=fake_processor):
            run_async(process_extraction_batch(batch))

        self.assertEqual(len(started), 10)
        self.assertEqual(max_active, 4)


if __name__ == "__main__":
    unittest.main()
