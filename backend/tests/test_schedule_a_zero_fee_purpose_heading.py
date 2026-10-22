"""A commission heading must not populate the separate fee-purpose field."""
import unittest

from app.models import NormalizedExtractionResult, ScheduleABrokerMoneyRow, ScheduleABrokerRow, SourceEvidence
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result


class ZeroFeePurposeTests(unittest.TestCase):
    def row(self, **overrides):
        values = dict(name='Example Agency', address_line_1='10 Main St', city='Boston',state='MA',zip_code='02199',
            commission_total='3.43',fee_total='0',purpose='Commissions Paid',
            commission_rows=[ScheduleABrokerMoneyRow(coverage='POL-1',amount='-0.80',purpose='COMMISSIONS'),
                ScheduleABrokerMoneyRow(coverage='POL-2',amount='4.23',purpose='COMMISSIONS')],
            evidence=[SourceEvidence(provider='Source table',page=3,source_text='Commissions Paid 3.43; Fees Paid 0.00')],source_page=3)
        values.update(overrides)
        return ScheduleABrokerRow(**values)

    def resolve(self,row):
        return resolve_schedule_a_result(NormalizedExtractionResult(provider='test',fields=[],schedule_a_broker_rows=[row])).schedule_a_broker_rows[0]

    def test_commission_heading_not_a_zero_fee_purpose(self):
        before=self.row()
        actual=self.resolve(before.model_copy(deep=True))
        self.assertIsNone(actual.purpose)
        self.assertEqual(actual.commission_rows,before.commission_rows)
        self.assertEqual(actual.commission_total,'3.43')
        self.assertEqual(actual.fee_total,'0')
        self.assertTrue(any(v.validator=='zero_fee_purpose_not_applicable' for v in actual.validation_results))

    def test_real_fee_purpose_unchanged(self):
        row=self.row(fee_total='20',purpose='Consulting',fee_rows=[ScheduleABrokerMoneyRow(amount='20',purpose='Consulting')])
        self.assertEqual(self.resolve(row).purpose,'Consulting')

    def test_missing_fee_is_not_treated_as_zero(self):
        for value in [None,'','not supplied']:
            with self.subTest(value=value):
                self.assertEqual(self.resolve(self.row(fee_total=value)).purpose,'Commissions Paid')

    def test_offsetting_fee_components_preserve_their_purpose(self):
        row=self.row(fee_rows=[ScheduleABrokerMoneyRow(amount='10',purpose='Adjustment'),ScheduleABrokerMoneyRow(amount='-10',purpose='Adjustment')])
        self.assertEqual(self.resolve(row).purpose,'Commissions Paid')

    def test_other_purpose_is_not_silently_erased(self):
        self.assertEqual(self.resolve(self.row(purpose='Bonus Paid')).purpose,'Bonus Paid')


if __name__=='__main__':unittest.main()
