"""Manual Send -> exact vendor payload -> literal saved roster, without vendor I/O."""
from copy import deepcopy
import unittest
from app.models import (ScheduleABrokerRow,FTWilliamsScheduleAMatchRequest,FTWilliamsScheduleABrokerRowsRequest,
    BrokerRosterReviewRequest,FTWilliamsSendUpdateRequest)
import test_new_schedule_broker_integration as integration

class BrokerSelectedSendIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await integration.NewScheduleBrokerIntegrationTests.asyncSetUp(self)
        self.row=ScheduleABrokerRow(name='MERCER HEALTH & BENEFITS LLC',address_line_1='4565 PAYSPHERE CIR',
            city='CHICAGO',state='IL',zip_code='60674',organization_code='3',commission_total='4035.74',fee_total='1133.09')
        await self.repo.update_filing(self.filing.id,{'schedule_a_broker_rows':[self.row]})
        for key,value in [('schedule_a_part_i_1d_contract_policy_number','POLICY-02'),
                          ('schedule_a_part_i_1b_insurance_carrier_ein','98-7654321')]:
            self.fields[key].value=self.fields[key].proposed_value=value
        await self.repo.replace_fields(self.filing.id,list(self.fields.values()))
        self.vendor.schedules['2'].update(InsBrokerCommTotAmt='0',InsBrokerFeesTotAmt='0')
        self.vendor.brokers['2']={'Broker':[{'NameXX':'MERCER HEALTH AND BENEFITS, LLC','AddressLine1XX':'4565 PAYSPHERE CIRCLE',
            'CityXX':'CHICAGO','StateXX':'IL','ZipCodeXX':'60674','FeesPdTextXX':'BROKER BONUS','CodeXX':'3'}]}
        await self.service.prepare_review(self.filing.id,send_queries=True)
        review=await self.service.select_schedule_a_match(self.filing.id,FTWilliamsScheduleAMatchRequest(ftw_seq_no='2'))
        review=await self.service.update_schedule_a_broker_rows(self.filing.id,FTWilliamsScheduleABrokerRowsRequest(
            action='reviewed',rows=[self.row],review=BrokerRosterReviewRequest(
                expected_context=review.schedule_a_broker_roster.review_context_sha256,complete_list_confirmed=True,
                reason='Isolated QA approved source roster',row_sources=['SOURCE:0'])),actor='qa-reviewer')
        self.assertEqual(review.schedule_a_broker_roster.mode,'REPLACE')
        self.before_schedules=deepcopy(self.vendor.schedules);self.before_brokers=deepcopy(self.vendor.brokers)
        self.before_parent=deepcopy(self.vendor.parent)

    async def asyncTearDown(self):
        await integration.NewScheduleBrokerIntegrationTests.asyncTearDown(self)

    async def test_manual_send_includes_approved_brokers_and_preserves_parent_and_sibling(self):
        field=self.fields['schedule_a_part_i_1e_persons_covered_end_of_policy_year']
        result=await self.service.send_approved_update(self.filing.id,FTWilliamsSendUpdateRequest(
            selected_field_ids=[field.id],include_broker_updates=True))
        self.assertTrue(result.update_verification_success,result.error_message)
        self.assertEqual(self.vendor.schedules['2']['InsBrokerCommTotAmt'],'4036')
        self.assertEqual(self.vendor.schedules['2']['InsBrokerFeesTotAmt'],'1133')
        saved=self.vendor.brokers['2']['Broker'];self.assertEqual(len(saved),1)
        self.assertEqual(saved[0]['CommPdAmtXX'],'4036');self.assertEqual(saved[0]['FeesPdAmtXX'],'1133')
        self.assertEqual(saved[0]['FeesPdTextXX'],'COMMISSIONS AND FEES')
        self.assertEqual(saved[0]['AddressLine1XX'],'4565 PAYSPHERE CIR')
        self.assertEqual(self.vendor.schedules['1'],self.before_schedules['1'])
        self.assertEqual(self.vendor.parent,self.before_parent)
        stored=await self.repo.get_filing(self.filing.id)
        self.assertTrue(stored.schedule_a_broker_roster.reviewer_confirmation['verified_update'])
        refreshed=await self.service.prepare_review(self.filing.id,send_queries=True)
        self.assertFalse(refreshed.broker_updates_pending)

    async def test_explicit_opt_out_preserves_old_brokers_and_does_not_verify_approved_list(self):
        field=self.fields['schedule_a_part_i_1e_persons_covered_end_of_policy_year']
        result=await self.service.send_approved_update(self.filing.id,FTWilliamsSendUpdateRequest(
            selected_field_ids=[field.id],include_broker_updates=False))
        self.assertTrue(result.update_verification_success,result.error_message)
        self.assertEqual(self.vendor.brokers['2'],self.before_brokers['2'])
        stored=await self.repo.get_filing(self.filing.id)
        self.assertNotIn('verified_update',stored.schedule_a_broker_roster.reviewer_confirmation)
        self.assertTrue(result.broker_updates_pending)
        self.assertFalse(result.broker_update_verified)

    async def test_broker_only_send_has_a_verified_receipt_and_survives_reload(self):
        result=await self.service.send_approved_update(self.filing.id,FTWilliamsSendUpdateRequest(
            selected_field_ids=[],include_broker_updates=True))
        self.assertTrue(result.update_verification_success,result.error_message)
        self.assertIsNotNone(result.update_receipt,'A broker-only write must have a verified receipt without inventing scalar field counts')
        refreshed=await self.service.prepare_review(self.filing.id,send_queries=True)
        self.assertIsNotNone(refreshed.update_receipt)
        self.assertFalse(refreshed.broker_updates_pending)
        self.assertTrue(refreshed.broker_update_verified)
        self.assertEqual(refreshed.update_attempted_count,0)

    async def test_explicitly_verified_empty_list_can_be_sent_and_verified(self):
        stored=await self.repo.get_filing(self.filing.id)
        status=stored.schedule_a_broker_roster.model_copy(deep=True)
        status.reviewer_confirmation=None
        await self.repo.update_filing(self.filing.id,{'schedule_a_broker_rows':[],'schedule_a_broker_roster':status})
        review=await self.service.prepare_review(self.filing.id,send_queries=True)
        await self.service.update_schedule_a_broker_rows(self.filing.id,FTWilliamsScheduleABrokerRowsRequest(
            action='reviewed',rows=[],review=BrokerRosterReviewRequest(
                expected_context=review.schedule_a_broker_roster.review_context_sha256,
                complete_list_confirmed=True,reason='Isolated explicitly empty source fixture',
                empty_list_evidence='Synthetic source explicitly confirms no reportable brokers',row_sources=[])),actor='qa-reviewer')
        result=await self.service.send_approved_update(self.filing.id,FTWilliamsSendUpdateRequest(
            selected_field_ids=[],include_broker_updates=True))
        self.assertTrue(result.update_verification_success,result.error_message)
        self.assertTrue(result.broker_update_verified)
        self.assertIsNotNone(result.update_receipt)
        self.assertFalse(self.vendor.brokers['2'])
        self.assertEqual(self.vendor.schedules['2']['InsBrokerCommTotAmt'],'0')
        self.assertEqual(self.vendor.schedules['2']['InsBrokerFeesTotAmt'],'0')
