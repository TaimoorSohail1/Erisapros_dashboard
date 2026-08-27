import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.repositories as repositories
from fastapi import HTTPException
from app.api.filings import (
    delete_filing_from_dashboard,
    get_filing,
    get_ftwilliams_bring_forward_link,
    list_filings,
    regenerate_xml,
    unapprove_filing,
    update_field,
)
from app.models import ExtractedField, ExtractedFieldStatus, FieldEditRequest, Filing, FilingStatus, FormType, FTWilliamsReview


def run_async(coro):
    return asyncio.run(coro)


class FilingsApiTests(unittest.TestCase):
    def setUp(self):
        repositories._repository = repositories.MemoryRepository()

    def tearDown(self):
        repositories._repository = None

    def test_delete_filing_from_dashboard_soft_deletes_with_audit_trail(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Delete Me Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/delete-me",
                    intake_source="SHAREFILE",
                    sharefile_item_id="sf-item-1",
                    sharefile_parent_id="sf-folder-1",
                    error_message="Previous failure",
                    package_documents=[
                        {
                            "sharefile_item_id": "sf-item-1",
                            "file_name": "Delete Me Schedule A.pdf",
                            "document_type": "SCHEDULE_A",
                        },
                        {
                            "sharefile_item_id": "sf-worksheet-1",
                            "file_name": "Plan Worksheet.docx",
                            "document_type": "PLAN_WORKSHEET",
                        },
                    ],
                )
            )

            response = await delete_filing_from_dashboard(filing.id)
            updated = await repo.get_filing(filing.id)
            events = await repo.list_events(filing.id)
            audits = await repo.list_audit_logs(filing.id)
            visible = await list_filings()
            suppressions = {
                item_id: await repo.get_sharefile_suppression(item_id)
                for item_id in ("sf-item-1", "sf-worksheet-1")
            }
            return response, updated, events, audits, visible, suppressions

        response, updated, events, audits, visible, suppressions = run_async(scenario())

        self.assertEqual(response["status"], FilingStatus.DELETED)
        self.assertEqual(updated.status, FilingStatus.DELETED)
        self.assertIsNone(updated.error_message)
        self.assertEqual(events[-1].type, "DELETE_FROM_DASHBOARD")
        self.assertEqual(audits[-1].event, "DASHBOARD_DELETE")
        self.assertEqual(audits[-1].details["sharefile_item_id"], "sf-item-1")
        self.assertEqual(visible["filings"], [])
        self.assertEqual(suppressions["sf-item-1"]["reason"], "DASHBOARD_DELETE")
        self.assertIsNone(suppressions["sf-worksheet-1"])

    def test_preview_xml_is_repeatable_and_does_not_mutate_the_filing(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Preview Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/preview",
                    proposed_xml="<Existing />",
                )
            )
            await repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="Carrier",
                        normalized_field_name="Carrier",
                        mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                        mapped_label="Carrier",
                        ftw_field="InsCarrierName",
                        xml_tag="InsCarrierName",
                        proposed_value="Example Carrier",
                        form_type=FormType.SCHEDULE_A,
                    )
                ]
            )
            first = await regenerate_xml(filing.id)
            second = await regenerate_xml(filing.id)
            stored = await repo.get_filing(filing.id)
            return first, second, stored, await repo.list_events(filing.id), await repo.list_audit_logs(filing.id)

        first, second, stored, events, audits = run_async(scenario())
        self.assertEqual(first, second)
        self.assertIn("Example Carrier", first["proposed_xml"])
        self.assertEqual(stored.proposed_xml, "<Existing />")
        self.assertEqual(events, [])
        self.assertEqual(audits, [])

    def test_preview_xml_returns_actionable_validation_error_for_invalid_value(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Invalid Date Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/invalid-date",
                )
            )
            await repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="Policy from date",
                        normalized_field_name="Policy from date",
                        mapped_rule_key="schedule_a_part_i_1f_policy_year_beginning_date",
                        mapped_label="Policy from date",
                        proposed_value="202501",
                        form_type=FormType.SCHEDULE_A,
                    )
                ]
            )
            with self.assertRaises(HTTPException) as raised:
                await regenerate_xml(filing.id)
            return raised.exception

        error = run_async(scenario())
        self.assertEqual(error.status_code, 400)
        self.assertIn("InsPolicyFromDate:202501", str(error.detail))
        self.assertIn("expected a valid date", str(error.detail))

    def test_unapprove_filing_clears_approval_and_locks_send_flow(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Approved Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/approved",
                    intake_source="SHAREFILE",
                    status=FilingStatus.APPROVED,
                )
            )
            await repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="Missing high priority",
                        normalized_field_name="Missing high priority",
                        status=ExtractedFieldStatus.MISSING,
                    )
                ]
            )

            response = await unapprove_filing(filing.id)
            updated = await repo.get_filing(filing.id)
            events = await repo.list_events(filing.id)
            audits = await repo.list_audit_logs(filing.id)
            return response, updated, events, audits

        response, updated, events, audits = run_async(scenario())

        self.assertEqual(response["status"], FilingStatus.NEEDS_REVIEW)
        self.assertEqual(updated.status, FilingStatus.NEEDS_REVIEW)
        self.assertIsNone(updated.approved_at)
        self.assertEqual(events[-1].type, "UNAPPROVE")
        self.assertEqual(audits[-1].event, "UNAPPROVED")

    def test_field_review_actions_distinguish_confirmed_values_from_marked_missing(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Review Buttons Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/review-buttons",
                    intake_source="SHAREFILE",
                )
            )
            field = (
                await repo.add_fields(
                    [
                        ExtractedField(
                            filing_id=filing.id,
                            source_field_name="4. Plan Characteristic Codes",
                            normalized_field_name="4. Plan Characteristic Codes",
                            status=ExtractedFieldStatus.MISSING,
                            status_reason="Not found in extraction output.",
                        )
                    ]
                )
            )[0]

            confirmed = await update_field(
                filing.id,
                field.id,
                FieldEditRequest(proposed_value="2A"),
            )
            confirmed_field = confirmed["field"].model_copy(deep=True)
            marked_missing = await update_field(
                filing.id,
                field.id,
                FieldEditRequest(proposed_value="", mark_missing=True),
            )
            events = await repo.list_events(filing.id)
            return confirmed_field, marked_missing, events

        confirmed, marked_missing, events = run_async(scenario())

        self.assertEqual(confirmed.status, ExtractedFieldStatus.EDITED)
        self.assertEqual(confirmed.status_reason, "Value confirmed by reviewer.")
        self.assertEqual(marked_missing["field"].status, ExtractedFieldStatus.MISSING)
        self.assertEqual(marked_missing["field"].proposed_value, "")
        self.assertEqual(marked_missing["field"].status_reason, "Marked missing by reviewer.")
        self.assertEqual([event.type for event in events[-2:]], ["EDIT", "MARK_MISSING"])

    def test_field_edit_recovers_failed_filing_for_reapproval(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Recovered Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/recovered",
                    intake_source="SHAREFILE",
                    status=FilingStatus.FAILED,
                    error_message="Previous FT Williams update failed.",
                )
            )
            field = (
                await repo.add_fields(
                    [
                        ExtractedField(
                            filing_id=filing.id,
                            source_field_name="1e. Persons Covered",
                            normalized_field_name="persons_covered",
                            mapped_rule_key="schedule_a_part_i_1e_persons_covered_end_of_policy_year",
                            mapped_label="1e. Persons Covered",
                            form_type=FormType.SCHEDULE_A,
                            priority="HIGH",
                            status=ExtractedFieldStatus.MISSING,
                        )
                    ]
                )
            )[0]

            await update_field(filing.id, field.id, FieldEditRequest(proposed_value="10"))
            return await repo.get_filing(filing.id)

        updated = run_async(scenario())

        self.assertEqual(updated.status, FilingStatus.READY_FOR_APPROVAL)
        self.assertIsNone(updated.approved_at)
        self.assertIsNone(updated.error_message)

    def test_field_edit_saves_when_another_field_fails_ftw_preview_validation(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Independent field decision.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/independent-field-decision",
                    intake_source="SHAREFILE",
                )
            )
            fields = await repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Persons Covered",
                        normalized_field_name="persons_covered",
                        mapped_rule_key="schedule_a_part_i_1e_persons_covered_end_of_policy_year",
                        mapped_label="1e. Persons Covered",
                        form_type=FormType.SCHEDULE_A,
                        proposed_value="9",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1f. Plan Sponsor Address",
                        normalized_field_name="sponsor_address",
                        mapped_rule_key="form_5500_part_i_1f_plan_sponsor_address",
                        mapped_label="1f. Plan Sponsor Address",
                        form_type=FormType.FORM_5500,
                        ftw_resolved_tag="SDAddressLine1",
                        proposed_value="12345 EXTREMELY LONG UNDELIMITED BUSINESS CENTER ADDRESS",
                    ),
                ]
            )

            unrelated_before = next(
                field.model_dump()
                for field in fields
                if field.mapped_rule_key == "form_5500_part_i_1f_plan_sponsor_address"
            )
            response = await update_field(
                filing.id,
                fields[0].id,
                FieldEditRequest(proposed_value="10"),
            )
            saved = await repo.list_fields(filing.id)
            return response, saved, unrelated_before

        response, saved, unrelated_before = run_async(scenario())

        edited = next(field for field in saved if field.mapped_rule_key == "schedule_a_part_i_1e_persons_covered_end_of_policy_year")
        invalid_address = next(field for field in saved if field.mapped_rule_key == "form_5500_part_i_1f_plan_sponsor_address")
        self.assertEqual(response["field"].proposed_value, "10")
        self.assertEqual(edited.status, ExtractedFieldStatus.EDITED)
        self.assertEqual(edited.proposed_value, "10")
        self.assertEqual(invalid_address.model_dump(), unrelated_before)
        self.assertIsNone(response["proposed_xml"])

    def test_manual_supported_value_is_a_single_ftw_update(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Manual persons covered.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/manual-persons-covered",
                    intake_source="SHAREFILE",
                )
            )
            fields = await repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Persons Covered (End of Policy Year)",
                        normalized_field_name="persons_covered",
                        mapped_rule_key="schedule_a_part_i_1e_persons_covered_end_of_policy_year",
                        mapped_label="1e. Persons Covered (End of Policy Year)",
                        form_type=FormType.SCHEDULE_A,
                        status=ExtractedFieldStatus.MISSING,
                        value="",
                        proposed_value="",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="9a(4). Total Earned Premium",
                        normalized_field_name="total_earned_premium",
                        mapped_rule_key="schedule_a_part_iii_9a_4_earned_1_2_3",
                        mapped_label="9a(4). Total Earned Premium",
                        form_type=FormType.SCHEDULE_A,
                        status=ExtractedFieldStatus.MISSING,
                        value="",
                        proposed_value="",
                    ),
                ]
            )
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    configured=True,
                    current_query_sent=True,
                    current_query_success=True,
                    current_query_complete=True,
                    current_year_exists=True,
                    schedule_a_current_values={"InsPrsnCoveredEoyCnt": ""},
                )
            )

            response = await update_field(
                filing.id,
                fields[0].id,
                FieldEditRequest(proposed_value="1100"),
            )
            saved = await repo.list_fields(filing.id)
            return response, saved

        response, saved = run_async(scenario())

        comparison = next(
            field
            for field in response["ftw_review"].fields
            if field.rule_key == "schedule_a_part_i_1e_persons_covered_end_of_policy_year"
        )
        untouched = next(
            field
            for field in saved
            if field.mapped_rule_key == "schedule_a_part_iii_9a_4_earned_1_2_3"
        )
        self.assertEqual(response["field"].status, ExtractedFieldStatus.EDITED)
        self.assertEqual(comparison.ftw_tag, "InsPrsnCoveredEoyCnt")
        self.assertTrue(comparison.changed)
        self.assertTrue(comparison.update_included)
        self.assertEqual(untouched.status, ExtractedFieldStatus.MISSING)
        self.assertEqual(untouched.proposed_value, "")

    def test_field_edit_applies_preview_validation_only_to_the_selected_field(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Invalid selected address.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/invalid-selected-address",
                    intake_source="SHAREFILE",
                )
            )
            fields = await repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1f. Plan Sponsor Address",
                        normalized_field_name="sponsor_address",
                        mapped_rule_key="form_5500_part_i_1f_plan_sponsor_address",
                        mapped_label="1f. Plan Sponsor Address",
                        form_type=FormType.FORM_5500,
                        ftw_resolved_tag="SDAddressLine1",
                        proposed_value="OLD ADDRESS",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Persons Covered",
                        normalized_field_name="persons_covered",
                        mapped_rule_key="schedule_a_part_i_1e_persons_covered_end_of_policy_year",
                        mapped_label="1e. Persons Covered",
                        form_type=FormType.SCHEDULE_A,
                        status=ExtractedFieldStatus.MISSING,
                        proposed_value="",
                    ),
                ]
            )
            untouched_before = fields[1].model_dump()

            response = await update_field(
                filing.id,
                fields[0].id,
                FieldEditRequest(proposed_value="12345 EXTREMELY LONG UNDELIMITED BUSINESS CENTER ADDRESS"),
            )
            saved = await repo.list_fields(filing.id)
            return response, saved, untouched_before

        response, saved, untouched_before = run_async(scenario())

        selected = next(field for field in saved if field.id == response["field"].id)
        untouched = next(field for field in saved if field.id != response["field"].id)
        self.assertEqual(selected.status, ExtractedFieldStatus.LOW_CONFIDENCE)
        self.assertIn("FT Williams pre-send validation", selected.status_reason)
        self.assertEqual(untouched.model_dump(), untouched_before)

    def test_filing_detail_reads_independent_collections_concurrently(self):
        class ConcurrentReadRepository(repositories.MemoryRepository):
            def __init__(self):
                super().__init__()
                self.active_reads = 0
                self.max_active_reads = 0

            async def _track(self, operation):
                self.active_reads += 1
                self.max_active_reads = max(self.max_active_reads, self.active_reads)
                try:
                    await asyncio.sleep(0.01)
                    return await operation
                finally:
                    self.active_reads -= 1

            async def list_fields(self, filing_id):
                return await self._track(super().list_fields(filing_id))

            async def list_events(self, filing_id):
                return await self._track(super().list_events(filing_id))

            async def list_extraction_jobs(self, filing_id):
                return await self._track(super().list_extraction_jobs(filing_id))

            async def list_audit_logs(self, filing_id):
                return await self._track(super().list_audit_logs(filing_id))

            async def get_ftwilliams_review(self, filing_id):
                return await self._track(super().get_ftwilliams_review(filing_id))

        async def scenario():
            repo = ConcurrentReadRepository()
            repositories._repository = repo
            filing = await repo.create_filing(
                Filing(file_name="Concurrent.pdf", content_type="application/pdf", file_size=1, s3_key="concurrent")
            )
            detail = await get_filing(filing.id)
            return detail, repo.max_active_reads

        detail, max_active_reads = run_async(scenario())

        self.assertEqual(detail.file_name, "Concurrent.pdf")
        self.assertEqual(max_active_reads, 5)

    def test_field_decision_reuses_one_loaded_field_snapshot(self):
        class CountingRepository(repositories.MemoryRepository):
            def __init__(self):
                super().__init__()
                self.field_list_reads = 0

            async def list_fields(self, filing_id):
                self.field_list_reads += 1
                return await super().list_fields(filing_id)

        async def scenario():
            repo = CountingRepository()
            repositories._repository = repo
            filing = await repo.create_filing(
                Filing(file_name="Fast decision.pdf", content_type="application/pdf", file_size=1, s3_key="fast-decision")
            )
            field = (
                await repo.add_fields(
                    [
                        ExtractedField(
                            filing_id=filing.id,
                            source_field_name="1f. Plan Sponsor Address",
                            normalized_field_name="sponsor_address",
                            mapped_rule_key="form_5500_part_i_1f_plan_sponsor_address",
                            mapped_label="1f. Plan Sponsor Address",
                            form_type=FormType.FORM_5500,
                            proposed_value="OLD ADDRESS",
                        )
                    ]
                )
            )[0]
            result = await update_field(filing.id, field.id, FieldEditRequest(proposed_value="NEW ADDRESS"))
            return result, repo.field_list_reads

        result, field_list_reads = run_async(scenario())

        self.assertEqual(result["field"].proposed_value, "NEW ADDRESS")
        self.assertEqual(field_list_reads, 1)

    def test_bring_forward_link_is_safe_and_audited_without_mutating_ftw(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Missing Current Year Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/bring-forward",
                    intake_source="SHAREFILE",
                )
            )
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    configured=True,
                    current_query_sent=True,
                    current_query_success=True,
                    current_year_exists=False,
                    bring_forward_required=True,
                    year="2025",
                    ftw_customer_id="1822236451",
                    ftw_plan_id="2196092986",
                    ftw_plan_url="https://www.ftwilliam.com/",
                )
            )
            response = await get_ftwilliams_bring_forward_link(filing.id)
            audits = await repo.list_audit_logs(filing.id)
            return response, audits

        response, audits = run_async(scenario())

        self.assertEqual(
            response["url"],
            "https://ftwilliam.com/cgi-bin/index.cgi?"
            "#go=iframe&page=/cgi-bin/PlanDoc2.cgi&PerformDoc5500=1&"
            "plan=1822236451,2196092986&Year=2025",
        )
        self.assertEqual(response["target_year"], "2025")
        self.assertIsNone(response["prior_year"])
        self.assertEqual(audits[-1].event, "FTWILLIAMS_BRING_FORWARD_OPENED")
        self.assertFalse(audits[-1].details["mutation_requested"])

    def test_bring_forward_link_uses_the_selected_2026_filing_year(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="2026 Missing Current Year Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/bring-forward-2026",
                    intake_source="SHAREFILE",
                )
            )
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    configured=True,
                    current_query_sent=True,
                    current_query_success=True,
                    current_year_exists=False,
                    bring_forward_required=True,
                    year="2026",
                    ftw_customer_id="1822236451",
                    ftw_plan_id="2196092986",
                )
            )
            return await get_ftwilliams_bring_forward_link(filing.id)

        response = run_async(scenario())

        self.assertIn("PerformDoc5500=1", response["url"])
        self.assertIn("Year=2026", response["url"])
        self.assertEqual(response["target_year"], "2026")

    def test_bring_forward_link_rejects_generic_homepage_when_ftw_ids_are_missing(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Missing FTW IDs Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/missing-ftw-ids",
                    intake_source="SHAREFILE",
                )
            )
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    configured=True,
                    current_query_sent=True,
                    current_query_success=True,
                    current_year_exists=False,
                    bring_forward_required=True,
                    year="2025",
                    ftw_plan_url="https://www.ftwilliam.com/",
                )
            )
            with self.assertRaises(HTTPException) as raised:
                await get_ftwilliams_bring_forward_link(filing.id)
            return raised.exception, await repo.list_audit_logs(filing.id)

        error, audits = run_async(scenario())
        self.assertEqual(error.status_code, 400)
        self.assertIn("plan-specific", str(error.detail))
        self.assertEqual(audits, [])


if __name__ == "__main__":
    unittest.main()
