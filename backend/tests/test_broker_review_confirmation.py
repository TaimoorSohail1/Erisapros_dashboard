"""Reviewer confirmation is a separate, source/destination-bound permission."""
import unittest
from app.models import ScheduleABrokerRow, ScheduleABrokerRosterReview, BrokerRosterReviewRequest
from app.services.broker_review_confirmation import confirm_broker_review, review_token, disclosure_items
from app.services.schedule_a_broker_roster import source_roster_review


class BrokerReviewConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.status = ScheduleABrokerRosterReview(other_disclosures=[{
            'name': 'Mercer Health & Benefits LLC', 'address': '4565 PAYSPHERE CIRCLE CHICAGO IL 60674',
            'amount': '240148.74', 'page': 5, 'source_text': 'Schedule C Direct compensation $240,148.74'}])
        self.context, self.snapshot = 'a' * 64, 'b' * 64

    def request(self, **updates):
        data = dict(expected_context=review_token([], self.status, self.context, self.snapshot),
            complete_list_confirmed=True, reason='Client confirmed the complete list and category',
            disclosure_decisions=[{'disclosure_id': disclosure_items(self.status)[0]['id'], 'treatment': 'INCLUDED'}],
            row_sources=['DISCLOSURE:' + disclosure_items(self.status)[0]['id']])
        data.update(updates)
        return BrokerRosterReviewRequest(**data)

    def row(self):
        return ScheduleABrokerRow(name='Mercer Health & Benefits LLC', address_line_1='4565 PAYSPHERE CIRCLE',
            city='CHICAGO',state='IL',zip_code='60674',organization_code='3',commission_total='0',fee_total='240148.74')

    def confirm(self, rows, request=None):
        return confirm_broker_review(rows, [], self.status, request or self.request(),
            actor='reviewer-sub', source_context=self.context, snapshot=self.snapshot)

    def test_disclosure_can_be_confirmed_without_manufacturing_provider_proof(self):
        rows, status = self.confirm([self.row()])
        self.assertEqual(source_roster_review(rows,status).mode,'REPLACE')
        self.assertEqual(rows[0].purpose,'FEES')
        self.assertEqual(status.reviewer_confirmation['actor'],'reviewer-sub')
        self.assertFalse(any(v.validator=='complete_source_broker_roster' for v in rows[0].validation_results))
        self.assertEqual(status.other_disclosures[0]['amount'],'240148.74')

    def test_partial_allocation_and_stale_review_are_rejected(self):
        row=self.row(); row.fee_total='240000'
        with self.assertRaisesRegex(ValueError,'original amount'): self.confirm([row])
        with self.assertRaisesRegex(ValueError,'changed'):
            self.confirm([self.row()],self.request(expected_context='c'*64))

    def test_supporting_disclosure_does_not_establish_empty_roster(self):
        request=self.request(row_sources=[],disclosure_decisions=[{
            'disclosure_id':disclosure_items(self.status)[0]['id'],'treatment':'SUPPORTING_ONLY'}])
        with self.assertRaisesRegex(ValueError,'empty'): self.confirm([],request)

    def test_unknown_is_not_zero(self):
        row=self.row();row.commission_total=None
        with self.assertRaisesRegex(ValueError,'amount'): self.confirm([row])

    def test_changed_values_invalidate_manual_confirmation(self):
        rows,status=self.confirm([self.row()])
        rows[0].fee_total='240000'
        self.assertEqual(source_roster_review(rows,status).mode,'PRESERVE')

    def test_unresolved_disclosure_and_duplicate_recipient_are_rejected(self):
        request=self.request();request.disclosure_decisions[0].treatment='UNRESOLVED'
        with self.assertRaisesRegex(ValueError,'unresolved'): self.confirm([self.row()],request)
        request=self.request();request.row_sources*=2
        with self.assertRaisesRegex(ValueError,'twice'): self.confirm([self.row(),self.row()],request)

    def test_signed_split_preserves_both_categories_even_when_net_is_zero(self):
        self.status.other_disclosures[0]['amount']='0'
        row=self.row();row.commission_total='100.10';row.fee_total='-100.10'
        rows,status=self.confirm([row])
        self.assertEqual(rows[0].purpose,'COMMISSIONS AND FEES')
        self.assertEqual(source_roster_review(rows,status).replacement_count,1)

    def test_explicit_empty_review_is_distinct_from_ignoring_a_disclosure(self):
        request=self.request(row_sources=[],empty_list_evidence='Client confirmed no reportable Schedule A recipients',
            disclosure_decisions=[{'disclosure_id':disclosure_items(self.status)[0]['id'],'treatment':'SUPPORTING_ONLY'}])
        rows,status=self.confirm([],request)
        self.assertEqual(source_roster_review(rows,status).state,'VERIFIED_EMPTY')

    def test_forged_provider_proofs_and_evidence_are_not_trusted(self):
        from app.models import ExtractionValidationResult,SourceEvidence
        row=self.row();row.evidence=[SourceEvidence(page=99,source_text='Forged evidence')]
        row.validation_results=[ExtractionValidationResult(validator='complete_source_broker_roster',status='PASS',normalized_value='forged')]
        rows,status=self.confirm([row])
        self.assertEqual(rows[0].source_page,5)
        self.assertNotIn('Forged evidence',[e.source_text for e in rows[0].evidence])
        self.assertEqual(rows[0].validation_results,[])

    def test_rebound_source_or_destination_invalidates_review_and_allows_no_replacement(self):
        from app.services.schedule_a_broker_roster import bind_roster_review
        rows,status=self.confirm([self.row()])
        for source in (self.context,'c'*64):
            rebound=bind_roster_review(rows,status,[{'ftw_seq_no':'1','query_results':{'ScheduleDesc':'Changed'}}],
                {'ftw_seq_no':'1'},True,source_context=source)
            self.assertEqual(rebound.mode,'PRESERVE')


class BrokerReviewServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_filing_change_rejects_conditional_review_save(self):
        from datetime import datetime,timedelta
        from app.models import Filing
        from app.repositories import MemoryRepository
        repo=MemoryRepository()
        filing=await repo.create_filing(Filing(file_name='qa.pdf',content_type='application/pdf',file_size=1,s3_key='qa.pdf'))
        old=filing.updated_at-timedelta(seconds=1)
        saved=await repo.save_broker_review(filing.id,old,{'schedule_a_broker_rows':[]})
        self.assertIsNone(saved)

    async def test_save_reloads_confirmed_rows_and_builds_full_replacement_without_matches(self):
        import hashlib
        from unittest.mock import AsyncMock,patch
        from app.models import Filing,FTWilliamsReview,FTWilliamsScheduleABrokerRowsRequest
        from app.repositories import MemoryRepository
        from app.services.ftwilliams_review import FTWilliamsReviewService
        from app.services.schedule_a_broker_roster import bind_roster_review
        repo=MemoryRepository();source=b'Immutable source PDF for review'
        filing=await repo.create_filing(Filing(file_name='qa.pdf',content_type='application/pdf',file_size=len(source),s3_key='qa/source.pdf',
            schedule_a_broker_roster=ScheduleABrokerRosterReview(source_sha256=hashlib.sha256(source).hexdigest()),
            schedule_a_broker_rows=[ScheduleABrokerRow(name='Verified source broker',address_line_1='10 Main St',city='Boston',state='MA',zip_code='02110',organization_code='3',commission_total='100',fee_total='0')]))
        records=[{'ftw_seq_no':'2','query_results':{'ScheduleDesc':'UHC QA'},'query_subparts':{'Broker':[{'NameXX':'Obsolete broker'}]}}]
        identity={'ftw_customer_id':'qa-customer','ftw_plan_id':'qa-plan','year':'2025','ftw_seq_no':'2'}
        service=FTWilliamsReviewService()
        async def prepare(*args,**kwargs):
            current=await repo.get_filing(filing.id)
            normalized=service._normalized_schedule_a_broker_rows(current.schedule_a_broker_rows)
            status=bind_roster_review(normalized,current.schedule_a_broker_roster,records,identity,True,
                source_context=service.source_context_sha256(current))
            review=FTWilliamsReview(filing_id=filing.id,current_query_success=True,current_query_complete=True,
                schedule_a_match={'ftw_seq_no':'2'},schedule_a_records=records,schedule_a_broker_rows=normalized,
                schedule_a_broker_roster=status,**identity)
            return review
        current=await prepare()
        request=BrokerRosterReviewRequest(expected_context=current.schedule_a_broker_roster.review_context_sha256,
            complete_list_confirmed=True,reason='Source confirms complete list',row_sources=['SOURCE:0'])
        payload=FTWilliamsScheduleABrokerRowsRequest(rows=filing.schedule_a_broker_rows,action='reviewed',review=request)
        with patch('app.services.ftwilliams_review.get_repository',return_value=repo),patch.object(service,'prepare_review',side_effect=prepare),patch('app.services.storage.StorageService.load_pdf',return_value=source):
            saved=await service.update_schedule_a_broker_rows(filing.id,payload,actor='authenticated-sub')
        self.assertEqual(saved.schedule_a_broker_roster.mode,'REPLACE')
        matches,resolved=service._resolve_schedule_a_brokers(saved.schedule_a_broker_rows,records,'2',[],create_new=False,broker_roster=saved.schedule_a_broker_roster)
        self.assertTrue(all(m.resolved and m.ftw_index is None for m in matches))
        self.assertEqual(resolved[0].name,'Verified source broker')
        from app.services.xml_builder import build_schedule_a_records_update_xml
        xml=build_schedule_a_records_update_xml(records,'2',[],year='2025',schedule_a_broker_rows=resolved,replace_broker_roster=True)
        self.assertIn('<InsBrokerCommTotAmt>100</InsBrokerCommTotAmt>',xml)
        self.assertNotIn('Obsolete broker',xml)


if __name__ == '__main__': unittest.main()
