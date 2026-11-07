"""A verified own write advances review binding; unrelated changes never do."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from app.models import Filing, FTWilliamsReview, ScheduleABrokerRow, ScheduleABrokerRosterReview, BrokerRosterReviewRequest
from app.repositories import MemoryRepository
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.broker_review_confirmation import confirm_broker_review
from app.services.schedule_a_broker_roster import bind_roster_review


class VerifiedBrokerReviewUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = MemoryRepository()
        self.service = FTWilliamsReviewService()
        self.row = ScheduleABrokerRow(name='Current Broker', address_line_1='10 Main St', city='Boston', state='MA',
            zip_code='02110', organization_code='3', commission_total='100.12', fee_total='0', purpose='COMMISSIONS')
        self.filing = await self.repo.create_filing(Filing(file_name='qa.pdf', content_type='application/pdf', file_size=1,
            s3_key='qa/source.pdf', schedule_a_broker_rows=[self.row], schedule_a_broker_roster=ScheduleABrokerRosterReview(source_sha256='a'*64)))
        self.identity = dict(ftw_customer_id='qa-customer', ftw_plan_id='qa-plan', year='2025', ftw_seq_no='2')
        self.before = [{'ftw_seq_no':'2', 'query_results':{'ScheduleDesc':'QA', 'InsContractNum':'913136'},
            'query_subparts':{'Broker':[{'Name1':'Obsolete Broker'}]}}]
        context = self.service.source_context_sha256(self.filing)
        bound = bind_roster_review([self.row], self.filing.schedule_a_broker_roster, self.before, self.identity, True, context)
        request = BrokerRosterReviewRequest(expected_context=bound.review_context_sha256, complete_list_confirmed=True,
            reason='Source confirms complete list', row_sources=['SOURCE:0'])
        rows, confirmed = confirm_broker_review([self.row], [self.row], bound, request,
            actor='reviewer-sub', source_context=context, snapshot=bound.current_snapshot_sha256)
        await self.repo.update_filing(self.filing.id, {'schedule_a_broker_rows':rows, 'schedule_a_broker_roster':confirmed})
        status = bind_roster_review(rows, confirmed, self.before, self.identity, True, context)
        self.sent = FTWilliamsReview(filing_id=self.filing.id, source_context_sha256=context, **self.identity,
            current_query_success=True, current_query_complete=True, schedule_a_records=self.before,
            schedule_a_broker_rows=rows, schedule_a_broker_roster=status)
        self.sent.update_xml_schedule_a = self.service._build_schedule_a_update_xml([], self.before, '2',
            self.service._identity_from_review(self.sent), schedule_a_broker_rows=rows, replace_broker_roster=True)
        document = self.service._update_documents(self.sent.update_xml_schedule_a, 'DOLScheduleAData')[0]
        self.after = deepcopy(self.before)
        self.after[0]['query_results'].update({'InsBrokerCommTotAmt':'100', 'InsBrokerFeesTotAmt':'0', 'OverrideCommissionsAndFees':'0'})
        self.after[0]['query_subparts'] = {'Broker':[{'Name1':'Current Broker','AddressLine101':'10 Main St','City01':'Boston',
            'State01':'MA','ZipCode01':'02110','Code01':'3','CommPdAmt01':'100','FeesPdAmt01':'0','FeesPdText01':'COMMISSIONS'}]}
        self.document = document
        self.current = self.sent.model_copy(deep=True)
        self.current.schedule_a_records = self.after
        self.current.schedule_a_broker_roster = bind_roster_review(rows, confirmed, self.after, self.identity, True, context)
        self.observations = [{'form_type':'SCHEDULE_A','expected_sequence':'2','actual_sequence':'2',
            'values':deepcopy(self.after[0]['query_results']), 'broker_rows':deepcopy(self.after[0]['query_subparts'])}]

    async def acknowledge(self, **changes):
        with patch('app.services.ftwilliams_review.get_repository', return_value=self.repo):
            return await self.service._acknowledge_verified_broker_review(self.filing.id, self.sent, self.current,
                changes.get('observations',self.observations))

    async def test_verified_own_update_remains_reviewed_and_repeat_is_noop(self):
        self.assertEqual(self.current.schedule_a_broker_roster.mode, 'PRESERVE')
        self.assertTrue(await self.acknowledge())
        filing = await self.repo.get_filing(self.filing.id)
        status = bind_roster_review(filing.schedule_a_broker_rows, filing.schedule_a_broker_roster,
            self.after, self.identity, True, self.service.source_context_sha256(filing))
        self.assertEqual(status.mode,'REPLACE')
        self.assertEqual(status.reviewer_confirmation['actor'],'reviewer-sub')
        self.assertEqual(status.reviewer_confirmation['reason'],'Source confirms complete list')
        self.assertIn('verified_update', status.reviewer_confirmation)
        self.current.schedule_a_broker_roster = status
        self.assertFalse(self.service._review_has_broker_updates(self.current))
        changed = deepcopy(self.after); changed[0]['query_subparts']['Broker'][0]['CommPdAmt01']='999'
        self.assertEqual(bind_roster_review(filing.schedule_a_broker_rows,status,changed,self.identity,True,
            self.service.source_context_sha256(filing)).mode, 'PRESERVE')

    async def test_unverified_or_changed_readback_cannot_advance_confirmation(self):
        for key,value in [('values',{'InsContractNum':'wrong'}),('broker_rows',{'Broker':[]}),('actual_sequence','3')]:
            with self.subTest(key=key):
                observations = deepcopy(self.observations); observations[0][key]=value
                self.assertFalse(await self.acknowledge(observations=observations))
        self.assertFalse(await self.acknowledge(observations=[]))

    async def test_source_destination_or_roster_edit_during_write_requires_new_review(self):
        self.current.ftw_plan_id='different-plan'
        self.assertFalse(await self.acknowledge())
        self.current.ftw_plan_id=self.sent.ftw_plan_id
        await self.repo.update_filing(self.filing.id, {'s3_key':'changed.pdf'})
        self.assertFalse(await self.acknowledge())
        await self.repo.update_filing(self.filing.id, {'s3_key':'qa/source.pdf'})
        changed=self.row.model_copy(deep=True);changed.commission_total='99'
        await self.repo.update_filing(self.filing.id, {'schedule_a_broker_rows':[changed]})
        self.assertFalse(await self.acknowledge())

    async def test_failed_conditional_save_does_not_claim_acknowledgement(self):
        from unittest.mock import AsyncMock
        with patch.object(self.repo,'save_broker_review',AsyncMock(return_value=None)):
            self.assertFalse(await self.acknowledge())

    async def test_preserved_old_brokers_cannot_verify_the_approved_replacement(self):
        # Reproduce Lincoln's manual partial send: the approved list is correct,
        # but the transmitted document and read-back still contain the old list.
        self.sent.update_xml_schedule_a = self.service._build_schedule_a_update_xml([], self.before, '2',
            self.service._identity_from_review(self.sent))
        self.current.schedule_a_records = deepcopy(self.before)
        self.current.schedule_a_broker_roster = self.sent.schedule_a_broker_roster.model_copy(deep=True)
        observations = [{'form_type':'SCHEDULE_A','expected_sequence':'2','actual_sequence':'2',
            'values':deepcopy(self.before[0]['query_results']), 'broker_rows':deepcopy(self.before[0]['query_subparts'])}]
        self.assertFalse(await self.acknowledge(observations=observations))
        stored = await self.repo.get_filing(self.filing.id)
        self.assertNotIn('verified_update',stored.schedule_a_broker_roster.reviewer_confirmation)

    async def test_old_success_receipt_still_exposes_unsent_brokers_on_read(self):
        from app.api.filings import get_filing
        self.sent.status='UPDATE_SENT'
        await self.repo.upsert_ftwilliams_review(self.sent)
        with patch('app.api.filings.get_repository',return_value=self.repo):
            detail=await get_filing(self.filing.id)
        self.assertTrue(detail.ftw_review.broker_updates_pending)

    async def test_explicit_broker_opt_out_never_records_roster_verification(self):
        with patch('app.services.ftwilliams_review.get_repository',return_value=self.repo):
            self.assertFalse(await self.service._acknowledge_verified_broker_review(
                self.filing.id,self.sent,self.current,self.observations,broker_updates_selected=False))


if __name__ == '__main__': unittest.main()
