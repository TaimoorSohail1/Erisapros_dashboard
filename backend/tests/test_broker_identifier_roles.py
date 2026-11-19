import unittest
from app.models import ScheduleABrokerRow,ExtractionValidationResult


class IdentifierRoleTests(unittest.TestCase):
    def row(self):
        return ScheduleABrokerRow(name='Example Benefit Services',organization_code='17-2430',commission_total='100',fee_total='0',
            validation_results=[ExtractionValidationResult(validator='source_organization_code_shape',status='ERROR')])

    def test_payee_identifier_is_not_an_organization_code(self):
        from app.services.schedule_a_broker_source_repair import repair_identifier_organization_codes
        row=self.row();before=(row.name,row.commission_total,row.fee_total)
        self.assertTrue(repair_identifier_organization_codes([row],[[(1,'Payee Code: 17-2430\nPayee Name: Example Benefit Services\nTotal Commission: $100')]]))
        self.assertEqual(row.organization_code,'3');self.assertTrue(row.organization_code_defaulted)
        self.assertEqual((row.name,row.commission_total,row.fee_total),before)
        self.assertFalse(any(v.validator=='source_organization_code_shape' and v.status=='ERROR' for v in row.validation_results))

    def test_explicit_code_manual_choices_and_unclear_source_are_preserved(self):
        from app.services.schedule_a_broker_source_repair import repair_identifier_organization_codes
        for text in ['Organization code: 17-2430\nExample Benefit Services','Example Benefit Services 17-2430',
                     'Payee Code: 17-2430\nExample Benefit Services\nCode (e): 6',
                     'Payee Code: 17-2430\nDifferent Recipient']:
            row=self.row();self.assertFalse(repair_identifier_organization_codes([row],[[(1,text)]]));self.assertEqual(row.organization_code,'17-2430')
        row=self.row();row.validation_results.append(ExtractionValidationResult(validator='reviewer_org_confirmation',status='PASS'))
        self.assertFalse(repair_identifier_organization_codes([row],[[(1,'Payee Code: 17-2430 Example Benefit Services')]]))

    def test_a_second_packet_with_an_explicit_org_label_prevents_default(self):
        from app.services.schedule_a_broker_source_repair import repair_identifier_organization_codes
        row=self.row()
        self.assertFalse(repair_identifier_organization_codes([row],[[(1,'Payee Code: 17-2430 Example Benefit Services')],
            [(1,'Organization code: 17-2430 Example Benefit Services')]]))
