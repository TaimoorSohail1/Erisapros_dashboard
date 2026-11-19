import unittest
import hashlib
from unittest.mock import AsyncMock, patch
from app.models import NormalizedExtractionField, NormalizedExtractionResult, ScheduleABrokerRow

STATEMENT = '''The following information is for Form 5500, Schedule A
Name of Service Organization: Example HealthTax ID #: 95-4402957
Commission and bonus amounts paid to broker(s)
EXAMPLE BENEFIT SERVICES
3697 MAIN BLVD
Suite 300
Lafayette, CA 94549                         $7,749.28
Bonuses and miscellaneous compensation paid to broker for period indicated above $0.00
Total Compensation Paid to Brokers for period indicated above $7,749.28
'''


class StatementEvidenceTests(unittest.TestCase):
    def test_explicit_party_pair_recovers_carrier_without_confusing_sponsor_tax_id(self):
        from app.services.schedule_a_statement_evidence import statement_identity_fields
        fields = statement_identity_fields([(1, 'Sponsor Tax ID: 11-1111111\n'+STATEMENT)])
        values = {f.field_name:f.value for f in fields}
        self.assertEqual(values['1a. Name of Insurance Company'], 'Example Health')
        self.assertEqual(values['1b. Insurance Carrier EIN'], '95-4402957')
        self.assertTrue(all(f.page==1 and 'Name of Service Organization' in f.source_text for f in fields))
        other = STATEMENT.replace('Example Health', 'Another Carrier').replace('95-4402957','22-2222222')
        self.assertEqual(statement_identity_fields([(1,STATEMENT),(2,other)]), [])

    def test_generic_commission_bonus_block_is_an_independent_complete_read(self):
        from app.services.schedule_a_statement_evidence import statement_broker_rows
        rows = statement_broker_rows([(1,STATEMENT)])
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].name,'EXAMPLE BENEFIT SERVICES')
        self.assertEqual(rows[0].address_line_2,'Suite 300')
        self.assertEqual(rows[0].commission_total,'7749.28')
        self.assertEqual(rows[0].fee_total,'0')
        self.assertEqual(rows[0].organization_code,'3')
        self.assertEqual(rows[0].source_page,1)
        self.assertTrue(rows[0].evidence[0].source_text)
        self.assertEqual(statement_broker_rows([(1,STATEMENT.replace('$7,749.28\n', '$7,749.29\n',1))]), [])

    def test_unallocated_bonus_for_multiple_recipients_is_not_auto_verified(self):
        from app.services.schedule_a_statement_evidence import statement_broker_rows
        other = 'SECOND BROKER\n123 MAIN ST\nBoston, MA 02110       $50.00\n'
        text = STATEMENT.replace('Bonuses and',other+'Bonuses and').replace('$0.00','$10.00')
        text = text.replace('above $7,749.28','above $7,809.28')
        self.assertEqual(statement_broker_rows([(1,text)]), [])

    def test_blank_broker_address_is_retained_without_inventing_components(self):
        from app.services.schedule_a_statement_evidence import statement_broker_rows
        from app.services.broker_address_policy import address_state
        text = STATEMENT.replace('3697 MAIN BLVD\nSuite 300\nLafayette, CA 94549', '')
        rows = statement_broker_rows([(1,text)])
        self.assertEqual(len(rows),1)
        self.assertEqual(address_state(rows[0]),'BLANK')
        self.assertEqual(rows[0].organization_code,'3')

    def test_verified_provider_and_independent_rows_allow_automatic_replacement(self):
        from app.services.schedule_a_statement_evidence import statement_broker_rows
        from app.services.schedule_a_broker_roster import attest_source_broker_roster, source_roster_review
        rows = statement_broker_rows([(1,STATEMENT)])
        result = NormalizedExtractionResult(provider='test',fields=[
            NormalizedExtractionField(field_name='3b. Amount of Commissions',value='7749.28'),
            NormalizedExtractionField(field_name='3c. Amount of Fees',value='0')], schedule_a_broker_rows=rows,
            raw={'extraction_stages':[{'stage':'structured_provider_normalized','broker_rows':[r.model_dump(mode='json') for r in rows]}],
                'independent_source_page_coverage':{'source_sha256':'a'*64,'page_count':1,
                    'pages':[{'page':1,'text_sha256':'b'*64,'readable':True}]}})
        attest_source_broker_roster(result,[r.model_copy(deep=True) for r in rows],'a'*64)
        status = source_roster_review(rows)
        self.assertEqual(status.mode,'REPLACE')
        self.assertIsNone(status.reviewer_confirmation)


class StoredSourceVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.models import Filing, ExtractedField, ExtractedFieldStatus, RawExtraction, FormType
        from app.repositories import MemoryRepository
        from app.services.schedule_a_statement_evidence import statement_broker_rows
        self.repo = MemoryRepository()
        self.content = b'fixture bytes bound to native/provider evidence'
        self.sha = hashlib.sha256(self.content).hexdigest()
        self.rows = statement_broker_rows([(1, STATEMENT)])
        self.filing = await self.repo.create_filing(Filing(file_name='statement.pdf', content_type='application/pdf',
            file_size=len(self.content), s3_key='stored.pdf', package_documents=[{'document_type':'SCHEDULE_A',
                'file_name':'statement.pdf','s3_key':'stored.pdf','source_sha256':self.sha}], schedule_a_broker_rows=self.rows))
        self.fields = await self.repo.add_fields([ExtractedField(filing_id=self.filing.id,
            source_field_name=label, normalized_field_name=key, mapped_rule_key=key, mapped_label=label,
            form_type=FormType.SCHEDULE_A, status=ExtractedFieldStatus.MISSING)
            for key,label in [('schedule_a_part_i_1a_name_of_insurance_company','1a. Name of Insurance Company'),
                              ('schedule_a_part_i_1b_insurance_carrier_ein','1b. Insurance Carrier EIN')]])
        self.raw = await self.repo.add_raw_extraction(RawExtraction(filing_id=self.filing.id, provider='vendor',
            raw={'file_name':'statement.pdf','document_type':'SCHEDULE_A','raw':{
                'independent_source_page_coverage':{'source_sha256':self.sha},
                'extraction_stages':[
                    {'stage':'structured_provider_normalized','broker_rows':[r.model_dump(mode='json') for r in self.rows]},
                    {'stage':'final_normalized','fields':[
                        {'field_name':'3b. Amount of Commissions','value':'7749.28'},
                        {'field_name':'3c. Amount of Fees','value':'0'}]}]}}))
        def reader(content, file_name, page_coverage, **kwargs):
            page_coverage.update({'source_sha256':self.sha,'page_count':1,
                'pages':[{'page':1,'readable':True,'text_sha256':'b'*64}]})
            return [r.model_copy(deep=True) for r in self.rows]
        self.patches = [patch('app.services.storage.StorageService.load_pdf', return_value=self.content),
            patch('app.services.extractor.extract_pdf_layout_text_pages', return_value=[(1,STATEMENT)]),
            patch('app.services.extractor.extract_schedule_a_broker_rows_from_document', side_effect=reader)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    async def recover(self):
        from app.services.schedule_a_source_verification import verify_stored_schedule_a_source
        return await verify_stored_schedule_a_source(self.repo, self.filing.model_copy(deep=True),
            [f.model_copy(deep=True) for f in self.fields])

    async def test_missing_identity_causes_incomplete_query_then_source_recovery_selects_unique_record(self):
        from app.models import FTWilliamsStatusItem
        from app.services.ftwilliams_review import FTWilliamsReviewService
        service = FTWilliamsReviewService()
        match = FTWilliamsStatusItem(type='ScheduleA', error_code='0', ftw_seq_no='6',
            query_results={'InsCarrierName':'EXAMPLE HEALTH','InsCarrierEIN':'95-4402957','ScheduleDesc':'Health'})
        snapshot = {'query_request_xmls':[], 'query_response_xmls':[], 'form_5500_current':{},
            'form_5500_error':None,'form_5500_query_failed':False,'schedule_statuses':[match],
            'schedule_a_error':None,'schedule_a_query_failed':False}
        with patch.object(service, '_current_data_snapshot', AsyncMock(return_value=snapshot)):
            before = await service._run_current_queries_for_year(self.fields,{},None)
            self.assertFalse(before['current_query_complete'])
            filing, fields = await self.recover()
            after = await service._run_current_queries_for_year(fields,{},None)
            self.assertTrue(after['current_query_complete'])
            self.assertEqual(after['matched_schedule_a'].ftw_seq_no,'6')
            self.assertEqual(filing.schedule_a_broker_roster.mode,'REPLACE')
            self.assertIsNone(filing.schedule_a_broker_roster.reviewer_confirmation)
            self.assertEqual(len(self.repo.audit),1)
            # Equal strong matches still require a decision, and transport failure stays incomplete.
            snapshot['schedule_statuses'].append(match.model_copy(update={'ftw_seq_no':'7'}))
            tied = await service._run_current_queries_for_year(fields,{},None)
            self.assertFalse(tied['current_query_complete'])
            snapshot['schedule_statuses'].pop()
            snapshot['schedule_a_query_failed'] = True
            failed = await service._run_current_queries_for_year(fields,{},None)
            self.assertFalse(failed['current_query_complete'])
        from app.services.schedule_a_source_verification import verify_stored_schedule_a_source
        with patch('app.services.storage.StorageService.load_pdf', side_effect=AssertionError('should not reread')):
            await verify_stored_schedule_a_source(self.repo,filing,fields)
        self.assertEqual(len(self.repo.audit),1)

    async def test_hash_mismatch_and_unbound_evidence_cannot_recover_or_approve(self):
        with patch('app.services.storage.StorageService.load_pdf', return_value=b'changed source'):
            filing, fields = await self.recover()
        self.assertIsNone(filing.schedule_a_broker_roster)
        self.assertTrue(all(not f.value for f in fields))
        self.raw.raw['raw']['independent_source_page_coverage']['source_sha256'] = 'f'*64
        filing, fields = await self.recover()
        self.assertIsNone(filing.schedule_a_broker_roster)
        self.assertTrue(all(not f.value for f in fields))

    async def test_partial_address_and_disagreeing_provider_do_not_approve(self):
        self.filing.schedule_a_broker_rows[0].city = ''
        filing, fields = await self.recover()
        self.assertIsNone(filing.schedule_a_broker_roster)
        self.assertTrue(all(f.value for f in fields))
        self.filing.schedule_a_broker_rows[0].city = 'Lafayette'
        self.raw.raw['raw']['extraction_stages'][0]['broker_rows'][0]['commission_total'] = '1'
        filing, fields = await self.recover()
        self.assertIsNone(filing.schedule_a_broker_roster)

    async def test_concurrent_human_field_edit_aborts_entire_source_save(self):
        from app.models import ExtractedFieldStatus
        original_save = self.repo.save_source_verification
        async def racing_save(filing, fields, values):
            self.repo.fields[fields[0][0].id] = fields[0][0].model_copy(
                update={'proposed_value':'Reviewer value','status':ExtractedFieldStatus.EDITED})
            return await original_save(filing, fields, values)
        with patch.object(self.repo,'save_source_verification',side_effect=racing_save):
            filing, fields = await self.recover()
        self.assertIsNone(filing.schedule_a_broker_roster)
        self.assertEqual(fields[0].proposed_value,'Reviewer value')
        self.assertFalse(fields[1].value)
        self.assertEqual(self.repo.audit,[])

    async def test_source_replaced_while_reading_aborts_identity_and_roster_save(self):
        def replaced_source(*args, **kwargs):
            self.repo.filings[self.filing.id].package_documents[0]['source_sha256'] = 'f'*64
            return self.content
        with patch('app.services.storage.StorageService.load_pdf',side_effect=replaced_source):
            filing, fields = await self.recover()
        self.assertIsNone(filing.schedule_a_broker_roster)
        self.assertTrue(all(not f.value for f in fields))
        self.assertEqual(self.repo.audit,[])

    async def test_prepare_runs_recovery_before_lookup_including_preloaded_path(self):
        from app.services.ftwilliams_review import FTWilliamsReviewService
        from app.services.schedule_a_source_verification import verify_stored_schedule_a_source
        recovered = await self.recover()
        sentinel = RuntimeError('stop after source verification')
        service = FTWilliamsReviewService()
        async def lookup(filing, fields, **kwargs):
            self.assertTrue(all(f.value for f in fields))
            self.assertEqual(filing.schedule_a_broker_roster.mode,'REPLACE')
            raise sentinel
        with patch('app.services.ftwilliams_review.get_repository',return_value=self.repo), \
             patch('app.services.schedule_a_source_verification.verify_stored_schedule_a_source',AsyncMock(return_value=recovered)) as verify, \
             patch.object(service,'_prepare_plan_lookup',side_effect=lookup):
            with self.assertRaisesRegex(RuntimeError,'stop after source verification'):
                await service.prepare_review(self.filing.id,preloaded=(self.filing,self.fields,[],None))
            verify.assert_awaited_once()

