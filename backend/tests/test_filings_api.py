import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.repositories as repositories
from fastapi import HTTPException
from app.api.filings import (
    delete_filing_from_dashboard,
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
