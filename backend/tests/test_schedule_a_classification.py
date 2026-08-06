import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.repositories as repositories
from app.models import (
    DocumentType,
    ExtractedField,
    ExtractedFieldStatus,
    FieldPriority,
    Filing,
    FilingStatus,
    FormType,
    FTWilliamsComparisonField,
    FTWilliamsReview,
    FTWilliamsScheduleAContractTypeRequest,
    ScheduleAContractType,
)
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.filing_pipeline import summarize_mapped_fields
from app.services.schedule_a_classification import (
    classify_schedule_a_current,
    classify_schedule_a_fields,
    filter_schedule_a_fields_for_contract_type,
    schedule_a_contract_type_allows_rule,
)


LINE_9A = "schedule_a_part_iii_9a_premiums_1_amount_received"
LINE_9C = "schedule_a_part_iii_9c_1_a_commissions"
LINE_10A = "schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier"


def run_async(coro):
    return asyncio.run(coro)


def extracted_field(rule_key: str, value: str = "100") -> ExtractedField:
    return ExtractedField(
        filing_id="filing-id",
        source_field_name=rule_key,
        normalized_field_name=rule_key,
        mapped_rule_key=rule_key,
        mapped_label=rule_key,
        form_type=FormType.SCHEDULE_A,
        source_document_type=DocumentType.SCHEDULE_A,
        value=value,
        proposed_value=value,
    )


class ScheduleAClassificationTests(unittest.TestCase):
    def setUp(self):
        repositories._repository = repositories.MemoryRepository()

    def tearDown(self):
        repositories._repository = None

    def test_line_9_values_are_experience_rated(self):
        classification = classify_schedule_a_fields([extracted_field(LINE_9A, "17007.41")])

        self.assertEqual(classification.contract_type, ScheduleAContractType.EXPERIENCE_RATED)

    def test_line_10a_only_is_nonexperience_rated(self):
        classification = classify_schedule_a_fields([extracted_field(LINE_10A, "170074")])

        self.assertEqual(classification.contract_type, ScheduleAContractType.NONEXPERIENCE_RATED)

    def test_line_9_and_10a_conflict_needs_review(self):
        classification = classify_schedule_a_fields([
            extracted_field(LINE_9A, "17007.41"),
            extracted_field(LINE_10A, "170074"),
        ])

        self.assertEqual(classification.contract_type, ScheduleAContractType.NEEDS_REVIEW)

    def test_zero_defaults_do_not_choose_a_contract_type(self):
        classification = classify_schedule_a_fields([
            extracted_field(LINE_9A, "0"),
            extracted_field(LINE_10A, "$0.00"),
        ])

        self.assertEqual(classification.contract_type, ScheduleAContractType.UNKNOWN)

    def test_ftw_current_data_uses_same_classification_rules(self):
        extracted = classify_schedule_a_fields([extracted_field(LINE_9A, "17007.41")])
        current = classify_schedule_a_current({"WlfrTotChargesPaidAmt": "170074"})

        self.assertEqual(extracted.contract_type, ScheduleAContractType.EXPERIENCE_RATED)
        self.assertEqual(current.contract_type, ScheduleAContractType.NONEXPERIENCE_RATED)

    def test_contract_type_filters_opposite_line_group_from_send_fields(self):
        line_9 = extracted_field(LINE_9C, "2500")
        line_10 = extracted_field(LINE_10A, "170074")

        self.assertEqual(
            filter_schedule_a_fields_for_contract_type([line_9, line_10], ScheduleAContractType.EXPERIENCE_RATED),
            [line_9],
        )
        self.assertEqual(
            filter_schedule_a_fields_for_contract_type([line_9, line_10], ScheduleAContractType.NONEXPERIENCE_RATED),
            [line_10],
        )
        self.assertFalse(schedule_a_contract_type_allows_rule(ScheduleAContractType.NEEDS_REVIEW, LINE_9A))
        self.assertFalse(schedule_a_contract_type_allows_rule(ScheduleAContractType.NEEDS_REVIEW, LINE_10A))

    def test_nonexperience_counts_exclude_missing_experience_fields(self):
        line_10 = extracted_field(LINE_10A, "170074")
        missing_line_9 = extracted_field(LINE_9A, "")
        missing_line_9.status = ExtractedFieldStatus.MISSING
        missing_line_9.priority = FieldPriority.HIGH

        relevant = filter_schedule_a_fields_for_contract_type(
            [line_10, missing_line_9],
            ScheduleAContractType.NONEXPERIENCE_RATED,
        )
        summary = summarize_mapped_fields(relevant)

        self.assertEqual(relevant, [line_10])
        self.assertEqual(summary["missing_high_priority_count"], 0)
        self.assertEqual(summary["review_field_count"], 1)
        self.assertEqual(summary["found_field_count"], 1)
        self.assertEqual(summary["status"], FilingStatus.READY_FOR_APPROVAL)

    def test_manual_override_confirms_type_and_removes_opposite_fields(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/schedule-a",
                    intake_source="SHAREFILE",
                )
            )
            line_9 = extracted_field(LINE_9A, "100")
            line_9.filing_id = filing.id
            line_10 = extracted_field(LINE_10A, "200")
            line_10.filing_id = filing.id
            await repo.add_fields([line_9, line_10])
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    ftw_schedule_a_contract_type=ScheduleAContractType.EXPERIENCE_RATED,
                    schedule_a_contract_type=ScheduleAContractType.NEEDS_REVIEW,
                    schedule_a_contract_type_mismatch=True,
                    fields=[
                        FTWilliamsComparisonField(
                            rule_key=LINE_9A,
                            label="9a. Premiums",
                            form_type=FormType.SCHEDULE_A,
                            proposed_value="100",
                            changed=True,
                            update_included=True,
                        ),
                        FTWilliamsComparisonField(
                            rule_key=LINE_10A,
                            label="10a. Premiums",
                            form_type=FormType.SCHEDULE_A,
                            proposed_value="200",
                            changed=True,
                            update_included=True,
                        ),
                    ],
                )
            )

            review = await FTWilliamsReviewService().set_schedule_a_contract_type(
                filing.id,
                FTWilliamsScheduleAContractTypeRequest(contract_type=ScheduleAContractType.EXPERIENCE_RATED),
            )
            updated_filing = await repo.get_filing(filing.id)
            return updated_filing, review

        filing, review = run_async(scenario())

        self.assertEqual(filing.schedule_a_contract_type, ScheduleAContractType.EXPERIENCE_RATED)
        self.assertTrue(filing.schedule_a_contract_type_confirmed)
        self.assertEqual(filing.review_field_count, 1)
        self.assertEqual(filing.found_field_count, 1)
        self.assertEqual(filing.excluded_field_count, 1)
        self.assertFalse(review.schedule_a_contract_type_mismatch)
        by_rule = {field.rule_key: field for field in review.fields}
        self.assertTrue(by_rule[LINE_9A].update_included)
        self.assertFalse(by_rule[LINE_10A].update_included)
        self.assertFalse(by_rule[LINE_10A].changed)

    def test_approval_ignores_irrelevant_missing_line_group_after_confirmation(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Nonexperience Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/nonexperience",
                    intake_source="SHAREFILE",
                )
            )
            line_10 = extracted_field(LINE_10A, "170074")
            line_10.filing_id = filing.id
            await repo.add_fields(
                [
                    line_10,
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name=LINE_9A,
                        normalized_field_name=LINE_9A,
                        mapped_rule_key=LINE_9A,
                        mapped_label=LINE_9A,
                        form_type=FormType.SCHEDULE_A,
                        status=ExtractedFieldStatus.MISSING,
                        priority=FieldPriority.HIGH,
                    ),
                ]
            )
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    schedule_a_contract_type=ScheduleAContractType.NONEXPERIENCE_RATED,
                    schedule_a_contract_type_confirmed=True,
                )
            )

            await FTWilliamsReviewService().approve_and_update(filing.id, override_blockers=False)
            return await repo.get_filing(filing.id)

        filing = run_async(scenario())

        self.assertEqual(filing.status, FilingStatus.APPROVED)


if __name__ == "__main__":
    unittest.main()
