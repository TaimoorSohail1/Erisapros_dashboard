"""New Schedule A broker approval is source/target-bound without a fake sequence."""
import hashlib
import unittest
from copy import deepcopy
from unittest.mock import patch

from app.models import (BrokerRosterReviewRequest, ExtractedField, Filing, FormType,
    FTWilliamsReview, FTWilliamsScheduleABrokerRowsRequest, ScheduleABrokerRow,
    ScheduleABrokerRosterReview)
from app.repositories import MemoryRepository
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.schedule_a_broker_roster import bind_roster_review


class NewScheduleBrokerReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = MemoryRepository()
        self.service = FTWilliamsReviewService(ftwilliams=object())
        self.source = b'Immutable synthetic source for new Schedule A'
        self.row = ScheduleABrokerRow(name='Source Broker', address_line_1='10 Main St',
            city='Boston', state='MA', zip_code='02110', organization_code='3',
            commission_total='100.12', fee_total='25.22')
        self.filing = await self.repo.create_filing(Filing(file_name='qa.pdf', content_type='application/pdf',
            file_size=len(self.source), s3_key='qa/source.pdf', schedule_a_broker_rows=[self.row],
            schedule_a_broker_roster=ScheduleABrokerRosterReview(source_sha256=hashlib.sha256(self.source).hexdigest())))
        self.identity = dict(ftw_customer_id='qa-customer', ftw_plan_id='qa-plan', year='2025', ftw_seq_no=None)
        self.match = dict(create_new=True, source='AUTO_NEW_SCHEDULE_A', schedule_desc='NEWLIFE',
            carrier='Example Life Insurance Company', carrier_ein='12-3456789', contract='NEW-222')
        self.records = [{'ftw_seq_no':'1','query_results':{'ScheduleDesc':'SIBLING',
            'InsCarrierEIN':'98-7654321','InsContractNum':'OLD-111'}}]
        for key, value in (
            ('schedule_a_part_i_1a_name_of_insurance_company',self.match['carrier']),
            ('schedule_a_part_i_1b_insurance_carrier_ein',self.match['carrier_ein']),
            ('schedule_a_part_i_1d_contract_policy_number',self.match['contract']),
        ):
            await self.repo.add_fields([ExtractedField(filing_id=self.filing.id, source_field_name=key,
                normalized_field_name=key, mapped_rule_key=key, value=value, proposed_value=value,
                form_type=FormType.SCHEDULE_A, confidence=.99)])

    async def prepare(self, *args, **kwargs):
        filing = await self.repo.get_filing(self.filing.id)
        rows = self.service._normalized_schedule_a_broker_rows(filing.schedule_a_broker_rows)
        identity = {**self.identity, 'new_schedule_a': {k:self.match[k] for k in ('schedule_desc','contract','carrier_ein')}}
        status = bind_roster_review(rows, filing.schedule_a_broker_roster, self.records, identity, True,
            self.service.source_context_sha256(filing))
        return FTWilliamsReview(filing_id=filing.id, current_query_success=True, current_query_complete=True,
            current_year_exists=True, ftw_editable=True, schedule_a_match=deepcopy(self.match),
            schedule_a_records=deepcopy(self.records), schedule_a_broker_rows=rows,
            form_5500_current_values={'PlanName':'QA Plan','SDEIN':'23-4567890'},
            schedule_a_broker_roster=status, **self.identity)

    async def payload(self):
        review = await self.prepare()
        return FTWilliamsScheduleABrokerRowsRequest(rows=[self.row], action='reviewed', review=BrokerRosterReviewRequest(
            expected_context=review.schedule_a_broker_roster.review_context_sha256,
            complete_list_confirmed=True, reason='Source confirms the complete paid broker list', row_sources=['SOURCE:0']))

    async def save(self, payload):
        with patch('app.services.ftwilliams_review.get_repository', return_value=self.repo), \
             patch.object(self.service, 'prepare_review', side_effect=self.prepare), \
             patch('app.services.storage.StorageService.load_pdf', return_value=self.source):
            return await self.service.update_schedule_a_broker_rows(self.filing.id, payload, actor='reviewer-sub')

    async def test_review_saves_for_a_verified_new_target_without_an_existing_sequence(self):
        result = await self.save(await self.payload())
        self.assertIsNone(result.ftw_seq_no)
        self.assertTrue(result.schedule_a_match['create_new'])
        self.assertEqual(result.schedule_a_broker_roster.mode, 'REPLACE')
        self.assertEqual(result.schedule_a_broker_rows[0].purpose, 'COMMISSIONS AND FEES')
        stored = await self.repo.get_filing(self.filing.id)
        self.assertEqual(stored.schedule_a_broker_roster.reviewer_confirmation['actor'], 'reviewer-sub')
        self.assertEqual(self.records[0]['query_results']['InsContractNum'], 'OLD-111')

    async def test_new_record_sends_verified_broker_rows_and_header_totals_and_preserves_sibling(self):
        import xml.etree.ElementTree as ET
        result = await self.save(await self.payload())
        before = deepcopy(self.records)
        xml = self.service._build_schedule_a_update_xml(await self.repo.list_fields(self.filing.id),
            self.records, None, self.identity, add_new_schedule_a=True, new_schedule_desc='NEWLIFE',
            schedule_a_broker_rows=result.schedule_a_broker_rows, replace_broker_roster=True,
            form_5500_current_values={'PlanName':'QA Plan','SDName':'QA Sponsor','SDEIN':'23-4567890'})
        documents = ET.fromstring(xml).findall('.//DOLScheduleAData')
        self.assertEqual(len(documents), 2)
        new = next(node for node in documents if node.findtext('ScheduleDesc') == 'NEWLIFE')
        self.assertEqual(new.findtext('InsBrokerCommTotAmt'), '100.12')
        self.assertEqual(new.findtext('InsBrokerFeesTotAmt'), '25.22')
        self.assertEqual(new.findtext('.//NameXX'), 'SOURCE BROKER')
        self.assertEqual(new.findtext('.//FeesPdTextXX'), 'COMMISSIONS AND FEES')
        self.assertEqual(new.findtext('PlanSponsorName'), 'QA SPONSOR')
        self.assertEqual(new.findtext('InsContractNum'), 'NEW-222')
        self.assertEqual(self.records, before)

    async def created_pair(self):
        sent = await self.save(await self.payload())
        sent.update_xml_schedule_a = self.service._build_schedule_a_update_xml(
            await self.repo.list_fields(self.filing.id), self.records, None, self.identity,
            add_new_schedule_a=True, new_schedule_desc='NEWLIFE',
            schedule_a_broker_rows=sent.schedule_a_broker_rows, replace_broker_roster=True)
        documents = self.service._schedule_update_documents_with_sequences(sent)
        current = sent.model_copy(deep=True)
        current.ftw_seq_no = '8'
        current.schedule_a_match = {'ftw_seq_no':'8','create_new':False}
        current.schedule_a_records = []
        observations = []
        for doc in documents:
            seq = str(doc.get('__ftw_seq_no') or '8')
            record = {'ftw_seq_no':seq,
                'query_results':{k:v for k,v in doc.items() if not k.startswith('__')},
                'query_subparts':deepcopy(doc.get('__subparts__',{}))}
            current.schedule_a_records.append(record)
            observations.append({'form_type':'SCHEDULE_A','expected_sequence':doc.get('__ftw_seq_no'),
                'actual_sequence':seq,'values':deepcopy(record['query_results']),
                'broker_rows':deepcopy(record['query_subparts'])})
        filing = await self.repo.get_filing(self.filing.id)
        current.schedule_a_broker_roster = bind_roster_review(filing.schedule_a_broker_rows,
            filing.schedule_a_broker_roster, current.schedule_a_records,
            {**self.identity,'ftw_seq_no':'8'},True,self.service.source_context_sha256(filing))
        return sent, current, observations

    async def acknowledge(self, sent, current, observations):
        with patch('app.services.ftwilliams_review.get_repository', return_value=self.repo):
            return await self.service._acknowledge_verified_broker_review(self.filing.id, sent, current, observations)

    async def test_new_sequence_is_acknowledged_only_after_full_saved_value_verification(self):
        sent, current, observations = await self.created_pair()
        self.assertTrue(await self.acknowledge(sent,current,observations))
        stored = await self.repo.get_filing(self.filing.id)
        status = bind_roster_review(stored.schedule_a_broker_rows, stored.schedule_a_broker_roster,
            current.schedule_a_records,{**self.identity,'ftw_seq_no':'8'},True,self.service.source_context_sha256(stored))
        self.assertEqual(status.mode,'REPLACE')
        self.assertEqual(status.reviewer_confirmation['verified_update']['destination']['ftw_seq_no'],'8')
        self.assertEqual(status.reviewer_confirmation['actor'],'reviewer-sub')

    async def test_new_sequence_acknowledgement_rejects_missing_or_changed_siblings_and_wrong_target(self):
        sent, current, observations = await self.created_pair()
        for kind in ('missing-sibling','changed-sibling','wrong-policy','wrong-plan','wrong-sequence'):
            with self.subTest(kind=kind):
                changed = current.model_copy(deep=True)
                seen = deepcopy(observations)
                if kind == 'missing-sibling': changed.schedule_a_records.pop(0)
                if kind == 'changed-sibling': changed.schedule_a_records[0]['query_results']['InsContractNum']='EXTERNAL-CHANGE'
                if kind == 'wrong-policy': changed.schedule_a_records[-1]['query_results']['InsContractNum']='OTHER-POLICY'
                if kind == 'wrong-plan': changed.ftw_plan_id='OTHER-PLAN'
                if kind == 'wrong-sequence': changed.ftw_seq_no='1'
                self.assertFalse(await self.acknowledge(sent,changed,seen))

    async def test_new_target_approval_is_invalidated_by_sibling_changes(self):
        payload = await self.payload()
        self.records[0]['query_results']['InsContractNum'] = 'CHANGED-SIBLING'
        with self.assertRaisesRegex(ValueError, 'changed'):
            await self.save(payload)
        stored = await self.repo.get_filing(self.filing.id)
        self.assertIsNone(stored.schedule_a_broker_roster.reviewer_confirmation)

    async def test_changed_new_description_invalidates_the_review(self):
        payload = await self.payload()
        self.match['schedule_desc'] = 'OTHER'
        with self.assertRaisesRegex(ValueError, 'changed'):
            await self.save(payload)

    async def test_padded_existing_policy_requires_target_review_not_global_trimming(self):
        self.match.update(contract='00001023386600000')
        self.records[0]['query_results'].update(InsCarrierEIN='12-3456789', InsContractNum='10233866')
        with self.assertRaisesRegex(ValueError, 'may refer to existing'):
            await self.save(await self.payload())
        self.assertEqual(self.match['contract'], '00001023386600000')

    async def test_existing_policy_cannot_be_confirmed_as_new_even_by_manual_selection(self):
        self.match['source'] = 'NEW_SCHEDULE_A'
        self.records[0]['query_results'].update(InsCarrierEIN='12-3456789', InsContractNum=self.match['contract'])
        with self.assertRaisesRegex(ValueError, 'already exists'):
            await self.save(await self.payload())

    def test_legacy_automatic_new_label_is_not_explicit_target_confirmation(self):
        selected = {'contract':'00001023386600000','carrier_ein':'35-0472300','source':'NEW_SCHEDULE_A'}
        records = [{'ftw_seq_no':'7','query_results':{'InsCarrierEIN':'35-0472300','InsContractNum':'10233866'}}]
        self.assertIn('existing Schedule A #7', self.service._padded_contract_review_error(selected,records))
        selected['new_target_confirmed'] = True
        self.assertIsNone(self.service._padded_contract_review_error(selected,records))

    async def test_missing_or_locked_destination_does_not_save(self):
        from unittest.mock import AsyncMock
        for changes in ({'current_query_complete':False}, {'current_year_exists':False},
                        {'ftw_editable':False}, {'ftw_customer_id':None}):
            with self.subTest(changes=changes):
                payload = await self.payload()
                review = (await self.prepare()).model_copy(update=changes)
                with patch('app.services.ftwilliams_review.get_repository', return_value=self.repo), \
                     patch.object(self.service, 'prepare_review', AsyncMock(return_value=review)):
                    with self.assertRaises(ValueError):
                        await self.service.update_schedule_a_broker_rows(self.filing.id, payload, actor='reviewer-sub')

    def test_automatic_creation_does_not_hide_a_possible_zero_padded_existing_policy(self):
        from app.models import FTWilliamsStatusItem
        fields = [ExtractedField(filing_id='qa', source_field_name=key, normalized_field_name=key,
            mapped_rule_key=key, value=value, proposed_value=value, confidence=.99, form_type=FormType.SCHEDULE_A)
            for key, value in (
                ('schedule_a_part_i_1d_contract_policy_number','00001023386600000'),
                ('schedule_a_part_i_1b_insurance_carrier_ein','12-3456789'))]
        status = FTWilliamsStatusItem(type='ScheduleA', error_code='0', ftw_seq_no='7',
            query_results={'InsContractNum':'10233866','InsCarrierEIN':'12-3456789'})
        self.assertIsNone(self.service._automatic_new_schedule_a_match(fields, [status],
            [{'ftw_seq_no':'7','query_results':status.query_results}]))


if __name__ == '__main__': unittest.main()
