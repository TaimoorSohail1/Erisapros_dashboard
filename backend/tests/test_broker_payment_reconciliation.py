from decimal import Decimal
import unittest
from app.models import BrokerRosterReviewRequest, ExtractionValidationResult, ScheduleABrokerRosterReview, ScheduleABrokerRow
from app.services.broker_review_confirmation import confirm_broker_review, review_token


class BrokerPaymentReconciliationTests(unittest.TestCase):
    def test_known_printed_fee_is_required_even_after_error_flag_is_cleared(self):
        from app.services.broker_payment_reconciliation import payment_reconciliation
        row=self.original(error=False);status=ScheduleABrokerRosterReview()
        self.assertTrue(payment_reconciliation([row],[]).required)
        request=BrokerRosterReviewRequest(expected_context=review_token([row],status,'a','b'),
            complete_list_confirmed=True,reason='Checked source',row_sources=['SOURCE:0'])
        with self.assertRaisesRegex(ValueError,'Fees: source 25.00, allocated 0.00, remaining 25.00'):
            confirm_broker_review([row],[row],status,request,actor='qa',source_context='a',snapshot='b')

    def original(self, error=True):
        return ScheduleABrokerRow(name='Example Brokerage', commission_total='100', fee_total='0', source_page=2,
            validation_results=[ExtractionValidationResult(validator='broker_total_reconciliation',
                status='ERROR' if error else 'WARN', normalized_value='source_totals:100:25',
                reason='Printed totals differ from recipient allocations.')])

    def test_display_uses_printed_category_totals_and_reports_exact_missing_fee(self):
        from app.services.broker_payment_reconciliation import payment_reconciliation
        result=payment_reconciliation([self.original()], [])
        self.assertEqual(result.source, 'PRINTED_SOURCE')
        self.assertEqual((result.expected_commission,result.expected_fees),('100.00','25.00'))
        self.assertTrue(result.required)

    def test_conflicting_or_unavailable_source_totals_are_not_replaced_with_zero(self):
        from app.services.broker_payment_reconciliation import payment_reconciliation
        a=self.original();b=self.original();b.validation_results[0].normalized_value='source_totals:100:26'
        result=payment_reconciliation([a,b],[])
        self.assertEqual(result.source,'CONFLICTING_SOURCE')
        self.assertIsNone(result.expected_fees)
        a.validation_results[0].normalized_value=None
        self.assertEqual(payment_reconciliation([a],[]).source,'UNAVAILABLE')

    def test_wrong_allocation_error_includes_both_exact_differences(self):
        row=self.original();status=ScheduleABrokerRosterReview()
        request=BrokerRosterReviewRequest(expected_context=review_token([row],status,'a','b'),
            complete_list_confirmed=True,reason='Source checked',row_sources=['SOURCE:0'])
        with self.assertRaises(ValueError) as caught:
            confirm_broker_review([row],[row],status,request,actor='qa',source_context='a',snapshot='b')
        self.assertIn('Fees: source 25.00, allocated 0.00, remaining 25.00',str(caught.exception))

    def test_balanced_multiple_new_rows_can_be_saved_without_losing_source_amounts(self):
        row=self.original();status=ScheduleABrokerRosterReview()
        request=BrokerRosterReviewRequest(expected_context=review_token([row],status,'a','b'),
            complete_list_confirmed=True,reason='Recipients confirmed from source instructions',
            row_sources=['SOURCE:0','NEW','NEW'])
        rows,confirmed=confirm_broker_review([row,
            ScheduleABrokerRow(name='First confirmed fee recipient',commission_total='0',fee_total='10'),
            ScheduleABrokerRow(name='Second confirmed fee recipient',commission_total='0',fee_total='15')],
            [row],status,request,actor='qa',source_context='a',snapshot='b')
        self.assertEqual(sum(Decimal(r.fee_total) for r in rows),Decimal('25'))
        self.assertEqual(confirmed.reviewer_confirmation['row_sources'],['SOURCE:0','NEW','NEW'])

    def test_manual_source_confirmation_does_not_bypass_reconciliation(self):
        row=self.original();row.validation_results.append(ExtractionValidationResult(validator='broker_column_semantics',status='ERROR'))
        status=ScheduleABrokerRosterReview()
        request=BrokerRosterReviewRequest(expected_context=review_token([row],status,'a','b'),complete_list_confirmed=True,
            source_verified=True,reason='Checked printed categories and complete recipients',row_sources=['SOURCE:0'])
        with self.assertRaisesRegex(ValueError,'both Schedule A'):
            confirm_broker_review([row],[row],status,request,actor='qa',source_context='a',snapshot='b')
        corrected=row.model_copy(update={'fee_total':'25'})
        verified,_=confirm_broker_review([corrected],[row],status,request,actor='qa',source_context='a',snapshot='b')
        self.assertEqual(verified[0].fee_total,'25')
        self.assertTrue(any(v.validator=='broker_column_semantics' and v.status=='WARN' for v in verified[0].validation_results))
