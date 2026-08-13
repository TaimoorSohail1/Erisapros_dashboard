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
    apply_schedule_a_classification,
    classification_signals_from_text,
    classify_schedule_a_current,
    classify_schedule_a_fields,
    filter_schedule_a_fields_for_contract_type,
    schedule_a_contract_type_allows_rule,
)


LINE_9A = "schedule_a_part_iii_9a_premiums_1_amount_received"
LINE_9C = "schedule_a_part_iii_9c_1_a_commissions"
LINE_9_REMAINDER_RULES = (
    "schedule_a_part_iii_9c_2_dividends_or_retroactive_rate_refunds",
    "schedule_a_part_iii_9d_1_status_of_policyholder_reserves_at_end_of_year_1_amount_held_to_provide_benefits_after_retirement",
    "schedule_a_part_iii_9d_2_claim_reserves",
    "schedule_a_part_iii_9d_3_other_reserves",
    "schedule_a_part_iii_9e_dividends_or_retroactive_rate_refunds_due",
)
LINE_10A = "schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier"
LINE_9A4 = "schedule_a_part_iii_9a_4_earned_1_2_3"
LINE_9B3 = "schedule_a_part_iii_9b_3_incurred_claims_add_1_and_2"
LINE_9C1H = "schedule_a_part_iii_9c_1_h_total_retention"


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

    def test_explicit_experience_rated_wording_is_used_when_line_mapping_is_missing(self):
        classification = classify_schedule_a_fields([], ["EXPLICIT_EXPERIENCE_RATED"])

        self.assertEqual(classification.contract_type, ScheduleAContractType.EXPERIENCE_RATED)
        self.assertIn("explicitly labels", classification.reason.lower())

    def test_document_wording_is_normalized_into_classification_signals(self):
        self.assertEqual(
            classification_signals_from_text("Premium basis: NON-EXPERIENCE RATED"),
            ["EXPLICIT_NONEXPERIENCE_RATED"],
        )

    def test_a_line_9_value_without_9a_or_premium_and_claim_evidence_defaults_to_nonexperience(self):
        classification = classify_schedule_a_fields([
            extracted_field("schedule_a_part_iii_9d_2_claim_reserves", "109724")
        ])

        self.assertEqual(classification.contract_type, ScheduleAContractType.NONEXPERIENCE_RATED)

    def test_line_10a_only_is_nonexperience_rated(self):
        classification = classify_schedule_a_fields([extracted_field(LINE_10A, "170074")])

        self.assertEqual(classification.contract_type, ScheduleAContractType.NONEXPERIENCE_RATED)

    def test_premiums_and_claims_are_experience_rated_even_when_premium_was_mapped_to_10a(self):
        classification = classify_schedule_a_fields([
            extracted_field(LINE_10A, "170074"),
            extracted_field("schedule_a_part_iii_9b_1_benefit_charges_1_claims_paid", "109724"),
        ])

        self.assertEqual(classification.contract_type, ScheduleAContractType.EXPERIENCE_RATED)

    def test_experience_rating_automatically_sets_10a_to_zero(self):
        line_9a = extracted_field(LINE_9A, "170074")
        line_10a = extracted_field(LINE_10A, "170074")

        classification = apply_schedule_a_classification([line_9a, line_10a])

        self.assertEqual(classification.contract_type, ScheduleAContractType.EXPERIENCE_RATED)
        self.assertEqual(line_10a.proposed_value, "0")
        self.assertIn("automatically derived", (line_10a.status_reason or "").lower())

    def test_nonexperience_rating_automatically_sets_required_line_9_totals_to_zero(self):
        line_10a = extracted_field(LINE_10A, "170074")
        derived_fields = [extracted_field(rule_key, "") for rule_key in (LINE_9A4, LINE_9B3, LINE_9C1H)]
        for field in derived_fields:
            field.status = ExtractedFieldStatus.MISSING

        classification = apply_schedule_a_classification([line_10a, *derived_fields])

        self.assertEqual(classification.contract_type, ScheduleAContractType.NONEXPERIENCE_RATED)
        self.assertEqual([field.proposed_value for field in derived_fields], ["0", "0", "0"])
        self.assertTrue(all(field.status == ExtractedFieldStatus.MATCHED for field in derived_fields))

    def test_automatic_zero_fields_remain_in_the_selected_ftw_field_set(self):
        experience_fields = [extracted_field(LINE_9A, "170074"), extracted_field(LINE_10A, "170074")]
        experience = apply_schedule_a_classification(experience_fields)
        experience_relevant = filter_schedule_a_fields_for_contract_type(experience_fields, experience.contract_type)

        nonexperience_fields = [
            extracted_field(LINE_10A, "170074"),
            *[extracted_field(rule_key, "") for rule_key in (LINE_9A4, LINE_9B3, LINE_9C1H)],
        ]
        nonexperience = apply_schedule_a_classification(nonexperience_fields)
        nonexperience_relevant = filter_schedule_a_fields_for_contract_type(nonexperience_fields, nonexperience.contract_type)

        self.assertIn(next(field for field in experience_fields if field.mapped_rule_key == LINE_10A), experience_relevant)
        self.assertTrue(all(field in nonexperience_relevant for field in nonexperience_fields))

    def test_reclassification_restores_the_original_premium_before_applying_new_rules(self):
        line_9a = extracted_field(LINE_9A, "170074")
        line_10a = extracted_field(LINE_10A, "170074")
        apply_schedule_a_classification([line_9a, line_10a])
        line_9a.value = ""
        line_9a.proposed_value = ""
        line_9a.status = ExtractedFieldStatus.MISSING

        classification = apply_schedule_a_classification([line_9a, line_10a])

        self.assertEqual(classification.contract_type, ScheduleAContractType.NONEXPERIENCE_RATED)
        self.assertEqual(line_10a.proposed_value, "170074")

    def test_unmapped_premium_amount_is_promoted_to_10a_for_nonexperience_rating(self):
        premium = ExtractedField(
            filing_id="filing-id",
            source_field_name="Total Premiums Paid",
            normalized_field_name="total premiums paid",
            value="45230.10",
            proposed_value="45230.10",
            form_type=FormType.SCHEDULE_A,
            status=ExtractedFieldStatus.UNMAPPED,
        )
        line_10a = extracted_field(LINE_10A, "")
        line_10a.status = ExtractedFieldStatus.MISSING

        classification = apply_schedule_a_classification([premium, line_10a])

        self.assertEqual(classification.contract_type, ScheduleAContractType.NONEXPERIENCE_RATED)
        self.assertEqual(line_10a.proposed_value, "45230.10")
        self.assertIn("premium evidence", (line_10a.status_reason or "").lower())

    def test_line_9a_takes_priority_when_line_10a_was_also_extracted(self):
        classification = classify_schedule_a_fields([
            extracted_field(LINE_9A, "17007.41"),
            extracted_field(LINE_10A, "170074"),
        ])

        self.assertEqual(classification.contract_type, ScheduleAContractType.EXPERIENCE_RATED)

    def test_zero_defaults_do_not_choose_a_contract_type(self):
        classification = classify_schedule_a_fields([
            extracted_field(LINE_9A, "0"),
            extracted_field(LINE_10A, "$0.00"),
        ])

        self.assertEqual(classification.contract_type, ScheduleAContractType.NONEXPERIENCE_RATED)
        self.assertEqual(classification.confidence, 0.6)
        self.assertIn("default rule", classification.reason.lower())

    def test_ftw_current_data_uses_same_classification_rules(self):
        extracted = classify_schedule_a_fields([extracted_field(LINE_9A, "17007.41")])
        current = classify_schedule_a_current({"WlfrTotChargesPaidAmt": "170074"})

        self.assertEqual(extracted.contract_type, ScheduleAContractType.EXPERIENCE_RATED)
        self.assertEqual(current.contract_type, ScheduleAContractType.NONEXPERIENCE_RATED)

    def test_ftw_current_without_9a_or_premium_and_claim_evidence_defaults_to_nonexperience(self):
        current = classify_schedule_a_current({"WlfrClaimsReserveAmt": "109724"})

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

    def test_nonexperience_filters_every_remaining_experience_line_9_field(self):
        line_9_fields = [extracted_field(rule_key) for rule_key in LINE_9_REMAINDER_RULES]

        self.assertEqual(
            filter_schedule_a_fields_for_contract_type(
                line_9_fields,
                ScheduleAContractType.NONEXPERIENCE_RATED,
            ),
            [],
        )

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

    def test_automatic_evidence_remains_authoritative_over_the_legacy_manual_endpoint(self):
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
            persisted_field_updates = []
            original_update_field = repo.update_field

            async def track_update_field(*args, **kwargs):
                persisted_field_updates.append((args, kwargs))
                return await original_update_field(*args, **kwargs)

            repo.update_field = track_update_field
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
                FTWilliamsScheduleAContractTypeRequest(contract_type=ScheduleAContractType.NONEXPERIENCE_RATED),
            )
            updated_filing = await repo.get_filing(filing.id)
            stored_fields = await repo.list_fields(filing.id)
            return updated_filing, review, stored_fields, persisted_field_updates

        filing, review, stored_fields, persisted_field_updates = run_async(scenario())

        self.assertEqual(filing.schedule_a_contract_type, ScheduleAContractType.EXPERIENCE_RATED)
        self.assertTrue(filing.schedule_a_contract_type_confirmed)
        self.assertEqual(filing.review_field_count, 2)
        self.assertEqual(filing.found_field_count, 2)
        self.assertEqual(filing.excluded_field_count, 0)
        self.assertFalse(review.schedule_a_contract_type_mismatch)
        by_rule = {field.rule_key: field for field in review.fields}
        self.assertTrue(by_rule[LINE_9A].update_included)
        self.assertTrue(by_rule[LINE_10A].update_included)
        self.assertEqual(by_rule[LINE_10A].proposed_value, "0")
        stored_by_rule = {field.mapped_rule_key: field for field in stored_fields}
        self.assertEqual(stored_by_rule[LINE_10A].proposed_value, "0")
        self.assertTrue(
            any(
                args[1] == stored_by_rule[LINE_10A].id
                and args[2] == "0"
                and kwargs.get("status_reason", "").startswith("Automatically derived")
                for args, kwargs in persisted_field_updates
            )
        )

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
