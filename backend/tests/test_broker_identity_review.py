"""Source-bound same/separate recipient choices; no vendor calls."""
import unittest
from decimal import Decimal
from copy import deepcopy
from app.models import (ScheduleABrokerRow, ScheduleABrokerRosterReview, ExtractionValidationResult,
    BrokerIdentityReviewRequest, BrokerRosterReviewRequest)
from app.services.broker_identity_review import identity_groups, save_identity_choice
from app.services.broker_review_confirmation import confirm_broker_review, review_token


class BrokerIdentityReviewTests(unittest.TestCase):
    def setUp(self):
        hold=ExtractionValidationResult(validator='recipient_identity_ambiguity',status='ERROR',reason='Confirm aggregation of different printed addresses')
        self.rows=[ScheduleABrokerRow(name='Mercer',address_line_1='4565 PAYSPHERE CIR',city='CHICAGO',state='IL',zip_code='60674',
            organization_code='3',commission_total='4035.74',fee_total='0',source_page=2,validation_results=[hold]),
            ScheduleABrokerRow(name='Mercer',address_line_1='4565 PAYSPHERE CIRCLE',city='CHICAGO',state='IL',zip_code='60674',
            organization_code='3',commission_total='0',fee_total='1133.09',source_page=2,validation_results=[hold.model_copy(deep=True)])]
        self.status=ScheduleABrokerRosterReview(source_sha256='a'*64,current_snapshot_sha256='b'*64)
        self.context='c'*64
        self.refresh()

    def refresh(self):
        self.status.identity_groups=identity_groups(self.rows,self.status,self.context,self.status.current_snapshot_sha256)
        self.status.review_context_sha256=review_token(self.rows,self.status,self.context,self.status.current_snapshot_sha256)

    def choose(self,choice='SAME',**overrides):
        data=dict(expected_context=self.status.review_context_sha256,group_id=self.status.identity_groups[0]['id'],
            choice=choice,address_source_index=1 if choice=='SAME' else None,verified=True,reason='Reviewer verified source recipient identity')
        data.update(overrides)
        self.status=save_identity_choice(self.rows,self.status,BrokerIdentityReviewRequest(**data),
            actor='reviewer-sub',source_context=self.context,snapshot=self.status.current_snapshot_sha256)
        self.refresh()

    def approve(self,rows=None):
        request=BrokerRosterReviewRequest(expected_context=self.status.review_context_sha256,
            complete_list_confirmed=True,reason='Reviewer approved source list',row_sources=['SOURCE:0','SOURCE:1'])
        return confirm_broker_review(rows or self.rows,self.rows,self.status,request,actor='reviewer-sub',
            source_context=self.context,snapshot=self.status.current_snapshot_sha256)

    def test_save_choice_does_not_merge_or_approve_and_survives_reload(self):
        original=deepcopy(self.rows)
        self.choose()
        self.assertEqual(self.rows,original)
        self.assertIsNone(self.status.reviewer_confirmation)
        self.assertEqual(self.status.mode,'PRESERVE')
        reloaded=ScheduleABrokerRosterReview.model_validate(self.status.model_dump(mode='json'))
        groups=identity_groups(self.rows,reloaded,self.context,self.status.current_snapshot_sha256)
        self.assertEqual(groups[0]['choice'],'SAME')
        self.assertEqual(groups[0]['address_source_index'],1)
        self.assertEqual(reloaded.identity_reviews[0]['actor'],'reviewer-sub')

    def test_same_merges_once_and_preserves_original_rows_and_totals(self):
        self.choose()
        rows,status=self.approve()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].address_line_1,'4565 PAYSPHERE CIRCLE')
        self.assertEqual((rows[0].commission_total,rows[0].fee_total),('4035.74','1133.09'))
        self.assertEqual(rows[0].purpose,'COMMISSIONS AND FEES')
        self.assertFalse(any(v.status=='ERROR' for v in rows[0].validation_results))
        self.assertEqual(len(status.reviewer_confirmation['original_rows']),2)
        self.assertEqual(status.reviewer_confirmation['identity_decisions'][0]['choice'],'SAME')

    def test_separate_keeps_each_address_and_payment(self):
        self.choose('SEPARATE')
        rows,_=self.approve()
        self.assertEqual(len(rows),2)
        self.assertEqual([r.address_line_1 for r in rows],[r.address_line_1 for r in self.rows])
        self.assertEqual([r.purpose for r in rows],['COMMISSIONS','FEES'])

    def test_missing_choice_address_attestation_or_stale_context_rejected(self):
        with self.assertRaisesRegex(ValueError,'source issues'):self.approve()
        for changes in ({'address_source_index':None},{'address_source_index':99},{'verified':False},
                        {'expected_context':'d'*64},{'group_id':'unknown'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.choose(**changes)

    def test_source_destination_or_roster_change_invalidates_identity_choice(self):
        self.choose()
        for context,snapshot in (('d'*64,self.status.current_snapshot_sha256),(self.context,'d'*64)):
            groups=identity_groups(self.rows,self.status,context,snapshot)
            self.assertIsNone(groups[0]['choice'])
        changed=deepcopy(self.rows);changed[0].commission_total='4035.75'
        self.assertIsNone(identity_groups(changed,self.status,self.context,self.status.current_snapshot_sha256)[0]['choice'])
        with self.assertRaisesRegex(ValueError,'identity.*changed'):self.approve(changed)

    def test_other_source_errors_are_never_cleared(self):
        self.rows[0].validation_results.append(ExtractionValidationResult(validator='source_component_total',status='ERROR',reason='Wrong total'))
        self.refresh();self.choose()
        with self.assertRaisesRegex(ValueError,'source issues'):self.approve()

    def test_signed_amounts_do_not_net_commission_against_fees(self):
        self.rows[0].commission_total='100.10';self.rows[1].fee_total='-100.10'
        self.refresh();self.choose()
        rows,_=self.approve()
        self.assertEqual(rows[0].purpose,'COMMISSIONS AND FEES')
        self.assertEqual((Decimal(rows[0].commission_total),Decimal(rows[0].fee_total)),(Decimal('100.10'),Decimal('-100.10')))


if __name__=='__main__':unittest.main()
