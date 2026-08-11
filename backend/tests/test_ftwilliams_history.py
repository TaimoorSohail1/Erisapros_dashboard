import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.repositories as repositories
from app.api.ftwilliams import failure_queue, history
from app.models import (
    AuditLog,
    Filing,
    FilingStatus,
    FTWilliamsComparisonField,
    FTWilliamsPlanLookup,
    FTWilliamsSendUpdateRequest,
    FTWilliamsReview,
    FTWilliamsReviewStatus,
)
from app.services.ftwilliams_review import FTWilliamsReviewService


def run_async(coro):
    return asyncio.run(coro)


class FTWilliamsHistoryTests(unittest.TestCase):
    def setUp(self):
        repositories._repository = repositories.MemoryRepository()

    def tearDown(self):
        repositories._repository = None

    def test_history_returns_recent_ftw_activity_with_filing_context(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Planters Bank Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/planters",
                    intake_source="SHAREFILE",
                )
            )
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    status=FTWilliamsReviewStatus.UPDATE_SENT,
                    current_query_success=True,
                    customer_id="64-0223390",
                    plan_id="64-0223390501",
                    ftw_customer_id="688253650",
                    ftw_plan_id="844204992",
                    year="2024",
                    plan_lookup=FTWilliamsPlanLookup(
                        company_employer_id="64-0223390",
                        plan_number="501",
                        plan_name="Planters Bank Welfare Benefit Plan",
                    ),
                    fields=[
                        FTWilliamsComparisonField(label="9. Plan funding arrangement", changed=True, update_included=True),
                        FTWilliamsComparisonField(label="10a. Plan benefit arrangement", changed=True, update_included=True),
                    ],
                )
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=filing.id,
                    event="FTWILLIAMS_UPDATE_SENT",
                    message="Approved fields were sent to FT Williams.",
                    details={"updated_field_count": 2},
                    created_at=datetime.utcnow() - timedelta(hours=2),
                )
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=filing.id,
                    event="FTWILLIAMS_UPDATE_FAILED",
                    message="Old failure.",
                    created_at=datetime.utcnow() - timedelta(days=40),
                )
            )
            superseded = await repo.create_filing(
                Filing(
                    file_name="Superseded Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/old",
                    intake_source="SHAREFILE",
                    status=FilingStatus.SUPERSEDED,
                )
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=superseded.id,
                    event="FTWILLIAMS_REVIEW_PREPARED",
                    message="Superseded preview.",
                    details={"send_queries": False, "field_count": 5},
                    created_at=datetime.utcnow(),
                )
            )
            return await history("30d")

        response = run_async(scenario())
        self.assertEqual(response.range, "30d")
        self.assertEqual(len(response.items), 1)
        item = response.items[0]
        self.assertEqual(item.action_label, "Update sent")
        self.assertEqual(item.status, "success")
        self.assertEqual(item.updated_field_count, 2)
        self.assertEqual(item.company_employer_id, "64-0223390")
        self.assertEqual(item.plan_number, "501")
        self.assertEqual(item.ftw_customer_id, "688253650")

    def test_history_labels_current_query_failures(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Guardian Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/guardian",
                    intake_source="SHAREFILE",
                )
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=filing.id,
                    event="FTWILLIAMS_REVIEW_PREPARED",
                    message="FT Williams side-by-side comparison prepared.",
                    details={"send_queries": True, "current_query_success": False, "field_count": 12},
                    created_at=datetime.utcnow(),
                )
            )
            return await history("1d")

        response = run_async(scenario())
        self.assertEqual(len(response.items), 1)
        self.assertEqual(response.items[0].action_label, "Current data queried")
        self.assertEqual(response.items[0].status, "failed")
        self.assertEqual(response.items[0].updated_field_count, 12)

    def test_history_labels_partial_current_query_as_warning(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Partial Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/partial",
                )
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=filing.id,
                    event="FTWILLIAMS_REVIEW_PREPARED",
                    message="FT Williams comparison prepared with a partial plan lookup.",
                    details={
                        "send_queries": True,
                        "current_query_success": True,
                        "field_count": 8,
                        "error": "Plan error 18: company identifier was not valid.",
                    },
                )
            )
            return await history("1d")

        response = run_async(scenario())
        self.assertEqual(response.items[0].status, "warning")
        self.assertIn("Plan error 18", response.items[0].error_message)

    def test_failure_queue_returns_only_active_unresolved_ftw_failures(self):
        async def scenario():
            repo = repositories.get_repository()
            failed = await repo.create_filing(
                Filing(
                    file_name="Failed Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/failed",
                    intake_source="SHAREFILE",
                    status=FilingStatus.FAILED,
                )
            )
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=failed.id,
                    status=FTWilliamsReviewStatus.UPDATE_FAILED,
                    current_query_success=True,
                    error_message="FT Williams rejected the update.",
                    plan_lookup=FTWilliamsPlanLookup(company_employer_id="12-3456789", plan_number="501"),
                    fields=[
                        FTWilliamsComparisonField(label="9. Plan funding arrangement", changed=True, update_included=True),
                    ],
                )
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=failed.id,
                    event="FTWILLIAMS_UPDATE_FAILED",
                    message="FT Williams update failed.",
                    details={"error": "FT Williams rejected the update."},
                    created_at=datetime.utcnow() - timedelta(minutes=5),
                )
            )

            resolved = await repo.create_filing(
                Filing(
                    file_name="Resolved Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/resolved",
                    intake_source="SHAREFILE",
                    status=FilingStatus.APPROVED,
                )
            )
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=resolved.id,
                    status=FTWilliamsReviewStatus.UPDATE_SENT,
                    current_query_success=True,
                )
            )

            superseded = await repo.create_filing(
                Filing(
                    file_name="Old Failed Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/old-failed",
                    intake_source="SHAREFILE",
                    status=FilingStatus.SUPERSEDED,
                )
            )
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=superseded.id,
                    status=FTWilliamsReviewStatus.UPDATE_FAILED,
                    current_query_success=True,
                    error_message="Old failure.",
                )
            )
            return await failure_queue()

        response = run_async(scenario())
        self.assertEqual(response.total, 1)
        self.assertEqual(response.items[0].filing_name, "Failed Schedule A.pdf")
        self.assertEqual(response.items[0].failure_reason, "FT Williams rejected the update.")
        self.assertEqual(response.items[0].attempted_field_count, 1)
        self.assertEqual(response.items[0].company_employer_id, "12-3456789")

    def test_failed_ftw_update_can_be_retried(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Retry Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/retry",
                    intake_source="SHAREFILE",
                    status=FilingStatus.FAILED,
                )
            )
            review = await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    status=FTWilliamsReviewStatus.UPDATE_FAILED,
                    current_query_success=True,
                    error_message="Previous FT Williams update failed.",
                )
            )
            service = FTWilliamsReviewService()

            async def fake_approve_and_update(filing_id: str, **kwargs):
                self.assertEqual(filing_id, filing.id)
                self.assertTrue(kwargs["send_to_ftw"])
                return review

            service.approve_and_update = fake_approve_and_update
            return await service.send_approved_update(filing.id, FTWilliamsSendUpdateRequest())

        result = run_async(scenario())
        self.assertEqual(result.status, FTWilliamsReviewStatus.UPDATE_FAILED)


if __name__ == "__main__":
    unittest.main()
