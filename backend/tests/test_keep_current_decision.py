import asyncio
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.repositories as repositories
from fastapi import HTTPException
from app.api.filings import update_field
from app.models import ExtractedField, FieldEditRequest, Filing, FormType, FTWFieldDecision, FTWilliamsComparisonField, FTWilliamsReview
from app.services.ftwilliams_review import FTWilliamsReviewService


class KeepCurrentDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        repositories._repository = repositories.MemoryRepository()
        self.repo = repositories.get_repository()
        self.filing = await self.repo.create_filing(Filing(file_name='source.pdf', content_type='application/pdf', file_size=10, s3_key='source-v1'))
        self.field = (await self.repo.add_fields([ExtractedField(filing_id=self.filing.id,
            source_field_name='Sponsor address', normalized_field_name='sponsor_address',
            mapped_rule_key='form_5500_part_i_1f_plan_sponsor_address',
            value='PRINTED SOURCE ADDRESS', proposed_value='PRINTED SOURCE ADDRESS', form_type=FormType.FORM_5500)]))[0]
        self.current = '123 LONG STREET SUITE 200, SPRINGFIELD, ILLINOIS 62704'
        self.review = FTWilliamsReview(filing_id=self.filing.id, current_query_sent=True,
            current_query_success=True, current_query_complete=True, ftw_customer_id='customer',
            ftw_plan_id='plan', year='2025', ftw_seq_no='2',
            fields=[FTWilliamsComparisonField(field_id=self.field.id, label='Sponsor address',
                form_type=FormType.FORM_5500, ftw_tag='SponsorUSAddressLine1',
                current_value=self.current, extracted_value=self.field.value, proposed_value=self.field.proposed_value,
                decision=FTWFieldDecision.BLOCKED, changed=True, update_included=True,
                validation_blocking=True, validation_message='maximum length is 35 characters')])
        await self.repo.upsert_ftwilliams_review(self.review)

    async def asyncTearDown(self):
        repositories._repository = None

    async def keep(self, expected=None, fresh=None):
        with patch.object(FTWilliamsReviewService, 'prepare_review', AsyncMock(return_value=fresh or self.review)) as prepare, \
             patch('app.api.filings.continue_ftw_automation', AsyncMock(side_effect=lambda fid, r:r)):
            result = await update_field(self.filing.id, self.field.id,
                FieldEditRequest(proposed_value=self.current if expected is None else expected, decision='KEEP_CURRENT'))
            self.assertTrue(prepare.await_args.kwargs['send_queries'])
            return result

    async def test_keep_long_current_address_is_no_write_not_an_edit(self):
        result = await self.keep()
        self.assertEqual(result['field'].value, 'PRINTED SOURCE ADDRESS')
        self.assertEqual(result['field'].proposed_value, 'PRINTED SOURCE ADDRESS')
        comparison = result['ftw_review'].fields[0]
        self.assertEqual(comparison.decision, FTWFieldDecision.KEEP_CURRENT)
        self.assertFalse(comparison.validation_blocking)
        self.assertFalse(comparison.update_included)
        self.assertFalse(comparison.changed)
        self.assertEqual(comparison.proposed_value, self.current)
        self.assertEqual((await self.repo.list_events(self.filing.id))[-1].type, 'KEEP_CURRENT')

    async def test_stale_current_is_rejected_without_saving_decision(self):
        with self.assertRaises(HTTPException) as error:
            await self.keep(expected='OLD CURRENT ADDRESS')
        self.assertEqual(error.exception.status_code, 409)
        self.assertIsNone(self.field.keep_current_context)

    async def test_failed_fresh_query_cannot_authorize_keep(self):
        fresh = self.review.model_copy(deep=True, update={'current_query_success':False})
        with self.assertRaises(HTTPException):
            await self.keep(fresh=fresh)
        self.assertIsNone(self.field.keep_current_context)

    async def test_decision_invalidates_when_source_target_or_current_changes(self):
        await self.keep()
        original = deepcopy(self.review)
        for kind in ['source', 'target', 'current', 'version']:
            with self.subTest(kind=kind):
                filing, field, review = deepcopy(self.filing), deepcopy(self.field), deepcopy(original)
                comparison = review.fields[0]
                comparison.decision = FTWFieldDecision.CONFLICT
                comparison.changed = comparison.update_included = True
                if kind == 'source': field.value = 'DIFFERENT SOURCE'
                if kind == 'target': review.ftw_plan_id = 'other-plan'
                if kind == 'current': comparison.current_value = 'NEW CURRENT'
                if kind == 'version': filing.s3_key = 'source-v2'
                FTWilliamsReviewService._apply_preserved_current_decisions(filing, [field], review)
                self.assertEqual(comparison.decision, FTWFieldDecision.CONFLICT)

    async def test_explicit_edit_clears_previous_keep_context(self):
        await self.keep()
        edited = await self.repo.update_field(self.filing.id, self.field.id, 'NEW SOURCE VALUE')
        self.assertIsNone(edited.keep_current_context)

    async def test_normal_edit_still_validates_invalid_long_new_value(self):
        with self.assertRaises(HTTPException) as error:
            await update_field(self.filing.id, self.field.id, FieldEditRequest(proposed_value='X'*200))
        self.assertEqual(error.exception.status_code, 422)

    async def test_rebuilt_send_validation_honors_no_write_keep(self):
        self.field.value = self.field.proposed_value = 'X'*100
        self.review.form_5500_current_values = {'SDAddressLine1':self.current}
        result = await self.keep()
        error = FTWilliamsReviewService()._review_validation_blocking_error(
            result['ftw_review'],fields=[self.field],include_brokers=False)
        self.assertIsNone(error)
