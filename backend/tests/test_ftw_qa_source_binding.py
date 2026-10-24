import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.repositories as repositories
from app.config import Settings
from app.models import Filing, FTWilliamsReview
from app.services.ftw_qa_guard import configure_qa_write_guard, is_qa_scoped
from app.services.ftwilliams import FTWilliamsService, set_qa_write_guard


class QASourceBindingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        repositories._repository = repositories.MemoryRepository()
        self.repo = repositories.get_repository()
        self.settings = Settings(_env_file=None, ftw_qa_guard_folder_ids_json='["fo-qa"]',
            ftw_qa_registry_s3_key='qa/control/registry.json', s3_bucket_name='private-qa', aws_region='eu-north-1')
        self.files = {'pdf':b'approved PDF bytes','worksheet':b'approved worksheet bytes'}
        self.case = {'target':{'ftw_customer_id':'11','ftw_plan_id':'22','year':'2025','ftw_seq_no':'2'},
            'source_sha256':hashlib.sha256(self.files['pdf']).hexdigest(),
            'worksheet_sha256':hashlib.sha256(self.files['worksheet']).hexdigest(),
            'baseline_form_5500':{'PlanName':'Original'}, 'allowed_parent_values':{},
            'baseline_records':[{'ftw_seq_no':'2','query_results':{'ScheduleDesc':'Selected','InsContractNum':'000222'},'query_subparts':{}}]}
        self.run = {'run_id':'run','state':'ACTIVE','folder_ids':['fo-qa'],
            'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),'cases':[self.case]}
        self.registry = {'schema_version':1,'runs':[self.run]}
        self.filing = await self.repo.create_filing(Filing(file_name='source.pdf', content_type='application/pdf',
            file_size=10, s3_key='pdf',sharefile_parent_id='fo-qa',package_documents=[
                {'document_type':kind,'s3_bucket':'private-qa','s3_key':key,'sharefile_parent_id':'fo-qa'}
                for kind,key in [('SCHEDULE_A','pdf'),('PLAN_WORKSHEET','worksheet')]]))
        self.review = FTWilliamsReview(filing_id=self.filing.id,ftw_customer_id='11',ftw_plan_id='22',year='2025',ftw_seq_no='2',
            schedule_a_records=deepcopy(self.case['baseline_records']),form_5500_current_values={'PlanName':'Original'})
        self.client = FTWilliamsService()
        self.http = SimpleNamespace(post=AsyncMock(return_value='vendor-response'))
        self.client._get_client = AsyncMock(return_value=self.http)
        self.xml = '<ftwLink><DataBatch><DOLScheduleAData><TransactionType>2</TransactionType><FTWCustomerID>11</FTWCustomerID><FTWPlanID>22</FTWPlanID><Year>2025</Year><ScheduleDesc>Selected</ScheduleDesc><InsContractNum>000222</InsContractNum></DOLScheduleAData></DataBatch></ftwLink>'

    async def asyncTearDown(self):
        repositories._repository = None

    def patches(self):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch('app.services.ftw_qa_guard.get_settings',return_value=self.settings))
        stack.enter_context(patch('app.services.ftw_qa_guard._read_registry',return_value=self.registry))
        stack.enter_context(patch('app.services.ftw_qa_guard.StorageService.load_pdf',side_effect=lambda key,bucket:self.files[key]))
        return stack

    async def test_registered_hashes_bind_and_closed_run_rejects_late_write(self):
        with self.patches():
            await configure_qa_write_guard(self.review,self.client)
            self.assertEqual(self.filing.qa_run_binding['run_id'],'run')
            self.assertEqual(await self.client._post_xml('https://vendor.invalid',self.xml),'vendor-response')
            self.run['state'] = 'CLOSED'
            with self.assertRaises(ValueError): await self.client._post_xml('https://vendor.invalid',self.xml)
        self.assertEqual(self.http.post.await_count,1)

    async def test_wrong_source_or_missing_worksheet_never_reaches_network(self):
        for change in ['bytes','worksheet']:
            with self.subTest(change=change):
                self.files['pdf'] = b'different PDF' if change=='bytes' else b'approved PDF bytes'
                if change == 'worksheet': self.filing.package_documents.pop()
                with self.patches(), self.assertRaises(ValueError):
                    await configure_qa_write_guard(self.review,self.client)
        self.http.post.assert_not_awaited()

    async def test_current_original_state_must_match_manifest(self):
        self.review.form_5500_current_values['PlanName'] = 'Concurrent change'
        with self.patches(),self.assertRaises(ValueError): await configure_qa_write_guard(self.review,self.client)

    async def test_unknown_write_without_transaction_type_cannot_bypass_callback(self):
        with self.patches():
            await configure_qa_write_guard(self.review,self.client)
            with self.assertRaises(ValueError): await self.client._post_xml('https://vendor.invalid','<ftwLink><CompanyData><FTWCustomerID>11</FTWCustomerID></CompanyData></ftwLink>')
        self.http.post.assert_not_awaited()

    async def test_persisted_binding_stays_restricted_when_folder_setting_removed(self):
        with self.patches(): await configure_qa_write_guard(self.review,self.client)
        self.settings.ftw_qa_guard_folder_ids_json = '[]'
        self.assertTrue(is_qa_scoped(self.filing,self.settings))
        with patch('app.services.ftw_qa_guard.get_settings',return_value=self.settings), \
             patch('app.services.ftw_qa_guard._read_registry',side_effect=ValueError('missing registry')), self.assertRaises(ValueError):
            await configure_qa_write_guard(self.review,self.client)

    async def test_ordinary_filing_does_not_read_private_registry(self):
        self.filing.sharefile_parent_id = 'ordinary-folder'
        self.filing.package_documents = []
        with patch('app.services.ftw_qa_guard.get_settings',return_value=self.settings), \
             patch('app.services.ftw_qa_guard._read_registry',side_effect=AssertionError('ordinary flow touched QA registry')):
            await configure_qa_write_guard(self.review,self.client)
            self.assertEqual(await self.client._post_xml('https://vendor.invalid',self.xml),'vendor-response')

    async def test_task_local_guards_do_not_leak_between_concurrent_sends(self):
        async def attempt(marker):
            set_qa_write_guard(self.client,AsyncMock(side_effect=ValueError(marker)))
            await asyncio.sleep(0)
            with self.assertRaises(ValueError) as error:
                await self.client._post_xml('https://vendor.invalid',self.xml)
            return str(error.exception)
        self.assertEqual(await asyncio.gather(attempt('first'),attempt('second')),['first','second'])
        self.http.post.assert_not_awaited()
