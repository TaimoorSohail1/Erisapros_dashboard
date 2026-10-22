"""Preserve sibling and restoration state; derive only for the selected/new record."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models import ExtractedField, FormType
from app.services.xml_builder import build_schedule_a_records_update_xml, schedule_a_replacement_data_gaps

ABSENT = object()


def record(seq, indicator=ABSENT, payment=None):
    values = {'ScheduleDesc': f'A{seq}', 'InsCarrierName': 'Existing Carrier', 'InsContractNum': f'000{seq}',
              'InsCarrierEIN': '12-3456789', 'InsPrsnCoveredEoyCnt': '10'}
    if indicator is not ABSENT: values['OverrideCommissionsAndFees'] = indicator
    return {'ftw_seq_no': str(seq), 'query_results': values,
            'query_subparts': {'Broker': [{'Name1': 'Existing Broker', 'CommPdAmt01': str(payment), 'Code01': '3'}]} if payment is not None else {}}


def build(records, matched='1', **kwargs):
    return build_schedule_a_records_update_xml(records, matched, [],
        ftw_customer_id='approved-test-customer', ftw_plan_id='approved-test-plan', year='2025', **kwargs)


def documents(xml):
    return {e.findtext('ScheduleDesc'): e for e in ET.fromstring(xml).findall('.//DOLScheduleAData')}


class PaymentIndicatorPreservationTests(unittest.TestCase):
    def test_unselected_missing_control_is_not_introduced(self):
        rows = documents(build([record(1), record(2)]))
        self.assertIsNone(rows['A2'].find('OverrideCommissionsAndFees'))
        self.assertEqual(rows['A1'].findtext('OverrideCommissionsAndFees'), '1')

    def test_unselected_blank_control_is_retained_as_blank(self):
        row = documents(build([record(1), record(2, '')]))['A2']
        self.assertIsNotNone(row.find('OverrideCommissionsAndFees'))
        self.assertEqual(row.findtext('OverrideCommissionsAndFees'), '')

    def test_unselected_explicit_zero_is_not_recomputed(self):
        row = documents(build([record(1), record(2, '0')]))['A2']
        self.assertEqual(row.findtext('OverrideCommissionsAndFees'), '0')

    def test_unselected_explicit_one_is_preserved_even_with_legacy_payment(self):
        row = documents(build([record(1), record(2, '1', payment='25')]))['A2']
        self.assertEqual(row.findtext('OverrideCommissionsAndFees'), '1')

    def test_restoration_preserves_each_original_control_state(self):
        originals = [record(1), record(2, ''), record(3, '0'), record(4, '1', payment='25')]
        before = deepcopy(originals)
        rows = documents(build(originals, matched=None))
        self.assertIsNone(rows['A1'].find('OverrideCommissionsAndFees'))
        self.assertIsNotNone(rows['A2'].find('OverrideCommissionsAndFees'))
        for key, expected in [('A2', ''), ('A3', '0'), ('A4', '1')]:
            with self.subTest(record=key): self.assertEqual(rows[key].findtext('OverrideCommissionsAndFees'), expected)
        self.assertEqual(originals, before, 'Input snapshots must never be modified')

    def test_new_schedule_does_not_recompute_existing_records(self):
        carrier = ExtractedField(filing_id='qa', source_field_name='Carrier', normalized_field_name='carrier',
            mapped_rule_key='schedule_a_part_i_1a_name_of_insurance_company', form_type=FormType.SCHEDULE_A,
            value='New Carrier', proposed_value='New Carrier')
        rows = documents(build([record(1), record(2, '0')], matched=None, add_new_fields=[carrier], new_schedule_desc='NEW'))
        self.assertIsNone(rows['A1'].find('OverrideCommissionsAndFees'))
        self.assertEqual(rows['A2'].findtext('OverrideCommissionsAndFees'), '0')
        self.assertEqual(rows['NEW'].findtext('OverrideCommissionsAndFees'), '1')

    def test_selected_control_still_follows_final_positive_and_negative_payments(self):
        for amount in ['25', '-25', '0']:
            with self.subTest(amount=amount):
                row = documents(build([record(1, '1', amount)]))['A1']
                self.assertEqual(row.findtext('OverrideCommissionsAndFees'), '1' if amount == '0' else '0')

    def test_shared_plan_year_updates_do_not_change_sibling_payment_control(self):
        begin = ExtractedField(filing_id='qa', source_field_name='Plan beginning', normalized_field_name='begin',
            mapped_rule_key='schedule_a_part_iv_4d_plan_year_beginning_date', form_type=FormType.SCHEDULE_A,
            value='01/01/2025', proposed_value='01/01/2025')
        row = documents(build([record(1), record(2, '0')], all_record_fields=[begin]))['A2']
        self.assertEqual(row.findtext('PlanYearBeginDate'), '01/01/2025')
        self.assertEqual(row.findtext('OverrideCommissionsAndFees'), '0')

    def test_send_guard_rejects_a_new_unselected_control(self):
        records = [record(1), record(2)]
        xml = ET.fromstring(build(records))
        row = xml.findall('.//DOLScheduleAData')[1]
        control = row.find('OverrideCommissionsAndFees')
        if control is None: control = ET.SubElement(row, 'OverrideCommissionsAndFees')
        control.text = '1'
        gaps = schedule_a_replacement_data_gaps(records, ET.tostring(xml, encoding='unicode'), matched_ftw_seq_no='1')
        self.assertTrue(any('sequence 2' in g and 'OverrideCommissionsAndFees' in g for g in gaps), gaps)

    def test_restoration_guard_rejects_a_new_control_when_no_record_selected(self):
        records = [record(1)]
        xml = ET.fromstring(build(records, matched=None))
        row = xml.find('.//DOLScheduleAData')
        control = row.find('OverrideCommissionsAndFees')
        if control is None: control = ET.SubElement(row, 'OverrideCommissionsAndFees')
        control.text = '1'
        self.assertTrue(schedule_a_replacement_data_gaps(records, ET.tostring(xml, encoding='unicode'), require_exact_payment_controls=True))

    def test_guard_rejects_dropped_blank_and_changed_explicit_sibling_controls(self):
        for original, replacement in [('', None), ('0', '1'), ('1', '0')]:
            with self.subTest(original=original, replacement=replacement):
                records = [record(1), record(2, original)]
                xml = ET.fromstring(build(records))
                row = xml.findall('.//DOLScheduleAData')[1]
                child = row.find('OverrideCommissionsAndFees')
                if child is not None: row.remove(child)
                if replacement is not None: ET.SubElement(row, 'OverrideCommissionsAndFees').text = replacement
                gaps = schedule_a_replacement_data_gaps(records, ET.tostring(xml, encoding='unicode'), matched_ftw_seq_no='1')
                self.assertTrue(any('sequence 2' in g and 'OverrideCommissionsAndFees' in g for g in gaps), gaps)

    def test_selected_control_changes_remain_allowed_by_send_guard(self):
        records = [record(1, '1', payment='25'), record(2, '0')]
        self.assertEqual(schedule_a_replacement_data_gaps(records, build(records), matched_ftw_seq_no='1'), [])


if __name__ == '__main__': unittest.main()
