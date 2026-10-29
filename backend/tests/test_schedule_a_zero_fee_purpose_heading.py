"""Preserve supplied purposes; cleaned non-purpose artifacts use the new default."""
import unittest

from app.models import NormalizedExtractionField, NormalizedExtractionResult, ScheduleABrokerMoneyRow, ScheduleABrokerRow, SourceEvidence
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

    def test_supplied_commission_purpose_is_preserved_with_zero_fees(self):
        before=self.row()
        actual=self.resolve(before.model_copy(deep=True))
        self.assertEqual(actual.purpose,before.purpose)
        self.assertEqual(actual.commission_rows,before.commission_rows)
        self.assertEqual(actual.commission_total,'3.43')
        self.assertEqual(actual.fee_total,'0')
        self.assertFalse(any(v.status=='ERROR' and v.validator=='zero_fee_purpose_not_applicable' for v in actual.validation_results))

    def test_zero_fee_placeholder_or_section_heading_is_not_a_recipient_purpose(self):
        for purpose in ['N/A', 'Not Applicable', 'Insurance fees and commission information',
                        'INSURANCE FEES AND COMMISSIONS INFORMATION:']:
            with self.subTest(purpose=purpose):
                before=self.row(purpose=purpose)
                actual=self.resolve(before.model_copy(deep=True))
                self.assertEqual(actual.purpose,'Commission')
                self.assertEqual(actual.commission_rows,before.commission_rows)
                self.assertEqual(actual.fee_total,'0')
                self.assertTrue(any(v.validator=='customer_missing_broker_values' for v in actual.validation_results))
                self.assertEqual(actual.evidence,before.evidence)

    def test_nonpurpose_normalization_records_the_original_literal_when_no_provider_trace_exists(self):
        result=resolve_schedule_a_result(NormalizedExtractionResult(provider='test',fields=[],
            schedule_a_broker_rows=[self.row(purpose='N/A')]))
        audit=result.raw['not_applicable_broker_purposes'][0]
        self.assertEqual(audit['observed_value'],'N/A')
        self.assertEqual(audit['name'],'Example Agency')
        self.assertEqual(audit['page'],3)

    def test_nonpurpose_clearing_is_bounded_to_established_zero_without_fee_components(self):
        for purpose in ['N/A', '.', 'Insurance fees and commission information', 'Sales commission paid', 'Base commissions']:
            for overrides in [dict(fee_total=None), dict(fee_total=''), dict(fee_total='25.01'),
                              dict(fee_rows=[ScheduleABrokerMoneyRow(amount='25.01',purpose='Administration'),
                                             ScheduleABrokerMoneyRow(amount='-25.01',purpose='Adjustment')])]:
                with self.subTest(purpose=purpose,overrides=overrides):
                    self.assertEqual(self.resolve(self.row(purpose=purpose,**overrides)).purpose,purpose)

    def test_a_real_purpose_containing_heading_words_is_not_erased(self):
        for purpose in ['Insurance fees and commission information consulting', 'N/A review consulting', 'Information Services', 'Consulting.', 'Sales commission paid consulting']:
            with self.subTest(purpose=purpose):
                self.assertEqual(self.resolve(self.row(purpose=purpose)).purpose,purpose)

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

    def test_generic_compensation_header_is_not_a_zero_fee_recipient_purpose(self):
        source = ('NAME AND ADDRESS OF SALES ADDITIONAL\n'
                  'EACH SOLICITING AGENT COMMISSION FEES COMPENSATION\n'
                  'OR BROKER RECEIVING PAID PAID PAID\nCOMPENSATION:\n'
                  'Example Agency 3.43 0.00 0.00\n10 Main St\nBoston MA 02199')
        row = self.row(purpose='Compensation', evidence=[
            SourceEvidence(provider='Current source table', page=3, source_text=source)])
        fields = [NormalizedExtractionField(field_name=name, value=value, page=3,
            source_text=(f'Derived recipient payment category: {value}\n' if name.startswith('3d.') else '') + source,
            confidence=.99) for name, value in [
                ('3b. Amount of Commissions', '3.43'), ('3c. Amount of Fees', '0'),
                ('3d. Purpose', 'Commissions')]]
        result = resolve_schedule_a_result(NormalizedExtractionResult(provider='test', fields=fields,
            schedule_a_broker_rows=[row], raw={'original_provider_purpose':'Compensation'}))
        self.assertEqual(result.schedule_a_broker_rows[0].purpose,'Commission')
        self.assertTrue(any(f.field_name.startswith('3d.') and f.value for f in result.fields))
        self.assertEqual(result.raw['not_applicable_broker_purposes'][0]['observed_value'], 'Compensation')
        self.assertEqual(result.schedule_a_broker_rows[0].evidence[0].source_text, source)

    def test_compensation_is_preserved_without_unambiguous_same_row_heading_evidence(self):
        header = ('NAME AND ADDRESS OF SALES ADDITIONAL\n'
                  'EACH SOLICITING AGENT COMMISSION FEES COMPENSATION\n'
                  'OR BROKER RECEIVING PAID PAID PAID\nCOMPENSATION:\n')
        for page, name, tail in [(2, 'Example Agency', '3.43 0.00 0.00'),
                                 (3, 'Different Agency', '3.43 0.00 0.00'),
                                 (3, 'Example Agency', '3.43 0.00 0.00 Purpose: Compensation'),
                                 (3, 'Example Agency', '3.43 0.00 0.00 Compensation')]:
            with self.subTest(page=page, name=name, tail=tail):
                row = self.row(purpose='Compensation', evidence=[SourceEvidence(
                    provider='Current source table', page=page, source_text=header + name + ' ' + tail)])
                self.assertEqual(self.resolve(row).purpose, 'Compensation')
        self.assertEqual(self.resolve(self.row(purpose='Compensation')).purpose, 'Compensation')
        for fee, components in [(None, []), ('25.01', []), ('0', [
                ScheduleABrokerMoneyRow(amount='10', purpose='Administration'),
                ScheduleABrokerMoneyRow(amount='-10', purpose='Administration')])]:
            with self.subTest(fee=fee, components=components):
                row = self.row(purpose='Compensation', fee_total=fee, fee_rows=components,
                    evidence=[SourceEvidence(provider='Current source table', page=3,
                        source_text=header + 'Example Agency 3.43 0.00 0.00')])
                self.assertEqual(self.resolve(row).purpose, 'Compensation')

    def test_single_period_placeholder_does_not_populate_a_zero_fee_purpose(self):
        before = self.row(purpose='.')
        actual = self.resolve(before.model_copy(deep=True))
        self.assertEqual(actual.purpose,'Commission')
        self.assertEqual(actual.commission_rows, before.commission_rows)
        self.assertEqual(actual.evidence, before.evidence)

    def summary(self,*,fee='0',purpose='COMMISSIONS',fee_rows=None,broker_purpose='same_as_summary'):
        source=f'{purpose} 3.43; Fees Paid {fee}; fee purpose N/A'
        fields=[NormalizedExtractionField(field_name=name,value=value,confidence=.99,page=3,source_text=source)
                for name,value in [('3b. Amount of Commissions','3.43'),('3c. Amount of Fees',fee),('3d. Purpose',purpose)]]
        result=NormalizedExtractionResult(provider='Source table',fields=fields,
            schedule_a_broker_rows=[self.row(fee_total=fee,purpose=purpose if broker_purpose=='same_as_summary' else broker_purpose,fee_rows=fee_rows or [])],
            raw={'extraction_stages':[{'stage':'provider_candidates','fields':[f.model_dump(mode='json') for f in fields]}]})
        return resolve_schedule_a_result(result)

    def test_zero_fee_summary_keeps_the_supplied_commission_purpose(self):
        result=self.summary()
        self.assertTrue(any(f.field_name.startswith('3d.') and f.value=='COMMISSIONS' for f in result.fields))
        self.assertEqual(result.raw['extraction_stages'][0]['fields'][2]['value'],'COMMISSIONS')
        self.assertFalse(any(r['field']=='3d. Purpose' for r in result.raw.get('not_applicable_fields',[])))

    def test_nonzero_or_offsetting_fees_keep_summary_purpose(self):
        for fee,rows in [('20',[ScheduleABrokerMoneyRow(amount='20',purpose='Consulting')]),
                         ('0',[ScheduleABrokerMoneyRow(amount='10',purpose='Adjustment'),ScheduleABrokerMoneyRow(amount='-10',purpose='Adjustment')])]:
            with self.subTest(fee=fee):
                result=self.summary(fee=fee,fee_rows=rows)
                self.assertTrue(any(f.field_name.startswith('3d.') and f.value=='COMMISSIONS' for f in result.fields))

    def test_base_commissions_summary_is_not_a_purpose_when_broker_fees_are_zero(self):
        for heading in ['BASE COMMISSIONS', 'Base Commission', 'Base Commissions Paid']:
            with self.subTest(heading=heading):
                result=self.summary(purpose=heading,broker_purpose=None)
                self.assertTrue(any(f.field_name.startswith('3d.') and f.value==heading for f in result.fields))
                self.assertEqual(result.raw['extraction_stages'][0]['fields'][2]['value'],heading)
                self.assertEqual(result.schedule_a_broker_rows[0].commission_total,'3.43')

    def test_supplied_sales_commission_purposes_survive_zero_fees(self):
        for category in ['Sales commission paid','Sales Commission','Sales Commissions','BASE COMMISSIONS']:
            with self.subTest(category=category):
                result=self.summary(purpose='Commissions',broker_purpose=category)
                self.assertEqual(result.schedule_a_broker_rows[0].purpose,category)
                self.assertTrue(any(f.field_name.startswith('3d.') and f.value=='Commissions' for f in result.fields))
                self.assertEqual(result.schedule_a_broker_rows[0].commission_total,'3.43')
                self.assertEqual(result.schedule_a_broker_rows[0].fee_total,'0')
                self.assertFalse(result.raw.get('not_applicable_broker_purposes'))
                self.assertEqual(result.raw['extraction_stages'][0]['fields'][2]['value'],'Commissions')

    def test_base_commissions_summary_is_not_erased_if_real_fees_exist(self):
        for category in ['BASE COMMISSIONS','Sales commission paid']:
            with self.subTest(category=category):
                result=self.summary(fee='20',purpose=category,broker_purpose='Consulting',
                                    fee_rows=[ScheduleABrokerMoneyRow(amount='20',purpose='Consulting')])
                self.assertTrue(any(f.field_name.startswith('3d.') and f.value==category for f in result.fields))

    def test_zero_fee_summary_does_not_erase_a_different_printed_purpose(self):
        result=self.summary(purpose='Bonus Paid')
        self.assertTrue(any(f.field_name.startswith('3d.') and f.value=='Bonus Paid' for f in result.fields))

    def test_missing_fee_summary_is_not_an_established_zero(self):
        result=self.summary(fee='')
        self.assertTrue(any(f.field_name.startswith('3d.') and f.value=='COMMISSIONS' for f in result.fields))


if __name__=='__main__':unittest.main()
