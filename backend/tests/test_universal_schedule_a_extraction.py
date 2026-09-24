from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import FieldRule, FieldRuleMappingMode
from app.services.extractor import (
    extract_layout_broker_rows,
    extract_rules_driven_schedule_a_fields,
    schedule_a_broker_compensation_fields,
)
from app.services.field_rules import DEFAULT_FIELD_RULES


PRINCIPAL_LAYOUT = """
    Contract #               1022824
    Name of Plan             FRAMESTORE INC                                   Principal Life Insurance Company
    Data Period              January 01, 2025 to December 31, 2025             Schedule A (Form 5500) Worksheet

Section 1: Coverage
    (a) Name of Insurance Carrier              Principal Life Insurance Company       (b) EIN     42-0127290       (c) NAIC Code 61271
    (d) Contract or ID Number                  1022824                                 Approximate Number of Total (e) 470
                                                                                       Persons Covered at End Employees 273
                                                                                       of Policy Year Dependents 197
    Policy or Contract Year                    From (f) January 01, 2025                To (g) December 31, 2025

Section 2: Insurance fee and commissions information
                                                        (a) Commissions Paid           (b) Fees Paid
    Total (from below)                                        22,776                         24,428

Section 3: Persons receiving commissions and fees
    (a) Name & Address of Agents or Brokers              (b) Amount of                 Fees Paid                    (e)
        to whom Commissions or Fees Paid                  Commissions Paid        (c) Amount/(d) Purpose             Org Code
    NFP CORPORATE SERVICES NY LLC                             13,369                    2,913 Service Fee             3 - Ins Agent
    265 FRANKLIN ST STE 1901                                                                                              Or
    BOSTON, MA 02110-3173                                                                                               Broker

    PROFESSIONAL GROUP PLANS INC                                                       445 *Bonus                     3 - Ins Agent
    225 WIRELESS BLVD                                                               19,056 *Override                        Or
    HAUPPAUGE, NY 11788-3914                                                                                           Broker

    MERCER HEALTH & BENEFITS LLC                             9,407                     2,014 Service Fee             3 - Ins Agent
    7201 W LAKE MEAD BLVD STE 400                                                                                           Or
    LAS VEGAS, NV 89128-8366                                                                                           Broker

    Reportable commissions and fees include all forms of compensation.

Section 8: Benefit and Contract Type
    (a) Health        (b) X Dental       (c) X Vision       (d) X Life Ins.
    (e) X Temporary Disability          (f) X Long Term Disability
    (k) X PPO Contract

Section 10: Non-Experience Rated Contracts
    (a) Total Premiums Paid to Carrier                  430,444
"""


class UniversalScheduleAExtractionTests(unittest.TestCase):
    def test_rules_driven_parser_extracts_principal_header_totals_and_dates(self):
        fields = extract_rules_driven_schedule_a_fields(
            [(3, PRINCIPAL_LAYOUT)],
            rules=DEFAULT_FIELD_RULES,
        )
        values = {field.field_name: field.value for field in fields}

        self.assertEqual(values["1a. Name of Insurance Company"], "Principal Life Insurance Company")
        self.assertEqual(values["1b. Insurance Carrier EIN"], "42-0127290")
        self.assertEqual(values["1c. NAIC Code"], "61271")
        self.assertEqual(values["1d. Contract/Policy Number"], "1022824")
        self.assertEqual(values["1e. Persons Covered (End of Policy Year)"], "470")
        self.assertEqual(values["1f. Policy Year Beginning Date"], "01/01/2025")
        self.assertEqual(values["1g. Policy Year Ending Date"], "12/31/2025")
        self.assertEqual(values["3b. Amount of Commissions"], "22,776")
        self.assertEqual(values["3c. Amount of Fees"], "24,428")
        self.assertEqual(values["10a. Total premiums or subscription charges paid to carrier"], "430,444")

    def test_rules_driven_parser_uses_a_new_mapped_alias_without_code_changes(self):
        rule = FieldRule(
            key="schedule_a_part_iii_9c_custom_risk_pool_charge",
            label="9c. Risk pool charge",
            ftw_field="Risk pool charge",
            xml_tag="RiskPoolChargeAmt",
            mapping_mode=FieldRuleMappingMode.FTW_MAPPED,
            priority="MEDIUM",
            source="Schedule A",
            form_section="Schedule A - Part III",
            field_type="Currency",
            aliases=["Carrier Risk Pool Charge"],
        )

        fields = extract_rules_driven_schedule_a_fields(
            [(1, "Section 9: Experience-Rated Contracts\nCarrier Risk Pool Charge: $12,345.67")],
            rules=[rule],
        )

        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0].field_name, "9c. Risk pool charge")
        self.assertEqual(fields[0].value, "12,345.67")
        self.assertIn("Carrier Risk Pool Charge", fields[0].source_text or "")

    def test_layout_parser_extracts_all_principal_broker_rows(self):
        rows = extract_layout_broker_rows([(3, PRINCIPAL_LAYOUT)])

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].name, "NFP CORPORATE SERVICES NY LLC")
        self.assertEqual(rows[0].address_line_1, "265 FRANKLIN ST STE 1901")
        self.assertEqual(rows[0].city, "BOSTON")
        self.assertEqual(rows[0].state, "MA")
        self.assertEqual(rows[0].zip_code, "02110-3173")
        self.assertEqual(rows[0].commission_total, "13,369")
        self.assertEqual(rows[0].fee_total, "2,913")
        self.assertEqual(rows[1].name, "PROFESSIONAL GROUP PLANS INC")
        self.assertEqual(rows[1].commission_total, "0")
        self.assertEqual(rows[1].fee_total, "19,501")
        self.assertEqual(rows[1].fee_rows[0].purpose, "Bonus; Override")
        self.assertEqual(rows[2].name, "MERCER HEALTH & BENEFITS LLC")
        self.assertEqual(rows[2].commission_total, "9,407")
        self.assertEqual(rows[2].fee_total, "2,014")

        fields = {field.field_name: field.value for field in schedule_a_broker_compensation_fields(rows)}
        self.assertEqual(fields["3a. Name of Agent/Broker/Person"], "NFP CORPORATE SERVICES NY LLC")
        self.assertEqual(fields["3b. Amount of Commissions"], "22,776")
        self.assertEqual(fields["3c. Amount of Fees"], "24,428")

    def test_layout_parser_rejects_column_headers_as_broker_names(self):
        rows = extract_layout_broker_rows(
            [
                (
                    1,
                    """
                    Persons receiving commissions and fees
                    (a) Name & Address of Agents or Brokers (b) Amount of Commissions Paid Fees Paid (e) Org Code
                    (b) Amount of                         22,776 24,428
                    """,
                )
            ]
        )

        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
