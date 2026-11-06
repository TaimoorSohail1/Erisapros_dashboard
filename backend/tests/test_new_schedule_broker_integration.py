"""Exercise new-record broker review, dispatch and read-back without vendor I/O."""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from app.models import (BrokerRosterReviewRequest, FormType, FTWilliamsScheduleABrokerRowsRequest,
    FTWilliamsScheduleAMatchRequest, ScheduleABrokerRow, ScheduleABrokerRosterReview)
from app.services.ftwilliams_review import FTWilliamsReviewService
import test_ftw_update_preservation_integration as integration


class CreatingSavedValueVendor(integration.SavedValueVendor):
    def __init__(self):
        super().__init__()
        self.brokers = {}

    async def run_query(self, payload):
        result = await super().run_query(payload)
        if payload.operation == 'query_schedule_a':
            for status in result.statuses:
                status.query_subparts = deepcopy(self.brokers.get(status.ftw_seq_no, {}))
        return result

    async def send_xml(self, operation, request_xml):
        # Only this deterministic vendor boundary assigns new sequences.
        controls = {'TransactionType','EditCheck','FTWCustomerID','FTWPlanID','FTWSeqNo','Year','Type','DOLSubPartData'}
        documents = ET.fromstring(request_xml).findall('./DataBatch/DOLScheduleAData')
        for node in documents:
            desc = node.findtext('ScheduleDesc')
            if not any(v['ScheduleDesc'] == desc for v in self.schedules.values()):
                seq = str(max(map(int,self.schedules),default=0)+1)
                self.schedules[seq] = {'ScheduleDesc':desc}
        result = await super().send_xml(operation, request_xml)
        for node in documents:
            seq = next(k for k,v in self.schedules.items() if v['ScheduleDesc'] == node.findtext('ScheduleDesc'))
            self.schedules[seq] = {c.tag:c.text or '' for c in node if c.tag not in controls}
            rows = [{c.tag:c.text or '' for c in row} for row in node.findall('./DOLSubPartData/Broker')]
            for values in [self.schedules[seq],*rows]:
                for tag in list(values):
                    if tag in {'InsBrokerCommTotAmt','InsBrokerFeesTotAmt','WlfrTotChargesPaidAmt'} or tag.startswith(('CommPdAmt','FeesPdAmt')):
                        values[tag] = str(Decimal(values[tag]).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            self.brokers[seq] = {'Broker':rows} if rows else {}
        return result


class NewScheduleBrokerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await integration.UpdatePreservationIntegrationTests.asyncSetUp(self)
        self.vendor = CreatingSavedValueVendor()
        self.service = FTWilliamsReviewService(self.vendor)
        self.source = b'QA verified new source'
        self.row = ScheduleABrokerRow(name='Source Broker',address_line_1='10 Main St',city='Boston',state='MA',
            zip_code='02110',organization_code='3',commission_total='100.12',fee_total='25.22')
        await self.repo.update_filing(self.filing.id,{'schedule_a_broker_rows':[self.row],
            'schedule_a_broker_roster':ScheduleABrokerRosterReview(source_sha256=hashlib.sha256(self.source).hexdigest())})
        for key,value in [('schedule_a_part_i_1d_contract_policy_number','NEW-333'),
            ('schedule_a_part_i_1b_insurance_carrier_ein','11-1111111'),
            ('form_5500_part_ii_14_active_participants_at_end','100'),
            ('form_5500_part_ii_12_total_participants_at_end_of_year','200')]:
            self.fields[key].value = self.fields[key].proposed_value = value
        await self.repo.replace_fields(self.filing.id,list(self.fields.values()))
        self.source_patch = patch('app.services.storage.StorageService.load_pdf',return_value=self.source)
        self.source_patch.start();self.addCleanup(self.source_patch.stop)

    async def asyncTearDown(self):
        await integration.UpdatePreservationIntegrationTests.asyncTearDown(self)

    async def test_new_broker_approval_creates_one_record_verifies_values_and_repeat_does_not_duplicate(self):
        original_parent = deepcopy(self.vendor.parent)
        originals = deepcopy(self.vendor.schedules)
        review = await self.service.prepare_review(self.filing.id,send_queries=True)
        review = await self.service.select_schedule_a_match(self.filing.id,FTWilliamsScheduleAMatchRequest(
            create_new=True,confirm_new_target=True,carrier='Example Insurance Company',carrier_ein='11-1111111',contract='NEW-333'))
        self.assertTrue(review.schedule_a_match.get('create_new'),review.error_message)
        self.assertIsNone(review.ftw_seq_no)
        self.assertTrue(review.schedule_a_match['new_target_confirmed'])
        self.assertTrue(review.schedule_a_broker_roster.destination_ready,review.schedule_a_broker_roster.destination_reason)
        review = await self.service.update_schedule_a_broker_rows(self.filing.id,
            FTWilliamsScheduleABrokerRowsRequest(rows=[self.row],action='reviewed',review=BrokerRosterReviewRequest(
                expected_context=review.schedule_a_broker_roster.review_context_sha256,
                complete_list_confirmed=True,reason='Verified source list for disposable new record',row_sources=['SOURCE:0'])),
            actor='qa-reviewer')
        self.assertEqual(review.schedule_a_broker_roster.mode,'REPLACE')
        result = await self.service.approve_and_update(self.filing.id,send_to_ftw=True,include_broker_updates=True)
        self.assertTrue(result.update_verification_success,result.error_message)
        self.assertEqual(len(self.vendor.schedules),len(originals)+1)
        new_seq = next(k for k in self.vendor.schedules if k not in originals)
        values = self.vendor.schedules[new_seq]
        self.assertEqual(values['InsContractNum'],'NEW-333')
        self.assertEqual(values['InsBrokerCommTotAmt'],'100')
        self.assertEqual(values['InsBrokerFeesTotAmt'],'25')
        self.assertEqual(values['PlanName'],original_parent['PlanName'].upper())
        paid = self.vendor.brokers[new_seq]['Broker']
        self.assertEqual(len(paid),1)
        self.assertEqual(paid[0]['FeesPdTextXX'],'COMMISSIONS AND FEES')
        self.assertEqual(paid[0]['CommPdAmtXX'],'100')
        self.assertEqual(paid[0]['FeesPdAmtXX'],'25')
        self.assertEqual(self.vendor.parent,original_parent)
        for seq in originals:self.assertEqual(self.vendor.schedules[seq],originals[seq])
        refreshed = await self.service.prepare_review(self.filing.id,send_queries=True)
        self.assertEqual(str(refreshed.ftw_seq_no),new_seq)
        self.assertFalse(refreshed.schedule_a_match.get('create_new'))
        self.assertEqual(refreshed.schedule_a_broker_roster.mode,'REPLACE')
        self.assertFalse(self.service._review_has_broker_updates(refreshed))
        sent_count = len(self.vendor.sent)
        await self.service.approve_and_update(self.filing.id,send_to_ftw=True,include_broker_updates=True)
        self.assertEqual(len(self.vendor.schedules),len(originals)+1)
        self.assertEqual(len(self.vendor.sent),sent_count)
