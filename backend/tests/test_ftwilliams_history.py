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
    ClientFacingError,
    Filing,
    FilingStatus,
    FTWilliamsComparisonField,
    FTWilliamsPlanLookup,
    FTWilliamsOperationDiagnostic,
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

    def test_history_batches_filing_and_review_context_reads(self):
        class CountingRepository(repositories.MemoryRepository):
            def __init__(self):
                super().__init__()
                self.individual_filing_reads = 0
                self.individual_review_reads = 0
                self.batch_filing_reads = 0
                self.batch_review_reads = 0

            async def get_filing(self, filing_id):
                self.individual_filing_reads += 1
                return await super().get_filing(filing_id)

            async def get_ftwilliams_review(self, filing_id):
                self.individual_review_reads += 1
                return await super().get_ftwilliams_review(filing_id)

            async def get_filings_by_ids(self, filing_ids):
                self.batch_filing_reads += 1
                return [self.filings[filing_id] for filing_id in filing_ids if filing_id in self.filings]

            async def get_ftwilliams_reviews_by_filing_ids(self, filing_ids):
                self.batch_review_reads += 1
                return [self.ftwilliams_reviews[filing_id] for filing_id in filing_ids if filing_id in self.ftwilliams_reviews]

        async def scenario():
            repo = CountingRepository()
            repositories._repository = repo
            for index in range(3):
                filing = await repo.create_filing(
                    Filing(
                        file_name=f"History {index}.pdf",
                        content_type="application/pdf",
                        file_size=1,
                        s3_key=f"history-{index}",
                    )
                )
                await repo.upsert_ftwilliams_review(FTWilliamsReview(filing_id=filing.id))
                await repo.add_audit(
                    AuditLog(
                        filing_id=filing.id,
                        event="FTWILLIAMS_REVIEW_PREPARED",
                        message="Prepared",
                        details={"field_count": index + 1},
                    )
                )
            repo.individual_filing_reads = 0
            repo.individual_review_reads = 0
            response = await history("1d")
            return response, repo

        response, repo = run_async(scenario())

        self.assertEqual(len(response.items), 3)
        self.assertEqual(repo.batch_filing_reads, 1)
        self.assertEqual(repo.batch_review_reads, 1)
        self.assertEqual(repo.individual_filing_reads, 0)
        self.assertEqual(repo.individual_review_reads, 0)

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
                    update_attempted_count=1,
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
                    details={"error": "FT Williams rejected the update.", "update_attempted_count": 1},
                    created_at=datetime.utcnow() - timedelta(minutes=5),
                )
            )

            blocked = await repo.create_filing(
                Filing(
                    file_name="Pre-send Blocked Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/pre-send-blocked",
                    intake_source="SHAREFILE",
                    status=FilingStatus.FAILED,
                )
            )
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=blocked.id,
                    status=FTWilliamsReviewStatus.UPDATE_FAILED,
                    current_query_success=True,
                    error_message="A current FT Williams Schedule A must be matched before sending.",
                    update_attempted_count=0,
                    fields=[
                        FTWilliamsComparisonField(label="3a. Broker", changed=True, update_included=True),
                    ],
                )
            )

            unknown = await repo.create_filing(
                Filing(
                    file_name="Unverified Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/unverified",
                    intake_source="SHAREFILE",
                    status=FilingStatus.FAILED,
                )
            )
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=unknown.id,
                    status=FTWilliamsReviewStatus.UPDATE_UNKNOWN,
                    current_query_success=False,
                    error_message="FT Williams accepted the request but returned no verifiable response.",
                )
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=unknown.id,
                    event="FTWILLIAMS_UPDATE_UNKNOWN",
                    message="FT Williams update requires verification.",
                    details={"error": "FT Williams accepted the request but returned no verifiable response."},
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
        self.assertEqual(response.total, 3)
        by_name = {item.filing_name: item for item in response.items}
        self.assertEqual(by_name["Failed Schedule A.pdf"].failure_reason, "FT Williams rejected the update.")
        self.assertEqual(by_name["Failed Schedule A.pdf"].attempted_field_count, 1)
        self.assertEqual(by_name["Pre-send Blocked Schedule A.pdf"].attempted_field_count, 0)
        self.assertEqual(by_name["Failed Schedule A.pdf"].company_employer_id, "12-3456789")
        self.assertEqual(by_name["Unverified Schedule A.pdf"].review_status, FTWilliamsReviewStatus.UPDATE_UNKNOWN)
        self.assertEqual(by_name["Unverified Schedule A.pdf"].last_action_label, "Verification required")

    def test_failure_queue_batches_filing_and_failed_audit_reads(self):
        class CountingRepository(repositories.MemoryRepository):
            def __init__(self):
                super().__init__()
                self.individual_filing_reads = 0
                self.individual_audit_reads = 0
                self.batch_filing_reads = 0
                self.batch_audit_reads = 0

            async def get_filing(self, filing_id):
                self.individual_filing_reads += 1
                return await super().get_filing(filing_id)

            async def list_audit_logs(self, filing_id):
                self.individual_audit_reads += 1
                return await super().list_audit_logs(filing_id)

            async def get_filings_by_ids(self, filing_ids):
                self.batch_filing_reads += 1
                return await super().get_filings_by_ids(filing_ids)

            async def list_latest_ftwilliams_failure_audits(self, filing_ids):
                self.batch_audit_reads += 1
                return await super().list_latest_ftwilliams_failure_audits(filing_ids)

            async def list_unresolved_ftwilliams_failure_audits(self):
                self.batch_audit_reads += 1
                return await super().list_unresolved_ftwilliams_failure_audits()

        async def scenario():
            repo = CountingRepository()
            repositories._repository = repo
            for index in range(3):
                filing = await repo.create_filing(
                    Filing(file_name=f"Failed {index}.pdf", content_type="application/pdf", file_size=1, s3_key=f"failed-{index}")
                )
                await repo.upsert_ftwilliams_review(
                    FTWilliamsReview(filing_id=filing.id, status=FTWilliamsReviewStatus.UPDATE_FAILED)
                )
                await repo.add_audit(
                    AuditLog(
                        filing_id=filing.id,
                        event="FTWILLIAMS_UPDATE_FAILED",
                        message="Failed",
                    )
                )
            repo.individual_filing_reads = 0
            repo.individual_audit_reads = 0
            response = await failure_queue()
            return response, repo

        response, repo = run_async(scenario())

        self.assertEqual(response.total, 3)
        self.assertEqual(repo.batch_filing_reads, 1)
        self.assertEqual(repo.batch_audit_reads, 1)
        self.assertEqual(repo.individual_filing_reads, 0)
        self.assertEqual(repo.individual_audit_reads, 0)

    def test_current_query_refresh_keeps_active_failure_in_queue_with_diagnostics(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Guardian Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/guardian-current",
                    status=FilingStatus.FAILED,
                )
            )
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    status=FTWilliamsReviewStatus.CURRENT_QUERIED,
                    current_query_success=True,
                    active_failure=True,
                    active_failure_reason="FT Williams returned an empty response.",
                    active_failure_client_error=ClientFacingError(
                        title="FT Williams returned no usable response",
                        message="The update outcome is unknown.",
                        code="FTW_EMPTY_OR_MALFORMED_RESPONSE",
                        technical_details="HTTP 200 with an empty body",
                    ),
                    active_failure_at=datetime.utcnow(),
                    update_diagnostics=[
                        FTWilliamsOperationDiagnostic(
                            operation="update_schedule_a",
                            sent=True,
                            http_status=200,
                            outcome_code="EMPTY_RESPONSE",
                            response_received=False,
                            request_id="request-123",
                            elapsed_ms=412,
                        )
                    ],
                )
            )
            return await failure_queue()

        response = run_async(scenario())

        self.assertEqual(response.total, 1)
        item = response.items[0]
        self.assertEqual(item.review_status, FTWilliamsReviewStatus.CURRENT_QUERIED)
        self.assertEqual(item.error_code, "FTW_EMPTY_OR_MALFORMED_RESPONSE")
        self.assertEqual(item.operation_diagnostics[0].outcome_code, "EMPTY_RESPONSE")
        self.assertEqual(item.operation_diagnostics[0].request_id, "request-123")

    def test_failure_queue_recovers_legacy_failure_hidden_by_current_query(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Legacy Guardian Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/legacy-guardian",
                    status=FilingStatus.APPROVED,
                )
            )
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    status=FTWilliamsReviewStatus.CURRENT_QUERIED,
                    current_query_success=True,
                )
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=filing.id,
                    event="FTWILLIAMS_UPDATE_FAILED",
                    message="FT Williams update failed.",
                    details={
                        "error": "FT Williams returned an empty response.",
                        "error_code": "FTW_EMPTY_OR_MALFORMED_RESPONSE",
                    },
                    created_at=datetime.utcnow() - timedelta(minutes=2),
                )
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=filing.id,
                    event="FTWILLIAMS_CURRENT_QUERIED",
                    message="Current values refreshed after the failure.",
                    created_at=datetime.utcnow() - timedelta(minutes=1),
                )
            )
            return await failure_queue()

        response = run_async(scenario())

        self.assertEqual(response.total, 1)
        self.assertEqual(response.items[0].filing_name, "Legacy Guardian Schedule A.pdf")
        self.assertEqual(response.items[0].error_code, "FTW_EMPTY_OR_MALFORMED_RESPONSE")

    def test_later_resolution_event_prevents_legacy_failure_recovery(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Resolved Legacy Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/resolved-legacy",
                    status=FilingStatus.APPROVED,
                )
            )
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    status=FTWilliamsReviewStatus.CURRENT_QUERIED,
                )
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=filing.id,
                    event="FTWILLIAMS_UPDATE_FAILED",
                    message="FT Williams update failed.",
                    created_at=datetime.utcnow() - timedelta(minutes=2),
                )
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=filing.id,
                    event="FTWILLIAMS_UPDATE_FAILURE_DISMISSED",
                    message="Operator dismissed the failure.",
                    created_at=datetime.utcnow() - timedelta(minutes=1),
                )
            )
            return await failure_queue()

        response = run_async(scenario())
        self.assertEqual(response.total, 0)

    def test_operator_can_dismiss_recovered_legacy_failure(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Dismiss Recovered Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/dismiss-recovered",
                    status=FilingStatus.APPROVED,
                )
            )
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    status=FTWilliamsReviewStatus.CURRENT_QUERIED,
                )
            )
            await repo.add_audit(
                AuditLog(
                    filing_id=filing.id,
                    event="FTWILLIAMS_UPDATE_FAILED",
                    message="Legacy failure.",
                )
            )
            review = await FTWilliamsReviewService().dismiss_active_failure(
                filing.id,
                "Reviewed legacy failure",
            )
            return review, await failure_queue()

        review, response = run_async(scenario())
        self.assertIsNotNone(review.failure_dismissed_at)
        self.assertEqual(response.total, 0)

    def test_operator_can_dismiss_active_failure_without_erasing_audit_history(self):
        async def scenario():
            repo = repositories.get_repository()
            filing = await repo.create_filing(
                Filing(
                    file_name="Dismiss Schedule A.pdf",
                    content_type="application/pdf",
                    file_size=100,
                    s3_key="sharefile-package/dismiss",
                    status=FilingStatus.FAILED,
                )
            )
            await repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    status=FTWilliamsReviewStatus.CURRENT_QUERIED,
                    active_failure=True,
                    active_failure_reason="Vendor response failed.",
                )
            )
            service = FTWilliamsReviewService()
            review = await service.dismiss_active_failure(filing.id, "Reviewed with FT support")
            queue = await failure_queue()
            audits = await repo.list_audit_logs(filing.id)
            return review, queue, audits

        review, queue, audits = run_async(scenario())

        self.assertFalse(review.active_failure)
        self.assertIsNotNone(review.failure_dismissed_at)
        self.assertEqual(queue.total, 0)
        self.assertIn("FTWILLIAMS_UPDATE_FAILURE_DISMISSED", [audit.event for audit in audits])

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
