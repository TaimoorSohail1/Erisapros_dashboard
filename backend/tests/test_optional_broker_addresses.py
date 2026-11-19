import unittest
from app.models import ScheduleABrokerRow, ScheduleABrokerRosterReview, BrokerRosterReviewRequest
from app.services.broker_address_policy import address_state, apply_address_policy, partial_address_confirmed
from app.services.broker_review_confirmation import confirm_broker_review, review_token
from app.services.schedule_a_broker_roster import source_roster_review
from app.services.xml_builder import schedule_a_broker_update_values
from app.services.ftwilliams_contract import FTWPayloadValidationError


class OptionalBrokerAddressTests(unittest.TestCase):
    def test_source_verified_blank_and_complete_addresses_qualify_without_human_approval(self):
        from app.models import NormalizedExtractionResult, NormalizedExtractionField, SourceEvidence
        from app.services.schedule_a_broker_roster import attest_source_broker_roster, broker_roster_is_complete
        from app.services.ftwilliams_review import FTWilliamsReviewService
        for address in ({}, dict(address_line_1='123 MAIN ST', city='BOSTON', state='MA', zip_code='02110')):
            with self.subTest(address=address):
                row = self.row(**address, source_page=1, evidence=[SourceEvidence(page=1, source_text='EXAMPLE BROKER 100 0')])
                result = NormalizedExtractionResult(provider='test', fields=[
                    NormalizedExtractionField(field_name='3b. Commissions', value='100'),
                    NormalizedExtractionField(field_name='3c. Fees', value='0')], schedule_a_broker_rows=[row],
                    raw={'extraction_stages':[{'stage':'structured_provider_normalized','broker_rows':[row.model_dump(mode='json')]}],
                         'independent_source_page_coverage':{'source_sha256':'a'*64,'page_count':1,
                            'pages':[{'page':1,'text_sha256':'b'*64,'readable':True}]}})
                attest_source_broker_roster(result, [row.model_copy(deep=True)], 'a'*64)
                normalized = FTWilliamsReviewService()._normalized_schedule_a_broker_rows(result.schedule_a_broker_rows)
                self.assertTrue(broker_roster_is_complete(normalized))
                status = source_roster_review(normalized)
                self.assertEqual(status.mode, 'REPLACE')
                self.assertIsNone(status.reviewer_confirmation)
                self.assertEqual(schedule_a_broker_update_values(normalized)['Code1'], '3')

    def row(self, **values):
        return ScheduleABrokerRow(name='EXAMPLE BROKER', commission_total='100', fee_total='0', **values)

    def confirm(self, row, accept=False):
        status = ScheduleABrokerRosterReview()
        request = BrokerRosterReviewRequest(expected_context=review_token([row], status, 'source', 'snapshot'),
            complete_list_confirmed=True, reason='Save the displayed list', row_sources=['SOURCE:0'],
            accept_partial_addresses=accept)
        return confirm_broker_review([row], [row], status, request, actor='reviewer',
            source_context='source', snapshot='snapshot')

    def test_blank_address_passes_blank_with_org_code_three(self):
        row = self.row()
        self.assertEqual(address_state(row), 'BLANK')
        self.assertNotEqual(apply_address_policy([row])[0].decision, 'REVIEW_REQUIRED')
        rows, status = self.confirm(row)
        self.assertEqual(source_roster_review(rows, status).mode, 'REPLACE')
        values = schedule_a_broker_update_values(rows)
        self.assertEqual(values['Code1'], '3')
        for tag in ('AddressLine11', 'AddressLine21', 'City1', 'State1', 'ZipCode1'):
            self.assertEqual(values[tag], '')

    def test_complete_address_needs_no_address_acknowledgement(self):
        row = self.row(address_line_1='123 MAIN ST', city='BOSTON', state='MA', zip_code='02110', organization_code='4')
        self.assertEqual(address_state(row), 'COMPLETE')
        rows, status = self.confirm(row)
        self.assertEqual(source_roster_review(rows, status).mode, 'REPLACE')
        self.assertEqual(schedule_a_broker_update_values(rows)['Code1'], '4')

    def test_partial_address_pauses_until_explicit_save_as_is(self):
        row = apply_address_policy([self.row(city='BOSTON')])[0]
        self.assertEqual(row.decision, 'REVIEW_REQUIRED')
        with self.assertRaises(FTWPayloadValidationError):
            schedule_a_broker_update_values([row])
        with self.assertRaisesRegex(ValueError, 'Save and send as-is'):
            self.confirm(row)
        rows, status = self.confirm(row, accept=True)
        self.assertTrue(partial_address_confirmed(rows[0]))
        self.assertEqual(source_roster_review(rows, status).mode, 'REPLACE')
        values = schedule_a_broker_update_values(rows)
        self.assertEqual(values['City1'], 'BOSTON')
        self.assertEqual(values['AddressLine11'], '')
        self.assertEqual(values['Code1'], '3')

    def test_address_change_invalidates_as_is_confirmation(self):
        rows, _ = self.confirm(self.row(city='BOSTON'), accept=True)
        rows[0].city = 'CHICAGO'
        self.assertFalse(partial_address_confirmed(rows[0]))
        self.assertEqual(apply_address_policy(rows)[0].decision, 'REVIEW_REQUIRED')

    def test_unrelated_source_error_cannot_be_waived(self):
        from app.models import ExtractionValidationResult
        row = self.row(city='BOSTON', validation_results=[ExtractionValidationResult(
            validator='source_evidence', status='ERROR', reason='Unverified payment source')])
        with self.assertRaisesRegex(ValueError, 'source issues'):
            self.confirm(row, accept=True)

    def test_stale_save_as_is_request_cannot_acknowledge_an_address(self):
        row = self.row(city='BOSTON')
        request = BrokerRosterReviewRequest(expected_context='stale', complete_list_confirmed=True,
            accept_partial_addresses=True, reason='Save as-is', row_sources=['SOURCE:0'])
        with self.assertRaisesRegex(ValueError, 'changed'):
            confirm_broker_review([row], [row], ScheduleABrokerRosterReview(), request,
                actor='reviewer', source_context='source', snapshot='snapshot')

    def test_cleared_unreadable_address_can_pass_but_invalid_supplied_zip_cannot(self):
        from app.models import ExtractionValidationResult
        issue = ExtractionValidationResult(validator='source_zip_shape', status='ERROR', reason='Printed ZIP unreadable')
        row = self.row(validation_results=[issue])
        rows, _ = self.confirm(row)
        self.assertEqual(schedule_a_broker_update_values(rows)['ZipCode1'], '')
        with self.assertRaises(FTWPayloadValidationError):
            self.confirm(self.row(zip_code='123', validation_results=[issue]), accept=True)


class OptionalBrokerAddressServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_as_is_persists_then_builds_sendable_replacement_without_approval(self):
        import hashlib
        from unittest.mock import patch
        from app.models import Filing, FTWilliamsReview, FTWilliamsScheduleABrokerRowsRequest
        from app.repositories import MemoryRepository
        from app.services.ftwilliams_review import FTWilliamsReviewService
        from app.services.schedule_a_broker_roster import bind_roster_review
        from app.services.xml_builder import build_schedule_a_records_update_xml
        repo = MemoryRepository()
        pdf = b'Immutable optional-address test source'
        row = ScheduleABrokerRow(name='SOURCE BROKER', city='BOSTON', commission_total='100', fee_total='0')
        filing = await repo.create_filing(Filing(file_name='source.pdf', content_type='application/pdf',
            file_size=len(pdf), s3_key='qa/source.pdf', schedule_a_broker_rows=[row],
            schedule_a_broker_roster=ScheduleABrokerRosterReview(source_sha256=hashlib.sha256(pdf).hexdigest())))
        records = [{'ftw_seq_no':'2','query_results':{'ScheduleDesc':'QA'},'query_subparts':{'Broker':[{'NameXX':'OLD BROKER'}]}}]
        identity = dict(ftw_customer_id='qa', ftw_plan_id='qa', year='2025', ftw_seq_no='2')
        service = FTWilliamsReviewService()
        async def prepare(*args, **kwargs):
            saved = await repo.get_filing(filing.id)
            rows = service._normalized_schedule_a_broker_rows(saved.schedule_a_broker_rows)
            status = bind_roster_review(rows, saved.schedule_a_broker_roster, records, identity, True,
                source_context=service.source_context_sha256(saved))
            return FTWilliamsReview(filing_id=filing.id, current_query_success=True, current_query_complete=True,
                schedule_a_match={'ftw_seq_no':'2'}, schedule_a_records=records, schedule_a_broker_rows=rows,
                schedule_a_broker_roster=status, **identity)
        before = await prepare()
        payload = FTWilliamsScheduleABrokerRowsRequest(action='reviewed', rows=[row], review=BrokerRosterReviewRequest(
            expected_context=before.schedule_a_broker_roster.review_context_sha256, complete_list_confirmed=True,
            accept_partial_addresses=True, reason='Save and send as-is', row_sources=['SOURCE:0']))
        with patch('app.services.ftwilliams_review.get_repository', return_value=repo), \
                patch.object(service, 'prepare_review', side_effect=prepare), \
                patch('app.services.storage.StorageService.load_pdf', return_value=pdf):
            saved = await service.update_schedule_a_broker_rows(filing.id, payload, actor='authenticated-reviewer')
        self.assertEqual(saved.schedule_a_broker_roster.mode, 'REPLACE')
        self.assertTrue(saved.schedule_a_broker_roster.reviewer_confirmation['partial_addresses_accepted'])
        self.assertTrue(service._can_replace_broker_roster(saved.schedule_a_broker_rows, records, '2', saved.schedule_a_broker_roster))
        xml = build_schedule_a_records_update_xml(records, '2', [], year='2025',
            schedule_a_broker_rows=saved.schedule_a_broker_rows, replace_broker_roster=True)
        self.assertIn('<CityXX>BOSTON</CityXX>', xml)
        self.assertIn('<CodeXX>3</CodeXX>', xml)
        self.assertNotIn('OLD BROKER', xml)
