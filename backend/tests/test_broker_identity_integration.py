"""Identity save/reload -> separate Approve -> fake-vendor saved-value verification."""
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock,patch
from starlette.requests import Request
from app.models import (BrokerIdentityReviewRequest,BrokerRosterReviewRequest,ExtractionValidationResult,
    FTWilliamsScheduleABrokerRowsRequest,FTWilliamsScheduleAMatchRequest,ScheduleABrokerRow)
from app.api.filings import update_ftwilliams_schedule_a_broker_rows
import test_new_schedule_broker_integration as integration


class BrokerIdentityIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await integration.NewScheduleBrokerIntegrationTests.asyncSetUp(self)
        hold=ExtractionValidationResult(validator='recipient_identity_ambiguity',status='ERROR',reason='Confirm different printed addresses')
        self.rows=[ScheduleABrokerRow(name='Mercer',address_line_1='4565 PAYSPHERE CIR',city='CHICAGO',state='IL',zip_code='60674',
            organization_code='3',commission_total='4035.74',fee_total='0',validation_results=[hold]),
            ScheduleABrokerRow(name='Mercer',address_line_1='4565 PAYSPHERE CIRCLE',city='CHICAGO',state='IL',zip_code='60674',
            organization_code='3',commission_total='0',fee_total='1133.09',validation_results=[hold.model_copy(deep=True)])]
        await self.repo.update_filing(self.filing.id,{'schedule_a_broker_rows':self.rows})

    async def asyncTearDown(self):
        await integration.NewScheduleBrokerIntegrationTests.asyncTearDown(self)

    async def run_choice(self,choice):
        before=deepcopy(self.vendor.schedules);parent=deepcopy(self.vendor.parent)
        await self.service.prepare_review(self.filing.id,send_queries=True)
        review=await self.service.select_schedule_a_match(self.filing.id,FTWilliamsScheduleAMatchRequest(
            create_new=True,confirm_new_target=True,carrier='Example Insurance Company',carrier_ein='11-1111111',contract='NEW-333'))
        group=review.schedule_a_broker_roster.identity_groups[0]
        payload=FTWilliamsScheduleABrokerRowsRequest(action='identity_reviewed',identity_review=BrokerIdentityReviewRequest(
            expected_context=review.schedule_a_broker_roster.review_context_sha256,group_id=group['id'],choice=choice,
            address_source_index=1 if choice=='SAME' else None,verified=True,reason='QA reviewer verified source recipient identity'))
        request=Request({'type':'http','state':{'user':{'sub':'qa-reviewer'}}})
        with patch('app.api.filings.FTWilliamsReviewService',return_value=self.service),patch('app.api.filings.continue_ftw_automation',AsyncMock()) as automatic:
            await update_ftwilliams_schedule_a_broker_rows(self.filing.id,payload,request)
        automatic.assert_not_awaited()
        stored=await self.repo.get_filing(self.filing.id)
        self.assertEqual(stored.schedule_a_broker_rows,self.rows,'Identity save alone does not transform source rows')
        self.assertIsNone(stored.schedule_a_broker_roster.reviewer_confirmation)
        self.assertEqual(self.vendor.sent,[],'Saving identity cannot send any FTWilliam updates')
        reloaded=await self.service.prepare_review(self.filing.id,send_queries=True)
        self.assertEqual(reloaded.schedule_a_broker_roster.identity_groups[0]['choice'],choice)
        reloaded=await self.service.update_schedule_a_broker_rows(self.filing.id,FTWilliamsScheduleABrokerRowsRequest(
            action='reviewed',rows=reloaded.schedule_a_broker_rows,review=BrokerRosterReviewRequest(
                expected_context=reloaded.schedule_a_broker_roster.review_context_sha256,complete_list_confirmed=True,
                reason='QA reviewer approved final broker roster',row_sources=['SOURCE:0','SOURCE:1'])),actor='qa-reviewer')
        self.assertEqual(reloaded.schedule_a_broker_roster.mode,'REPLACE')
        self.assertEqual(len(reloaded.schedule_a_broker_rows),1 if choice=='SAME' else 2)
        self.assertEqual(self.vendor.sent,[],'Broker review is separate from an explicit vendor dispatch in this test')
        result=await self.service.approve_and_update(self.filing.id,send_to_ftw=True,include_broker_updates=True)
        self.assertTrue(result.update_verification_success,result.error_message)
        sequence=next(k for k in self.vendor.schedules if k not in before)
        brokers=self.vendor.brokers[sequence]['Broker']
        self.assertEqual(len(brokers),1 if choice=='SAME' else 2)
        self.assertEqual(self.vendor.schedules[sequence]['InsBrokerCommTotAmt'],'4036')
        self.assertEqual(self.vendor.schedules[sequence]['InsBrokerFeesTotAmt'],'1133')
        if choice=='SAME':
            self.assertEqual(brokers[0]['AddressLine1XX'],'4565 PAYSPHERE CIRCLE')
            self.assertEqual(brokers[0]['FeesPdTextXX'],'COMMISSIONS AND FEES')
        else:
            self.assertEqual({b['AddressLine1XX'] for b in brokers},{'4565 PAYSPHERE CIR','4565 PAYSPHERE CIRCLE'})
            self.assertEqual({b['FeesPdTextXX'] for b in brokers},{'COMMISSIONS','FEES'})
        self.assertEqual(self.vendor.parent,parent)
        for seq in before:self.assertEqual(self.vendor.schedules[seq],before[seq])
        refreshed=await self.service.prepare_review(self.filing.id,send_queries=True)
        self.assertEqual(refreshed.schedule_a_broker_roster.mode,'REPLACE')
        self.assertFalse(self.service._review_has_broker_updates(refreshed))
        sends=len(self.vendor.sent)
        await self.service.approve_and_update(self.filing.id,send_to_ftw=True,include_broker_updates=True)
        self.assertEqual(len(self.vendor.sent),sends)

    async def test_same_broker_save_reload_approve_and_saved_readback(self):await self.run_choice('SAME')
    async def test_separate_brokers_save_reload_approve_and_saved_readback(self):await self.run_choice('SEPARATE')

    async def test_identity_save_rejects_stale_source_unauthenticated_and_concurrent_saves(self):
        await self.service.prepare_review(self.filing.id,send_queries=True)
        review=await self.service.select_schedule_a_match(self.filing.id,FTWilliamsScheduleAMatchRequest(
            create_new=True,confirm_new_target=True,carrier='Example Insurance Company',carrier_ein='11-1111111',contract='NEW-333'))
        payload=FTWilliamsScheduleABrokerRowsRequest(action='identity_reviewed',identity_review=BrokerIdentityReviewRequest(
            expected_context=review.schedule_a_broker_roster.review_context_sha256,
            group_id=review.schedule_a_broker_roster.identity_groups[0]['id'],choice='SEPARATE',verified=True,reason='QA source check'))
        with self.assertRaisesRegex(ValueError,'Authenticated'):
            await self.service.update_schedule_a_broker_rows(self.filing.id,payload,actor=None)
        with patch('app.services.storage.StorageService.load_pdf',return_value=b'changed source'),self.assertRaisesRegex(ValueError,'Source PDF changed'):
            await self.service.update_schedule_a_broker_rows(self.filing.id,payload,actor='qa-reviewer')
        with patch.object(self.repo,'save_broker_review',AsyncMock(return_value=False)),self.assertRaisesRegex(ValueError,'Filing changed'):
            await self.service.update_schedule_a_broker_rows(self.filing.id,payload,actor='qa-reviewer')
        stored=await self.repo.get_filing(self.filing.id)
        self.assertFalse(stored.schedule_a_broker_roster.identity_reviews)
        self.assertEqual(self.vendor.sent,[])
