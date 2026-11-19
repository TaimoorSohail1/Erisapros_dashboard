import unittest
from app.models import ExtractedField,ExtractedFieldStatus,FormType
from app.repositories import MemoryRepository
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.schedule_a_classification import apply_schedule_a_classification

class AutomaticDerivedEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo=MemoryRepository()
        self.field=ExtractedField(filing_id='f',source_field_name='Earned',normalized_field_name='earned',
            mapped_rule_key='schedule_a_part_iii_9a_4_earned_1_2_3',form_type=FormType.SCHEDULE_A,
            value='',proposed_value='0',confidence=.95,status=ExtractedFieldStatus.MATCHED,status_reason='Automatically derived as zero')
        self.field=(await self.repo.add_fields([self.field]))[0]

    async def test_existing_derived_value_repairs_its_missing_persisted_evidence(self):
        before=FTWilliamsReviewService._automatic_field_state([self.field])
        derived=self.field.model_copy(deep=True);derived.page=2;derived.source_text='Nonexperience rated premium: 450.00'
        await FTWilliamsReviewService._persist_automatic_field_changes(self.repo,'f',before,[derived])
        saved=(await self.repo.list_fields('f'))[0]
        self.assertEqual(saved.value,'');self.assertEqual(saved.proposed_value,'0')
        self.assertEqual(saved.page,2);self.assertEqual(saved.source_text,derived.source_text)

    async def test_new_derived_value_and_evidence_are_saved_together(self):
        self.field.proposed_value='';self.field.status=ExtractedFieldStatus.MISSING
        before=FTWilliamsReviewService._automatic_field_state([self.field])
        derived=self.field.model_copy(deep=True);derived.proposed_value='0';derived.status=ExtractedFieldStatus.MATCHED
        derived.page=2;derived.source_text='Nonexperience rated premium: 450.00'
        await FTWilliamsReviewService._persist_automatic_field_changes(self.repo,'f',before,[derived])
        saved=(await self.repo.list_fields('f'))[0]
        self.assertEqual((saved.proposed_value,saved.page,saved.source_text),('0',2,derived.source_text))

    async def test_concurrent_reviewer_edit_is_not_overwritten_by_automatic_derivation(self):
        before=FTWilliamsReviewService._automatic_field_state([self.field])
        derived=self.field.model_copy(deep=True);derived.proposed_value='10';derived.page=2;derived.source_text='Validated components: 10'
        await self.repo.update_field('f',self.field.id,'99',status=ExtractedFieldStatus.EDITED)
        with self.assertRaisesRegex(ValueError,'changed'):
            await FTWilliamsReviewService._persist_automatic_field_changes(self.repo,'f',before,[derived])
        saved=(await self.repo.list_fields('f'))[0]
        self.assertEqual(saved.proposed_value,'99');self.assertEqual(saved.status,ExtractedFieldStatus.EDITED)

    def test_classification_does_not_replace_explicit_reviewer_value_with_automatic_zero(self):
        field=self.field.model_copy(update={'status':ExtractedFieldStatus.EDITED,'proposed_value':'99'})
        premium=ExtractedField(filing_id='f',source_field_name='Premium',normalized_field_name='premium',
            mapped_rule_key='schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier',
            form_type=FormType.SCHEDULE_A,value='450',proposed_value='450',page=2,source_text='Nonexperience rated premium: 450.00',confidence=.95)
        apply_schedule_a_classification([field,premium],['EXPLICIT_NONEXPERIENCE_RATED'])
        self.assertEqual(field.proposed_value,'99');self.assertEqual(field.status,ExtractedFieldStatus.EDITED)
