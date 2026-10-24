from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.repositories as repositories
from app.models import FTWilliamsReview, FTWilliamsQueryResponse, FTWilliamsStatusItem, FTWilliamsUpdateReceipt
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.ftwilliams import FTWilliamsService


class VerifiedEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        repositories._repository = repositories.MemoryRepository()
        self.service = FTWilliamsReviewService(FTWilliamsService())

    async def asyncTearDown(self):
        repositories._repository = None

    async def test_actual_rounded_saved_value_survives_readback(self):
        review = FTWilliamsReview(filing_id='filing', ftw_customer_id='customer', ftw_plan_id='plan',
            year='2025', ftw_seq_no='2', schedule_a_records=[{'ftw_seq_no':'2'}],
            update_xml_schedule_a='<ftwLink><DataBatch><DOLScheduleAData><InsContractNum>000123</InsContractNum><WlfrTotChargesPaidAmt>100.49</WlfrTotChargesPaidAmt></DOLScheduleAData></DataBatch></ftwLink>')
        self.service._query_schedule_a_readback = AsyncMock(return_value=([
            FTWilliamsStatusItem(type='ScheduleA', error_code='0', ftw_seq_no='2',
                query_results={'InsContractNum':'000123','WlfrTotChargesPaidAmt':'100'})], ['request'], ['response'], None))
        result = await self.service._verify_update_readback(review)
        self.assertTrue(result['success'])
        self.assertEqual(result['observations'][0]['values']['WlfrTotChargesPaidAmt'], '100')
        review.update_readback_observations = result['observations']
        self.assertEqual(self.service.literal_readback_value(review, {'form_type':'SCHEDULE_A','tag':'WlfrTotChargesPaidAmt'}), '100')
        self.assertIsNone(self.service.literal_readback_value(review, {'form_type':'SCHEDULE_A','tag':'MissingTag'}))

    async def test_same_tag_in_other_record_or_parent_cannot_supply_saved_value(self):
        review = FTWilliamsReview(filing_id='filing', ftw_seq_no='2', update_readback_observations=[
            {'form_type':'FORM_5500','values':{'Name':'parent'}},
            {'form_type':'SCHEDULE_A','expected_sequence':'1','values':{'Name':'sibling'}},
            {'form_type':'SCHEDULE_A','expected_sequence':'2','values':{'Name':''}}])
        self.assertEqual(self.service.literal_readback_value(review, {'form_type':'SCHEDULE_A','tag':'Name'}), '')
        self.assertEqual(self.service.literal_readback_value(review, {'form_type':'FORM_5500','tag':'Name'}), 'parent')

    async def test_dispatch_intent_contains_actual_selected_xml_without_credential(self):
        repo = repositories.get_repository()
        async def send(operation, xml):
            audit = (await repo.list_audit_logs('filing'))[-1]
            self.assertEqual(audit.event, 'FTWILLIAMS_DISPATCH_INTENT')
            self.assertNotIn('private-test-key', str(audit.details))
            self.assertIn('<InsContractNum>000123</InsContractNum>', audit.details['xml'])
            return FTWilliamsQueryResponse(operation=operation, configured=True, sent=True,
                request_xml='<masked-request />', success=True, http_status=200,
                raw_response='<ftwLinkResponse><Status ErrorCode="0" /></ftwLinkResponse>')
        self.service.ftwilliams.send_xml = send
        review = FTWilliamsReview(filing_id='filing', ftw_customer_id='customer', ftw_plan_id='plan', year='2025', ftw_seq_no='2',
            update_xml_schedule_a='<ftwLink><KeyID>private-test-key</KeyID><DataBatch><DOLScheduleAData><InsContractNum>000123</InsContractNum></DOLScheduleAData></DataBatch></ftwLink>')
        responses = await self.service._send_update_payload(review)
        self.assertEqual(len(responses), 1)

    async def test_completed_summary_uses_verified_outcome_not_remaining_changes(self):
        review = FTWilliamsReview(filing_id='filing', status='UPDATE_SENT',
            update_verification_attempted=True, update_verification_success=True, update_confirmed_count=5, update_remaining_count=0,
            update_receipt=FTWilliamsUpdateReceipt(receipt_id='receipt', action='SCHEDULE_A_UPDATED',
                verified_at='2026-10-24T00:00:00', year='2025', ftw_seq_no='2'))
        summary = self.service.verified_outcome_summary(review)
        self.assertEqual(summary['verified_update_count'], 5)
        review.update_verification_success = False
        self.assertIsNone(self.service.verified_outcome_summary(review)['verified_update_count'])
