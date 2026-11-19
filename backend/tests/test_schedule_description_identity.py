import unittest
from unittest.mock import AsyncMock, patch
from app.models import ExtractedField,ExtractedFieldStatus,FormType,FTWilliamsReview,FTWilliamsReviewStatus,FTWilliamsStatusItem,FTWilliamsScheduleAMatchRequest
from app.services.ftwilliams_review import FTWilliamsReviewService

def identity(contract='POLICY200',ein='22-0832760'):
    return [ExtractedField(filing_id='f',source_field_name=key,normalized_field_name=key,mapped_rule_key=key,
        form_type=FormType.SCHEDULE_A,value=value,proposed_value=value,confidence=.99,status=ExtractedFieldStatus.MATCHED)
        for key,value in [('schedule_a_part_i_1a_name_of_insurance_company','Example Insurance'),
            ('schedule_a_part_i_1b_insurance_carrier_ein',ein),('schedule_a_part_i_1d_contract_policy_number',contract)]]

def record(seq='1',contract='POLICY100',ein='22-0832760',description='EXAMPLEI'):
    return {'ftw_seq_no':seq,'query_results':{'ScheduleDesc':description,'InsContractNum':contract,'InsCarrierEIN':ein,'InsCarrierName':'EXAMPLE INSURANCE'}}

class CreatedScheduleIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):self.service=FTWilliamsReviewService();self.fields=identity()

    def test_description_cannot_bind_a_different_contract_or_carrier(self):
        for other in [record(),record(contract='POLICY200',ein='11-1111111')]:
            with self.subTest(other=other):
                self.assertIsNone(self.service._matching_existing_schedule_a_for_create(self.fields,[other],expected_schedule_desc='EXAMPLEI'))

    def test_description_cannot_replace_missing_source_identity(self):
        self.assertIsNone(self.service._matching_existing_schedule_a_for_create(self.fields[:1],[record()],expected_schedule_desc='EXAMPLEI'))

    def test_description_only_disambiguates_identity_verified_rows(self):
        rows=[record(contract='POLICY200',description='OTHER'),record(seq='2',contract='POLICY200')]
        self.assertEqual(self.service._matching_existing_schedule_a_for_create(self.fields,rows,expected_schedule_desc='EXAMPLEI')['ftw_seq_no'],'2')

    def test_duplicate_identity_without_unique_description_needs_decision(self):
        rows=[record(contract='POLICY200'),record(seq='2',contract='POLICY200')]
        self.assertIsNone(self.service._matching_existing_schedule_a_for_create(self.fields,rows,expected_schedule_desc='UNUSED'))

    def test_unique_identity_is_reconciled_when_another_policy_uses_expected_description(self):
        rows=[record(),record(seq='2',contract='POLICY200',description='OTHER')]
        self.assertEqual(self.service._matching_existing_schedule_a_for_create(self.fields,rows,expected_schedule_desc='EXAMPLEI')['ftw_seq_no'],'2')

    async def query(self,review):
        item=record();status=FTWilliamsStatusItem(type='ScheduleA',error_code='0',ftw_seq_no='1',query_results=item['query_results'])
        snapshot={'query_request_xmls':[],'query_response_xmls':[],'form_5500_current':{},'form_5500_error':None,
            'form_5500_query_failed':False,'schedule_statuses':[status],'schedule_a_error':None,'schedule_a_query_failed':False}
        with patch.object(self.service,'_current_data_snapshot',AsyncMock(return_value=snapshot)):
            return await self.service._run_current_queries_for_year(self.fields,{},review)

    def pending(self):
        return FTWilliamsReview(filing_id='f',schedule_a_match={'create_new':True,'source':'AUTO_NEW_SCHEDULE_A',
            'schedule_desc':'EXAMPLEI','contract':'POLICY200','carrier_ein':'22-0832760'})

    async def test_fresh_auto_new_collision_regenerates_description_without_cross_contract_match(self):
        result=await self.query(self.pending())
        self.assertIsNone(result['matched_schedule_a']);self.assertTrue(result['current_query_complete'])
        new=result['automatic_schedule_a_match'];self.assertIsNotNone(new)
        self.assertEqual(new['contract'],'POLICY200');self.assertEqual(new['schedule_desc'],'EXAMPLE1')

    async def test_manual_new_description_is_not_silently_changed(self):
        review=self.pending();review.schedule_a_match['source']='MANUAL'
        result=await self.query(review)
        self.assertIsNone(result['matched_schedule_a']);self.assertIsNone(result['automatic_schedule_a_match'])

    async def test_explicitly_confirmed_auto_target_description_is_preserved(self):
        review=self.pending();review.schedule_a_match['new_target_confirmed']=True
        result=await self.query(review)
        self.assertIsNone(result['matched_schedule_a']);self.assertIsNone(result['automatic_schedule_a_match'])

    def test_many_policy_descriptions_stay_unique(self):
        labels=['EXAMPLEI']+['EXAMPLE'+str(i) for i in range(1,10)]+['EXAMPL99']
        rows=[record(seq=str(i+1),description=label) for i,label in enumerate(labels)]
        generated=self.service._schedule_desc_from_payload_or_fields(FTWilliamsScheduleAMatchRequest(create_new=True),self.fields,rows)
        self.assertLessEqual(len(generated),8);self.assertNotIn(generated,labels)

    async def test_unknown_prior_write_cannot_silently_prepare_another_new_record(self):
        review=self.pending();review.update_attempted_count=1;review.status=FTWilliamsReviewStatus.UPDATE_UNKNOWN
        result=await self.query(review)
        self.assertIsNone(result['matched_schedule_a']);self.assertIsNone(result['automatic_schedule_a_match'])
