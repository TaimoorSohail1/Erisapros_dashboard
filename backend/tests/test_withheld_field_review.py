"""Source review holds must remain visible after extraction-to-FTW comparison."""
import unittest

from app.models import (
    DocumentType, ExtractionValidationResult, ExtractedFieldStatus, FormType,
    FTWilliamsReview, NormalizedExtractionField, ScheduleAContractType,
)
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.mapping import map_extraction_to_rules
from app.services.xml_builder import update_values_for_form


CASES = (
    ("10a. Total premiums or subscription charges paid to carrier", "173,344.57",
     "WlfrTotChargesPaidAmt", "Confirm which financial section applies to this premium."),
    ("1e. Persons Covered (End of Policy Year)", "171",
     "InsPrsnCoveredEoyCnt", "Different benefit rows report different covered counts."),
)


def withheld_field(label, value, reason):
    source = NormalizedExtractionField(
        field_name=label, value=value, confidence=.99, page=2,
        source_text=f"Source table: {label} {value}", decision="REVIEW_REQUIRED",
        validation_results=[ExtractionValidationResult(
            validator="source_interpretation", status="ERROR", reason=reason)],
    )
    mapped = map_extraction_to_rules(
        "offline-qa", [source], form_type=FormType.SCHEDULE_A,
        source_document_type=DocumentType.SCHEDULE_A,
    )
    field = next(f for f in mapped["fields"] if f.source_field_name == label)
    field.id = "offline-" + label.split(".")[0]
    return field


class WithheldFieldReviewTests(unittest.TestCase):
    def test_source_values_withheld_by_mapping_remain_actionable_review_items(self):
        service = FTWilliamsReviewService(ftwilliams=object())
        for label, value, tag, reason in CASES:
            with self.subTest(label=label):
                field = withheld_field(label, value, reason)
                self.assertEqual(field.status, ExtractedFieldStatus.LOW_CONFIDENCE)
                self.assertEqual(field.proposed_value, "")
                safe = service._safe_update_fields([field], FormType.SCHEDULE_A, {})
                row = service._comparison_fields([field], {}, {}, update_fields=safe)[0]
                self.assertEqual(row.extracted_value, value)
                self.assertEqual(row.ftw_tag, tag)
                self.assertEqual(row.decision, "BLOCKED")
                self.assertEqual(row.validation_status, "REVIEW_REQUIRED")
                self.assertTrue(row.validation_blocking)
                self.assertIn(reason, row.validation_message)
                self.assertFalse(row.update_included)
                self.assertEqual(row.proposed_value, "")
                self.assertEqual(update_values_for_form(safe, FormType.SCHEDULE_A), {})
                review = FTWilliamsReview(filing_id="offline-qa", fields=[row],
                    schedule_a_contract_type=ScheduleAContractType.NONEXPERIENCE_RATED)
                self.assertIsNotNone(service._review_validation_blocking_error(
                    review, include_brokers=False))
                self.assertEqual(service._review_decision_count_updates(review)["blocked_field_count"], 1)

    def test_existing_vendor_value_does_not_silently_resolve_a_source_hold(self):
        service = FTWilliamsReviewService(ftwilliams=object())
        for label, value, tag, reason in CASES:
            with self.subTest(label=label):
                field = withheld_field(label, value, reason)
                row = service._comparison_fields([field], {}, {tag: value}, update_fields=[])[0]
                self.assertEqual(row.decision, "BLOCKED")
                self.assertEqual(row.current_value, value)
                self.assertEqual(row.proposed_value, value)
                self.assertFalse(row.changed)
                self.assertFalse(row.update_included)
                self.assertEqual(field.proposed_value, "")

    def test_explicit_review_confirmation_allows_serialization_without_overwriting_evidence(self):
        service = FTWilliamsReviewService(ftwilliams=object())
        for label, value, tag, reason in CASES:
            with self.subTest(label=label):
                source = withheld_field(label, value, reason)
                reviewed = source.model_copy(update={
                    "status": ExtractedFieldStatus.EDITED, "proposed_value": value,
                    "status_reason": "Value and interpretation explicitly confirmed by reviewer.",
                })
                safe = service._safe_update_fields([reviewed], FormType.SCHEDULE_A, {})
                row = service._comparison_fields([reviewed], {}, {}, update_fields=safe)[0]
                self.assertEqual(row.decision, "WILL_UPDATE")
                self.assertFalse(row.validation_blocking)
                self.assertTrue(row.update_included)
                self.assertIn(tag, update_values_for_form(safe, FormType.SCHEDULE_A))
                self.assertEqual(reviewed.value, source.value)
                self.assertEqual(source.proposed_value, "")

    def test_absent_values_are_still_skipped(self):
        service = FTWilliamsReviewService(ftwilliams=object())
        field = withheld_field(*CASES[1][:2], CASES[1][3])
        absent = field.model_copy(update={"value": "", "source_text": "",
            "status": ExtractedFieldStatus.MISSING})
        row = service._comparison_fields([absent], {}, {}, update_fields=[])[0]
        self.assertEqual(row.decision, "SKIP_EMPTY")
        self.assertFalse(row.validation_blocking)

    def test_unreadable_source_stays_reviewable_and_zero_is_not_treated_as_absent(self):
        service = FTWilliamsReviewService(ftwilliams=object())
        field = withheld_field(*CASES[1][:2], CASES[1][3])
        for value in ("", "0"):
            with self.subTest(value=value):
                held = field.model_copy(update={"value": value})
                row = service._comparison_fields([held], {}, {}, update_fields=[])[0]
                self.assertEqual(row.decision, "BLOCKED")
                self.assertFalse(row.update_included)

    def test_irrelevant_contract_section_remains_not_applicable(self):
        service = FTWilliamsReviewService(ftwilliams=object())
        field = withheld_field(*CASES[0][:2], CASES[0][3])
        field = field.model_copy(update={
            "mapped_rule_key": "schedule_a_part_iii_9a_premiums_1_amount_received",
            "mapped_label": "9a(1). Amount received",
        })
        row = service._comparison_fields([field], {}, {}, update_fields=[],
            schedule_a_contract_type=ScheduleAContractType.NONEXPERIENCE_RATED)[0]
        self.assertEqual(row.decision, "NO_CHANGE")
        self.assertEqual(row.validation_status, "NOT_APPLICABLE")
        self.assertFalse(row.validation_blocking)

    def test_partial_send_of_unrelated_fields_does_not_bypass_selected_hold(self):
        service = FTWilliamsReviewService(ftwilliams=object())
        field = withheld_field(*CASES[1][:2], CASES[1][3])
        row = service._comparison_fields([field], {}, {}, update_fields=[])[0]
        review = FTWilliamsReview(filing_id="offline-qa", fields=[row],
            schedule_a_contract_type=ScheduleAContractType.NONEXPERIENCE_RATED)
        self.assertIsNone(service._review_validation_blocking_error(
            review, selected_field_ids=["unrelated-confirmed-field"], include_brokers=False))
        self.assertIsNotNone(service._review_validation_blocking_error(
            review, selected_field_ids=[field.id], include_brokers=False))


if __name__ == "__main__":
    unittest.main()
