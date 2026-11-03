"""Review-only regressions: source holds stay visible until explicitly resolved."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.repositories as repositories
from app.models import (
    ExtractionValidationResult, Filing, FormType, FTWilliamsReview,
    FTWilliamsScheduleABrokerRowsRequest, ScheduleABrokerMatch,
    ScheduleABrokerRow, SourceEvidence,
)
from app.services.ftwilliams_review import FTWilliamsReviewService


def broker(**changes):
    values = dict(
        name="TEST BROKER LLC", address_line_1="12421 TEST DR", city="URBANDALE",
        state="IA", zip_code="50398", organization_code="3",
        commission_total="21,871.85", confidence=0.5, source_page=3,
        decision="REVIEW_REQUIRED",
        evidence=[SourceEvidence(provider="source parser", page=3, source_text="ZIP 50398-900")],
        validation_results=[ExtractionValidationResult(
            validator="source_zip_shape", status="ERROR", reason="Printed ZIP is incomplete.")],
    )
    values.update(changes)
    return ScheduleABrokerRow(**values)


class BrokerReviewHoldTests(unittest.TestCase):
    def setUp(self):
        self.previous_repo = repositories._repository
        repositories._repository = repositories.MemoryRepository()

    def tearDown(self):
        repositories._repository = self.previous_repo

    def save(self, original, submitted, *, edited_index=0, action="edited"):
        async def run():
            repo = repositories.get_repository()
            filing = await repo.create_filing(Filing(
                file_name="test.pdf", content_type="application/pdf", file_size=1,
                s3_key="test/source", schedule_a_broker_rows=original,
            ))
            service = FTWilliamsReviewService()
            with patch.object(service, "prepare_review", AsyncMock(return_value=FTWilliamsReview(filing_id=filing.id))) as prepare:
                await service.update_schedule_a_broker_rows(filing.id, FTWilliamsScheduleABrokerRowsRequest(
                    rows=submitted, edited_index=edited_index, action=action,
                ))
                prepare.assert_awaited_once_with(filing.id, send_queries=False)
            audit = repo.audit[-1]
            self.assertEqual(audit.event, "FTWILLIAMS_SCHEDULE_A_BROKER_ROWS_UPDATED")
            self.assertEqual(audit.details["edited_index"], edited_index)
            return await repo.get_filing(filing.id)
        return asyncio.run(run())

    def test_explicit_valid_zip_save_clears_only_zip_hold_and_retains_source(self):
        original = broker()
        untouched = broker(name="SECOND TEST BROKER")
        stored = self.save([original, untouched], [original.model_copy(deep=True), untouched.model_copy(update={"validation_results": [], "decision": "AUTOMATIC"})])
        row = stored.schedule_a_broker_rows[0]
        self.assertFalse(any(v.status == "ERROR" for v in row.validation_results))
        self.assertNotEqual(row.decision, "REVIEW_REQUIRED")
        self.assertNotEqual(row.decision, "AUTOMATIC")
        self.assertEqual(row.evidence, original.evidence)
        self.assertEqual(row.confidence, original.confidence)
        self.assertEqual(row.commission_total, original.commission_total)
        self.assertEqual(row.zip_code, "50398")
        self.assertEqual(stored.schedule_a_broker_rows[1], untouched)
        self.assertTrue(any(v.validator == "reviewer_zip_confirmation" and v.status == "PASS" for v in row.validation_results))

    def test_missing_client_metadata_cannot_clear_another_source_hold(self):
        original = broker()
        original.validation_results.append(ExtractionValidationResult(
            validator="payment_classification_ambiguity", status="ERROR", reason="Commission or fee unresolved."))
        submitted = original.model_copy(update={"validation_results": [], "decision": "AUTOMATIC", "evidence": []})
        stored = self.save([original], [submitted])
        row = stored.schedule_a_broker_rows[0]
        self.assertTrue(any(v.validator == "payment_classification_ambiguity" and v.status == "ERROR" for v in row.validation_results))
        self.assertEqual(row.decision, "REVIEW_REQUIRED")
        self.assertEqual(row.evidence, original.evidence)

    def test_bulk_save_does_not_acknowledge_source_zip(self):
        original = broker()
        stored = self.save([original], [original.model_copy(deep=True)], edited_index=None)
        self.assertEqual(stored.schedule_a_broker_rows[0], original)

    def test_invalid_zip_remains_rejected(self):
        with self.assertRaisesRegex(ValueError, "ZIP"):
            self.save([broker()], [broker(zip_code="50398-900")])

    def test_edited_broker_identity_reaches_the_explicitly_selected_ftw_row(self):
        from app.services.schedule_a_broker_matching import resolved_schedule_a_broker_rows
        original = broker(validation_results=[], decision="AUTOMATIC", fee_total="0")
        submitted = original.model_copy(update={"name": "CORRECTED BROKER LLC", "address_line_1": "42 MAIN ST"})
        stored = self.save([original], [submitted])
        match = ScheduleABrokerMatch(extracted_index=0, ftw_index=0, status="CONFIRMED", resolved=True)
        current = [{"NameXX": original.name, "AddressLine1XX": original.address_line_1, "CommPdAmtXX": "21871.85"}]
        updated = resolved_schedule_a_broker_rows(stored.schedule_a_broker_rows, current, [match])[0]
        self.assertEqual(updated.name, "CORRECTED BROKER LLC")
        self.assertEqual(updated.address_line_1, "42 MAIN ST")
        self.assertEqual(updated.commission_total, original.commission_total)

    def test_confirmed_identity_edit_can_clear_an_old_suite_without_restoring_it(self):
        import xml.etree.ElementTree as ET
        from app.services.xml_builder import build_schedule_a_records_update_xml
        original = broker(validation_results=[], decision="AUTOMATIC", fee_total="0", address_line_2="OLD SUITE")
        submitted = original.model_copy(update={"address_line_2": ""})
        stored = self.save([original], [submitted])
        xml = build_schedule_a_records_update_xml([{ "ftw_seq_no": "1", "query_results": {"ScheduleDesc": "TEST"},
            "query_subparts": {"Broker": [{"NameXX": original.name, "AddressLine2XX": "OLD SUITE"}]}}],
            "1", [], year="2025", schedule_a_broker_rows=stored.schedule_a_broker_rows)
        self.assertEqual(ET.fromstring(xml).findtext(".//Broker/AddressLine2XX"), "")
        self.assertNotIn("OLD SUITE", xml)
        service = FTWilliamsReviewService()
        expected = service._update_documents(xml, "DOLScheduleAData")[0]
        broker_row = expected["__subparts__"]["Broker"][0]
        self.assertIn("AddressLine2XX", broker_row)
        mismatches = service._compare_readback_document(FormType.SCHEDULE_A, expected,
            {"ScheduleDesc": "TEST", "OverrideCommissionsAndFees": "0"},
            actual_subparts={"Broker": [{**broker_row, "AddressLine2XX": "OLD SUITE"}]})
        self.assertTrue(any("AddressLine2" in item["tag"] for item in mismatches))

    def test_blank_zip_does_not_clear_source_hold(self):
        stored = self.save([broker()], [broker(zip_code="")])
        self.assertEqual(stored.schedule_a_broker_rows[0].decision, "REVIEW_REQUIRED")

    def test_count_includes_date_and_source_hold_once_per_broker(self):
        review = FTWilliamsReview(
            filing_id="test", plan_year_conflict={"worksheet_begin": "01/01/2025", "ftw_form_begin": "10/01/2025"},
            schedule_a_broker_rows=[broker()],
            schedule_a_broker_matches=[ScheduleABrokerMatch(extracted_index=0, status="NEEDS_CONFIRMATION", resolved=False)],
        )
        self.assertEqual(FTWilliamsReviewService._review_decision_count_updates(review)["decision_required_count"], 2)
        review.schedule_a_broker_matches[0].resolved = True
        self.assertEqual(FTWilliamsReviewService._review_decision_count_updates(review)["decision_required_count"], 2)
        review.schedule_a_broker_rows[0].validation_results = []
        review.schedule_a_broker_rows[0].decision = "UNASSESSED"
        self.assertEqual(FTWilliamsReviewService._review_decision_count_updates(review)["decision_required_count"], 1)
        self.assertIsNotNone(FTWilliamsReviewService._review_plan_year_block_reason(review))


if __name__ == "__main__":
    unittest.main()
