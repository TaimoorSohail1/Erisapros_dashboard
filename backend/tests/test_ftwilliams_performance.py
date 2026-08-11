import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.repositories as repositories
from app.models import (
    DocumentType,
    ExtractedField,
    FieldPriority,
    Filing,
    FormType,
    FTWilliamsQueryResponse,
    FTWilliamsStatusItem,
)
from app.services.ftwilliams import FTWilliamsService
from app.services.ftwilliams_review import FTWilliamsReviewService, clear_ftw_current_snapshot_cache



def sample_filing() -> Filing:
    return Filing(
        file_name="2025 Filing Performance Test",
        content_type="application/vnd.erisapros.filing-package",
        file_size=100,
        document_type=DocumentType.SCHEDULE_A,
        s3_key="sharefile-package/performance-test",
        intake_source="SHAREFILE",
    )


class SlowCountingFTWilliamsService(FTWilliamsService):
    def __init__(self):
        self.calls = []
        self.active_schedule_queries = 0
        self.max_active_schedule_queries = 0

    def status(self) -> dict:
        return {"configured": True}

    async def run_query(self, payload):
        self.calls.append(payload)
        request_xml = self.mask_key_id(self.build_request_xml(payload))
        if payload.operation == "query_plan":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="Plan",
                        error_code="0",
                        ftw_customer_id="customer-1",
                        ftw_plan_id="plan-1",
                        query_results={"PlanNumber": "501", "PlanLine1": "Performance Test Plan"},
                    )
                ],
            )
        if payload.operation == "query_5500":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="5500",
                        error_code="0",
                        query_results={"PLAN_NAME0": "Performance Test Plan"},
                    )
                ],
            )
        if payload.operation == "query_schedule_a":
            self.active_schedule_queries += 1
            self.max_active_schedule_queries = max(
                self.max_active_schedule_queries,
                self.active_schedule_queries,
            )
            try:
                await asyncio.sleep(0.02)
            finally:
                self.active_schedule_queries -= 1
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=False,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="DOLScheduleAData",
                        error_code="59",
                        error_desc="Could not locate form",
                    )
                ],
            )
        raise AssertionError(f"Unexpected FT Williams operation: {payload.operation}")


async def create_filing_with_identity():
    repo = repositories.get_repository()
    filing = await repo.create_filing(sample_filing())
    await repo.add_fields(
        [
            ExtractedField(
                filing_id=filing.id,
                source_field_name="1e. Plan Sponsor EIN",
                normalized_field_name="sponsor_ein",
                mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                mapped_label="1e. Plan Sponsor EIN",
                form_type=FormType.FORM_5500,
                source_document_type=DocumentType.PLAN_WORKSHEET,
                priority=FieldPriority.MEDIUM,
                value="73-1185740",
                proposed_value="73-1185740",
            ),
            ExtractedField(
                filing_id=filing.id,
                source_field_name="1b. Plan Number (PN)",
                normalized_field_name="plan_number",
                mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                mapped_label="1b. Plan Number (PN)",
                form_type=FormType.FORM_5500,
                source_document_type=DocumentType.PLAN_WORKSHEET,
                priority=FieldPriority.MEDIUM,
                value="501",
                proposed_value="501",
            ),
            ExtractedField(
                filing_id=filing.id,
                source_field_name="7. Plan Year Ending Date",
                normalized_field_name="plan_year_end",
                mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                mapped_label="7. Plan Year Ending Date",
                form_type=FormType.FORM_5500,
                source_document_type=DocumentType.PLAN_WORKSHEET,
                priority=FieldPriority.LOW,
                value="2025-12-31",
                proposed_value="2025-12-31",
            ),
            ExtractedField(
                filing_id=filing.id,
                source_field_name="1a. Name of Insurance Company",
                normalized_field_name="carrier",
                mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                mapped_label="1a. Name of Insurance Company",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="Performance Insurance",
                proposed_value="Performance Insurance",
            ),
        ]
    )
    return filing


class FTWilliamsPerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        repositories._repository = repositories.MemoryRepository()
        clear_ftw_current_snapshot_cache()

    async def asyncTearDown(self):
        clear_ftw_current_snapshot_cache()
        repositories._repository = None

    async def test_schedule_slots_are_queried_with_bounded_concurrency(self):
        filing = await create_filing_with_identity()
        ftw = SlowCountingFTWilliamsService()

        await FTWilliamsReviewService(ftw).prepare_review(filing.id, send_queries=True)

        schedule_calls = [call for call in ftw.calls if call.operation == "query_schedule_a"]
        self.assertEqual(len(schedule_calls), 21)
        self.assertGreater(ftw.max_active_schedule_queries, 1)
        self.assertLessEqual(ftw.max_active_schedule_queries, 5)

    async def test_same_plan_year_reuses_one_current_data_snapshot(self):
        first = await create_filing_with_identity()
        second = await create_filing_with_identity()
        ftw = SlowCountingFTWilliamsService()
        first_review, second_review = await asyncio.gather(
            FTWilliamsReviewService(ftw).prepare_review(
                first.id,
                send_queries=True,
                reuse_current_snapshot=True,
            ),
            FTWilliamsReviewService(ftw).prepare_review(
                second.id,
                send_queries=True,
                reuse_current_snapshot=True,
            ),
        )

        current_5500_calls = [call for call in ftw.calls if call.operation == "query_5500"]
        plan_calls = [call for call in ftw.calls if call.operation == "query_plan"]
        schedule_calls = [call for call in ftw.calls if call.operation == "query_schedule_a"]
        self.assertEqual(len(plan_calls), 1)
        self.assertEqual(len(current_5500_calls), 1)
        self.assertEqual(len(schedule_calls), 21)
        self.assertEqual(first_review.current_query_success, second_review.current_query_success)
        first_values = [
            (
                field.rule_key,
                field.current_value,
                field.extracted_value,
                field.proposed_value,
                field.update_included,
            )
            for field in first_review.fields
        ]
        second_values = [
            (
                field.rule_key,
                field.current_value,
                field.extracted_value,
                field.proposed_value,
                field.update_included,
            )
            for field in second_review.fields
        ]
        self.assertEqual(first_values, second_values)
        first_filing = await repositories.get_repository().get_filing(first.id)
        second_filing = await repositories.get_repository().get_filing(second.id)
        self.assertEqual(first_filing.proposed_xml, second_filing.proposed_xml)


if __name__ == "__main__":
    unittest.main()
