"""Dependency-free test runner for scoped production image overlays."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.models import FTWilliamsQueryResponse, FTWilliamsStatusItem, NormalizedExtractionResult
from app.services.extractor import bcbsma_column_value, extract_columnar_broker_compensation_rows
from app.services.ftwilliams_local_agent_jobs import FTWLocalAgentService
from app.services.schedule_a_customer_rules import apply_customer_defaults


class OxfordReleaseSmoke(unittest.TestCase):
    def test_current_year_identity_does_not_require_an_action_url(self):
        url = "https://ftwilliam.com/cgi-bin/index.cgi?#plan=11,22&Year=2025"
        job = SimpleNamespace(id="job", filing_id="filing", target_url=url, expected_year="2025",
            expected_ein="87-4619719", expected_plan_number="501", expected_account="HighlandTech",
            operation_dispatched_at=None, result_state=None)
        review = SimpleNamespace(ftw_browser_customer_id="11", ftw_browser_plan_id="22",
            browser_mapping_confirmed=True, ftw_customer_id="11", ftw_plan_id="22", year="2025",
            plan_lookup=SimpleNamespace(company_employer_id="87-4619719", plan_number="501"))
        refreshed = SimpleNamespace(**vars(review), current_query_success=True, current_query_complete=True,
            current_year_exists=True, bring_forward_required=False, ftw_plan_url=None)
        repo = SimpleNamespace(upsert_ftwilliams_review=AsyncMock(), update_ftw_local_agent_job=AsyncMock(), add_audit=AsyncMock())
        service = FTWLocalAgentService(repo=repo, review_service=SimpleNamespace(prepare_review=AsyncMock(return_value=refreshed)))
        service._hold_preflight = AsyncMock(return_value=False)
        answer = FTWilliamsQueryResponse(operation="query_schedule_a", configured=True, sent=True, request_xml="",
            http_status=200, success=True, statuses=[FTWilliamsStatusItem(error_code="0", query_result_record_count=1,
                query_results={"ContractNum": "918412", "FTWSeqNo": "1"})])
        with patch("app.services.ftwilliams.FTWilliamsService.run_query", new=AsyncMock(return_value=answer)), \
                patch("app.services.ftwilliams.FTWilliamsService._close_client", new=AsyncMock()), \
                patch("app.services.ftwilliams_automation.FTWAutomationService.run", new=AsyncMock()):
            self.assertFalse(asyncio.run(service._authorize_fresh_bring_forward(job, review)))
        service._hold_preflight.assert_not_awaited()
        self.assertEqual(repo.update_ftw_local_agent_job.call_args.args[1]["result_state"], "NO_LONGER_REQUIRED")

    def test_decimal_precision(self):
        self.assertEqual(bcbsma_column_value("Total Premium 1,630,230.90 0.00 0.00", "Total Premium", "MEDICAL"), "1,630,230.90")
        self.assertEqual(bcbsma_column_value("Claims Charged 1422413.5874 0 0", "Claims Charged", "MEDICAL"), "1422413.5874")

    def test_leading_decimal_fees(self):
        text = "5. INSURANCE FEES AND COMMISSION INFORMATION:\nNAME AND ADDRESS SALES COMMISSIONS FEES ADDITIONAL COMPENSATION\n\nRSC INSURANCE BROKERAGE INC 5,349.89 .00 617.87\n4TH FLOOR\n160 FEDERAL STREET\nBOSTON MA 02110\n\n4. COVERAGE/BENEFITS\n"
        rows = extract_columnar_broker_compensation_rows([(1, text)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].fee_total, "617.87")

    def test_missing_code_does_not_invent_a_broker(self):
        result = NormalizedExtractionResult(provider="QA", fields=[])
        apply_customer_defaults(result)
        self.assertEqual(result.fields[0].value, "3")
        self.assertFalse(result.schedule_a_broker_rows)

    def check_guard(self, code, prior=False):
        url = "https://ftwilliam.com/cgi-bin/index.cgi?#plan=11,22&Year=2025"
        job = SimpleNamespace(id="job", target_url=url, expected_year="2025", expected_ein="87-4619719",
            expected_plan_number="501", expected_account="HighlandTech", operation_dispatched_at=None, result_state=None)
        review = SimpleNamespace(ftw_browser_customer_id="11", ftw_browser_plan_id="22", browser_mapping_confirmed=True,
            ftw_customer_id="11", ftw_plan_id="22", year="2025",
            plan_lookup=SimpleNamespace(company_employer_id="87-4619719", plan_number="501"))
        repo = SimpleNamespace(list_ftw_target_operation_history=AsyncMock(return_value=[SimpleNamespace(
            id="prior", target_url=url)] if prior else []))
        service = FTWLocalAgentService(repo=repo)
        service._hold_preflight = AsyncMock(return_value=False)
        answer = FTWilliamsQueryResponse(operation="query_schedule_a", configured=True, sent=True, request_xml="",
            http_status=200, success=False, statuses=[FTWilliamsStatusItem(error_code=code)])
        with patch("app.services.ftwilliams.FTWilliamsService.run_query", new=AsyncMock(return_value=answer)), \
                patch("app.services.ftwilliams.FTWilliamsService._close_client", new=AsyncMock()) as close:
            allowed = asyncio.run(service._authorize_fresh_bring_forward(job, review))
            close.assert_awaited_once()
        return allowed, service._hold_preflight

    def test_fresh_missing_can_authorize_first_operation(self):
        allowed, hold = self.check_guard("59")
        self.assertTrue(allowed)
        hold.assert_not_awaited()

    def test_invalid_fresh_query_cannot_authorize(self):
        allowed, hold = self.check_guard("55")
        self.assertFalse(allowed)
        self.assertEqual(hold.call_args.args[1], "CURRENT_QUERY_REQUIRED")

    def test_prior_operation_prevents_repeat(self):
        allowed, hold = self.check_guard("59", prior=True)
        self.assertFalse(allowed)
        self.assertEqual(hold.call_args.args[1], "PRIOR_OPERATION_UNCONFIRMED")


if __name__ == "__main__":
    unittest.main()
