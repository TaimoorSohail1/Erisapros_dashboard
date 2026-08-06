import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.repositories as repositories
from app.api.filings import (
    delete_filing_from_dashboard,
    get_ftwilliams_bring_forward_link,
    list_filings,
    unapprove_filing,
)
from app.models import ExtractedField, ExtractedFieldStatus, Filing, FilingStatus, FTWilliamsReview


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
                )
            )

            response = await delete_filing_from_dashboard(filing.id)
            updated = await repo.get_filing(filing.id)
            events = await repo.list_events(filing.id)
            audits = await repo.list_audit_logs(filing.id)
            visible = await list_filings()
            return response, updated, events, audits, visible

        response, updated, events, audits, visible = run_async(scenario())

        self.assertEqual(response["status"], FilingStatus.DELETED)
        self.assertEqual(updated.status, FilingStatus.DELETED)
        self.assertIsNone(updated.error_message)
        self.assertEqual(events[-1].type, "DELETE_FROM_DASHBOARD")
        self.assertEqual(audits[-1].event, "DASHBOARD_DELETE")
        self.assertEqual(audits[-1].details["sharefile_item_id"], "sf-item-1")
        self.assertEqual(visible["filings"], [])

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
                    comparison_year="2024",
                    comparison_year_source="PRIOR_YEAR_FALLBACK",
                    year="2025",
                    ftw_plan_url="https://www.ftwilliam.com/",
                )
            )
            response = await get_ftwilliams_bring_forward_link(filing.id)
            audits = await repo.list_audit_logs(filing.id)
            return response, audits

        response, audits = run_async(scenario())

        self.assertEqual(response["url"], "https://www.ftwilliam.com/")
        self.assertEqual(response["target_year"], "2025")
        self.assertEqual(response["prior_year"], "2024")
        self.assertEqual(audits[-1].event, "FTWILLIAMS_BRING_FORWARD_OPENED")
        self.assertFalse(audits[-1].details["mutation_requested"])


if __name__ == "__main__":
    unittest.main()
