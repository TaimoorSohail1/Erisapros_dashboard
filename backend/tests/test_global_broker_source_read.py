import unittest
from app.services.extractor import extract_schedule_a_broker_rows_from_document, extract_eyemed_broker_rows, extract_metlife_standard_broker_rows

class GlobalSourceReaderTests(unittest.TestCase):
 def test_labelled_address_columns_keep_unit_in_street_and_city_separate(self):
  from app.services.schedule_a_native_brokers import labelled_payee_rows
  rows=labelled_payee_rows([(1,'''Commissions or fees paid by carrier to agents, brokers or other persons:
Payee Name                    Contract or ID #        Address Line 1                       City        State  Zip Code     Amount
Example Benefit Services      12345678901             15400 Main Dr. Ste. 125              Rockville   MD     20855        $126.07
Other Insurance Services      12345678901             505 Brand Blvd Suite 600             Glendale    CA     91203        $630.30
Total: $756.37''')])
  self.assertEqual(len(rows),2)
  self.assertEqual((rows[0].address_line_1,rows[0].city),('15400 Main Dr. Ste. 125','Rockville'))
  self.assertEqual(rows[1].city,'Glendale')
  self.assertEqual(rows[0].decision,'REVIEW_REQUIRED')
  self.assertTrue(any(v.validator=='payment_classification_ambiguity' and v.status=='ERROR' for v in rows[0].validation_results))
 def test_signed_subtotals_preserve_refunds_and_fee_categories(self):
  from app.services.extractor import _extract_metlife_standard_broker_block
  text='''Name: Example Benefit Services
Address: 123 MAIN ST City: BOSTON ST: MA ZIP: 02110
Commissions Paid Fees Paid Organization
codeCoverage Amount Purpose Coverage Amount Purpose
LIFE (81) Base Commissions 03
Dental (772) Base Commissions
(853) Sub-total (170) Sub-total'''
  rows=_extract_metlife_standard_broker_block(text,2)
  self.assertEqual(len(rows),1)
  self.assertEqual((rows[0].commission_total,rows[0].fee_total),('-853','-170'))
 def test_short_readable_pages_are_not_unreadable_due_to_word_threshold(self):
  from app.services.extractor import _broker_page_coverage
  self.assertTrue(_broker_page_coverage([(1,'Guardian Life Insurance Company\nAnnual report cover letter')])[0]['readable'])
  self.assertFalse(_broker_page_coverage([(1,'')])[0]['readable'])
 def test_equivalent_street_line_splits_corroborate_but_different_city_does_not(self):
  from app.models import ScheduleABrokerRow
  from app.services.schedule_a_broker_roster import _key
  a=ScheduleABrokerRow(name='Example Broker',address_line_1='123 MAIN ST',address_line_2='SUITE 100',city='Boston',state='MA',zip_code='02110',commission_total='100',fee_total='0')
  b=a.model_copy(update={'address_line_1':'123 MAIN ST SUITE 100','address_line_2':None})
  self.assertEqual(_key(a),_key(b))
  self.assertNotEqual(_key(a),_key(b.model_copy(update={'city':'Cambridge'})))
 def test_labelled_recipient_block_excludes_empty_none_slots_and_dates(self):
  from app.services.schedule_a_native_brokers import labelled_recipient_blocks
  text='''Total Amount of Commissions Paid: $100.00
Total Amount of Fees Paid: $0.00
1) Name and address of the agent, broker, or other person to whom commissions or fees were paid:
Example Benefit Services
123 Main St Suite 100
Boston, MA 02110
Amount of sales and base commissions paid to Example Benefit Services: $100.00
Fees and other compensation paid to Example Benefit Services: $0.00
2) Name and address of the agent, broker, or other person to whom commissions or fees were paid:
None
Amount of sales and base commissions paid to None $0.00
Fees and other compensation paid to None: $0.00
March 31, 2026'''
  rows=labelled_recipient_blocks([(3,text)])
  self.assertEqual(len(rows),1)
  self.assertEqual(rows[0].city,'Boston')
  self.assertEqual(rows[0].commission_total,'100')

if __name__=='__main__':unittest.main()
