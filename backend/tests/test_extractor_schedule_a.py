import unittest

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import (
    DocumentType,
    FieldRule,
    FieldRuleMappingMode,
    FormType,
    NormalizedExtractionField,
    ScheduleABrokerMoneyRow,
    ScheduleABrokerRow,
)
from app.services.extractor import (
    extract_cigna_schedule_a_broker_rows,
    extract_cigna_schedule_a_fields,
    extract_columnar_broker_compensation_rows,
    extract_bcbs_michigan_addendum_broker_rows,
    extract_bcbs_michigan_schedule_a_fields,
    extract_bcbs_michigan_schedule_a_summaries,
    extract_eyemed_broker_rows,
    extract_eyemed_schedule_a_fields,
    extract_eyemed_schedule_a_summaries,
    extract_explicit_benefit_indicator_fields,
    extract_fields_from_groundx_xray,
    extract_bcbsma_commission_breakdown_broker_rows,
    extract_bcbsma_schedule_a_worksheet_fields,
    extract_bcbsma_schedule_a_worksheet_summaries,
    extract_prudential_broker_rows,
    extract_prudential_schedule_a_fields,
    extract_prudential_schedule_a_summaries,
    extract_schedule_a_broker_rows,
    extract_summary_table_broker_rows,
    extract_standard_broker_rows,
    extract_standard_schedule_a_fields,
    extract_standard_schedule_a_records,
    extract_standard_schedule_a_summaries,
    extract_united_omaha_broker_rows,
    extract_united_omaha_schedule_a_fields,
    extract_united_omaha_schedule_a_records,
    extract_united_omaha_schedule_a_summaries,
    build_groundx_schema_query,
    extract_fields_from_document_text,
    is_obvious_template_placeholder,
    merge_schedule_a_fields,
    parse_schedule_a_text,
    schedule_a_broker_compensation_fields,
)
from app.services.field_rules import DEFAULT_FIELD_RULES
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.mapping import map_extraction_to_rules
from app.services.schedule_a_classification import classify_schedule_a_fields


class ScheduleAExtractionTests(unittest.TestCase):
    def test_schedule_a_parser_extracts_and_merges_columnar_broker_disclosure_rows(self):
        pages = [
            (
                2,
                """
                5. INSURANCE FEES AND COMMISSION INFORMATION:
                NAME AND ADDRESS OF EACH SOLICITING AGENT OR BROKER RECEIVING COMPENSATION:
                SALES COMMISSION PAID FEES PAID ADDITIONAL COMPENSATION PAID

                ALLIANT INSURANCE
                SERVICES, INC.
                $ 0.00 $ 0.00 $ 0.00
                32 OLD SLIP
                NEW YORK, NY 10005

                NFP CORPORATE SERVICES
                (NY) LLC
                $ 1,689.77 $ 0.00 $ 0.00
                PO BOX 9101
                PLAINVIEW, NY 11803

                USI INSURANCE SERVICES LLC $ 45.45 $ 0.00 $ 0.00
                3RD FLOOR
                600 THIRD AVENUE
                NEW YORK, NY 10016

                USI INSURANCE SERVICES LLC $ 0.00 $ 0.00 $ 52.98
                3RD FLOOR
                600 THIRD AVENUE
                NEW YORK, NY 10016

                MANAGEMENT COMPENSATION
                GROUP/NFP
                $ 0.00 $ 0.00 $ 145.16
                STE 200
                3445 PEACHTREE RD NE
                """,
            ),
            (
                3,
                """
                ATLANTA, GA 30326

                NFP CORPORATE SERVICES
                (NY) LLC
                $ 1,035.96 $ 0.00 $ 0.00
                PO BOX 9101
                PLAINVIEW, NY 11803

                NFP INSURANCE SERVICES,
                INC
                $ 0.00 $ 0.00 $ 510.90
                1250 CAPITAL OF TEXAS HWY
                BLDG 2 STE 125
                AUSTIN, TX 78746

                6. COVERAGE/BENEFITS PROVIDED: DISABILITY
                """,
            ),
        ]

        rows = extract_columnar_broker_compensation_rows(pages)
        by_name = {row.name: row for row in rows}

        self.assertEqual(len(rows), 4)
        self.assertNotIn("ALLIANT INSURANCE SERVICES, INC.", by_name)
        self.assertEqual(by_name["NFP CORPORATE SERVICES (NY) LLC"].commission_total, "2,725.73")
        self.assertEqual(by_name["NFP CORPORATE SERVICES (NY) LLC"].fee_total, "0")
        self.assertEqual(by_name["NFP CORPORATE SERVICES (NY) LLC"].zip_code, "11803")
        self.assertEqual(by_name["USI INSURANCE SERVICES LLC"].commission_total, "45.45")
        self.assertEqual(by_name["USI INSURANCE SERVICES LLC"].fee_total, "52.98")
        self.assertEqual(by_name["USI INSURANCE SERVICES LLC"].address_line_1, "600 THIRD AVENUE")
        self.assertEqual(by_name["USI INSURANCE SERVICES LLC"].address_line_2, "3RD FLOOR")
        self.assertEqual(by_name["MANAGEMENT COMPENSATION GROUP/NFP"].city, "ATLANTA")
        self.assertEqual(by_name["MANAGEMENT COMPENSATION GROUP/NFP"].fee_total, "145.16")
        self.assertEqual(by_name["NFP INSURANCE SERVICES INC"].fee_total, "510.90")

        fields = {field.field_name: field.value for field in schedule_a_broker_compensation_fields(rows)}
        self.assertEqual(fields["3b. Amount of Commissions"], "2,771.18")
        self.assertEqual(fields["3c. Amount of Fees"], "709.04")

    def test_columnar_broker_disclosure_fails_closed_when_a_paid_row_has_no_name(self):
        rows = extract_columnar_broker_compensation_rows(
            [
                (
                    1,
                    """
                    INSURANCE FEES AND COMMISSION INFORMATION:
                    SALES COMMISSION PAID FEES PAID ADDITIONAL COMPENSATION PAID

                    $ 100.00 $ 0.00 $ 0.00
                    1 MAIN STREET
                    BOSTON, MA 02110

                    6. COVERAGE/BENEFITS PROVIDED: LIFE
                    """,
                )
            ]
        )

        self.assertEqual(rows, [])

    def test_verified_broker_table_replaces_incorrect_ai_broker_values(self):
        ai_fields = [
            NormalizedExtractionField(field_name="3a. Name of Agent/Broker/Person", value="March", confidence=0.99),
            NormalizedExtractionField(field_name="3b. Amount of Commissions", value="31", confidence=0.99),
        ]
        broker_fields = schedule_a_broker_compensation_fields(
            [
                ScheduleABrokerRow(
                    name="NFP CORPORATE SERVICES (NY) LLC",
                    commission_total="2,725.73",
                    fee_total="0",
                    commission_rows=[ScheduleABrokerMoneyRow(amount="2,725.73", purpose="Sales Commission")],
                    source_page=2,
                )
            ]
        )

        merged = {field.field_name: field.value for field in merge_schedule_a_fields(ai_fields, broker_fields)}

        self.assertEqual(merged["3a. Name of Agent/Broker/Person"], "NFP CORPORATE SERVICES (NY) LLC")
        self.assertEqual(merged["3b. Amount of Commissions"], "2,725.73")

    def test_cigna_summary_page_wins_over_state_appendices(self):
        pages = [
            (
                1,
                """
                Cigna Health and Life Insurance Company
                Schedule A Insurance Information
                Part I Information Concerning Insurance Contract Coverage, Fees and Commissions
                (Summary of All Insurance Contracts Included in Part III)
                1. Coverage Information (a) Name of Insurance Carrier: Cigna Health and Life Insurance Company and affiliates (\"Cigna\")
                (b) EIN
                59-1031071
                (c) NAIC Code
                67369
                (d) Contract or Identification Number
                3341244
                (e) Approx. no. of persons covered at end of policy or contract year
                475 Employees
                Policy/Contract Year
                (f) From (g) To
                01/01/2025 12/31/2025
                2. Insurance fees and commissions information.
                (a) Total Amount of commissions paid $18,603 (b) Total Amount of fees paid $1,397
                3. Persons receiving commissions and fees.
                Non Experience - Rated
                NFP CORPORATE SERVICES (NY), LLC,
                PO BOX 786677, PHILADELPHIA, PA, $18,603 $1,397 General Agent Payments 3-Insurance Agent or Broker
                19178
                Part III Welfare Benefit Contract Information
                9. Experience-Rated Contracts This section not applicable for this Plan
                10. Nonexperience-rated contracts
                (a) Total premiums or subscriptions charges paid to carrier $375,747
                PART IV Provision of Information
                11. Did the insurance company fail to provide any information necessary to complete Schedule A? Yes No
                12. If the answer to line 11 is \"Yes\", specify the information not provided. Answer \"Not Applicable\"
                """,
            ),
            (
                2,
                """
                Appendix to 1a, b and c
                Cigna Health and Life Insurance Company
                06-1141174 95660 3341244 1 Employees 01/01/2025 12/31/2025
                """,
            ),
        ]

        fields = extract_cigna_schedule_a_fields(pages)
        by_name = {field.field_name: field.value for field in fields}
        rows = extract_cigna_schedule_a_broker_rows(pages)

        self.assertEqual(by_name["1a. Name of Insurance Company"], "Cigna Health and Life Insurance Company")
        self.assertEqual(by_name["1b. Insurance Carrier EIN"], "59-1031071")
        self.assertEqual(by_name["1c. NAIC Code"], "67369")
        self.assertEqual(by_name["1d. Contract/Policy Number"], "3341244")
        self.assertEqual(by_name["1e. Persons Covered (End of Policy Year)"], "475")
        self.assertEqual(by_name["1f. Policy Year Beginning Date"], "01/01/2025")
        self.assertEqual(by_name["1g. Policy Year Ending Date"], "12/31/2025")
        self.assertEqual(by_name["3b. Amount of Commissions"], "18,603")
        self.assertEqual(by_name["3c. Amount of Fees"], "1,397")
        self.assertEqual(by_name["3d. Purpose"], "General Agent Payments")
        self.assertEqual(by_name["3e. Organizational Code"], "3")
        self.assertEqual(by_name["10a. Total premiums or subscription charges paid to carrier"], "375,747")
        self.assertEqual(
            by_name["11. Did the insurance company fail to provide any information necessary to complete Schedule A?"],
            "No",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "NFP CORPORATE SERVICES (NY), LLC")
        self.assertEqual(rows[0].commission_total, "18,603")
        self.assertEqual(rows[0].fee_total, "1,397")

    def test_equitable_schedule_a_worksheet_extracts_coverage_period_and_checkbox_no_values(self):
        health_rule = FieldRule(
            key="ftw_discovered_schedule_a_health_ind",
            label="Health Indicator",
            ftw_field="Health Indicator",
            xml_tag="HealthInd",
            mapping_mode=FieldRuleMappingMode.FTW_MAPPED,
            priority="MEDIUM",
            source="Schedule A",
            form_section="Schedule A - Discovered FTW fields",
            field_type="Dynamic",
            existing_behavior="Review Only",
            new_behavior="Keep FTW",
            aliases=["Health"],
        )
        vision_rule = health_rule.model_copy(
            update={
                "key": "ftw_discovered_schedule_a_vision_ind",
                "label": "Vision Indicator",
                "ftw_field": "Vision Indicator",
                "xml_tag": "VisionInd",
                "aliases": ["Vision"],
            }
        )
        text = """
        Schedule A (Form5500)Worksheet
        (D) Contract or ID Number 011335 Total (E) 279 Combined Numbers
        Approx. no. of Persons cov. At End of Policy Year Employees
        (E) Policy or Contract Year (F) From (F) 2023-10-01 (G) To (G) 2024-09-30
        Section 8: Benefit and Contract Type
        (A) [] Health (other than dental or vision) (C) [] Vision (D) [X] Life Ins.
        """

        fields = parse_schedule_a_text(text, rules=[health_rule, vision_rule])
        fields.extend(
            extract_explicit_benefit_indicator_fields(
                text,
                rules=[health_rule, vision_rule],
            )
        )
        by_name = {field.field_name: field.value for field in fields}

        self.assertEqual(by_name["1e. Persons Covered (End of Policy Year)"], "279")
        self.assertEqual(by_name["1f. Policy Year Beginning Date"], "10/01/2023")
        self.assertEqual(by_name["1g. Policy Year Ending Date"], "09/30/2024")
        self.assertEqual(by_name["Health Indicator"], "No")
        self.assertEqual(by_name["Vision Indicator"], "No")

    def test_obvious_irs_template_placeholders_are_not_real_values(self):
        self.assertTrue(is_obvious_template_placeholder("ABCDEFGHI ABCDEFGHI ABCDEFGHI"))
        self.assertTrue(is_obvious_template_placeholder("123456789012345"))
        self.assertFalse(is_obvious_template_placeholder("Federal Insurance Company"))
        self.assertFalse(is_obvious_template_placeholder("0927447"))

    def test_local_parser_uses_discovered_ftw_aliases_for_explicit_benefits(self):
        health_rule = FieldRule(
            key="ftw_discovered_schedule_a_health_ind",
            label="Health Indicator",
            ftw_field="Health Indicator",
            xml_tag="HealthInd",
            mapping_mode=FieldRuleMappingMode.FTW_MAPPED,
            priority="MEDIUM",
            source="Schedule A",
            form_section="Schedule A - Discovered FTW fields",
            field_type="Dynamic",
            existing_behavior="Review Only",
            new_behavior="Keep FTW",
            aliases=["Health", "Medical coverage"],
        )
        vision_rule = health_rule.model_copy(
            update={
                "key": "ftw_discovered_schedule_a_vision_ind",
                "label": "Vision Indicator",
                "ftw_field": "Vision Indicator",
                "xml_tag": "VisionInd",
                "aliases": ["Vision", "Eye care"],
            }
        )

        fields = extract_fields_from_document_text(
            b"Benefits: Health, Dental, Vision, Prescription Drug",
            "schedule-a.txt",
            rules=[health_rule, vision_rule],
        )
        by_name = {field.field_name: field.value for field in fields}

        self.assertEqual(by_name["Health Indicator"], "Yes")
        self.assertEqual(by_name["Vision Indicator"], "Yes")

    def test_local_parser_keeps_one_best_value_per_schedule_a_field(self):
        text = """
        Name of Insurance Carrier: Kaiser Foundation Health Plan, Inc.
        Total Amount of Commissions Paid: $4,810.38
        March 12, 2026
        """

        fields = extract_fields_from_document_text(text.encode(), "schedule-a.txt")
        names = [field.field_name for field in fields]

        self.assertEqual(names.count("1a. Name of Insurance Company"), 1)
        self.assertEqual(names.count("3b. Amount of Commissions"), 1)

    def test_merge_replaces_invalid_ai_contract_with_validated_document_value(self):
        ai_fields = [
            NormalizedExtractionField(
                field_name="1d. Contract/Policy Number",
                value="4",
                confidence=0.99,
                page=1,
            ),
            NormalizedExtractionField(
                field_name="1d. Contract/Policy Number",
                value="10420761002",
                confidence=0.98,
                page=1,
            ),
        ]
        document_fields = [
            NormalizedExtractionField(
                field_name="1d. Contract/Policy Number",
                value="1042075/6-1001/1002",
                confidence=0.92,
                page=1,
            )
        ]

        merged = merge_schedule_a_fields(ai_fields, document_fields)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].value, "1042075/6-1001/1002")

    def test_groundx_query_contains_published_aliases_for_the_relevant_form(self):
        custom_alias = "Carrier Registry Number"
        rules = [
            rule.model_copy(update={"aliases": [*rule.aliases, custom_alias]})
            if rule.key == "schedule_a_part_i_1c_naic_code"
            else rule
            for rule in DEFAULT_FIELD_RULES
        ]

        query = build_groundx_schema_query(
            "schedule-a.pdf",
            rules,
            form_type=FormType.SCHEDULE_A,
        )

        self.assertIn(custom_alias, query)
        self.assertNotIn("Plan Administrator Name", query)

    def test_groundx_query_contains_alias_for_new_discovered_ftw_field(self):
        rule = FieldRule(
            key="ftw_discovered_schedule_a_ins_fail_provide_info_text",
            label="Reason information was not provided",
            ftw_field="Insurance Carrier Missing Information Explanation",
            xml_tag="InsFailProvideInfoText",
            mapping_mode=FieldRuleMappingMode.FTW_MAPPED,
            priority="MEDIUM",
            source="Schedule A",
            form_section="Schedule A - Discovered FTW fields",
            field_type="Dynamic",
            existing_behavior="Review Only",
            new_behavior="Keep FTW",
            aliases=["Carrier explanation for missing information"],
        )

        query = build_groundx_schema_query(
            "schedule-a.pdf",
            [rule],
            form_type=FormType.SCHEDULE_A,
        )

        self.assertIn("Reason information was not provided", query)
        self.assertIn("Carrier explanation for missing information", query)

    def test_groundx_xray_maps_gross_premium_table_to_nonexperience_line_10a(self):
        payload = {
            "chunks": [
                {
                    "pageNumbers": [2],
                    "text": "Total Premium received - Type of Benefit - Gross Premium",
                    "json": [
                        {
                            "record_type": "total_premium_received",
                            "rows": [
                                {"type_of_benefit": "Dental", "gross_premium": "$95,409.74"},
                                {"type_of_benefit": "Total", "gross_premium": "$95,409.74"},
                            ],
                        }
                    ],
                }
            ],
            "documentPages": [{"pageNumber": 2}],
        }

        fields = extract_fields_from_groundx_xray(payload)
        by_name = {field.field_name: field.value for field in fields}

        self.assertEqual(
            by_name["10a. Total premiums or subscription charges paid to carrier"],
            "95,409.74",
        )

    def test_groundx_ocr_maps_vendor_total_premium_to_nonexperience_line_10a(self):
        text = """
        5500 Schedule A Insurance Information
        Name of insurance carrier Sun Life Assurance Company of Canada
        Total Premium received 01/01/2025 to 12/31/2025
        Type of Benefit Dental Gross Premium $95,409.74
        Total $95,409.74
        """

        fields = parse_schedule_a_text(text)
        by_name = {field.field_name: field.value for field in fields}

        self.assertEqual(
            by_name["10a. Total premiums or subscription charges paid to carrier"],
            "95,409.74",
        )
        mapped = map_extraction_to_rules(
            "peerless-test",
            fields,
            form_type=FormType.SCHEDULE_A,
            source_document_type=DocumentType.SCHEDULE_A,
        )["fields"]
        self.assertEqual(classify_schedule_a_fields(mapped).contract_type.value, "NONEXPERIENCE_RATED")

    def test_groundx_ocr_maps_policy_year_premium_sentence_to_nonexperience_line_10a(self):
        text = """
        Annual Policy Information Report
        Premiums, Commissions and Fees are as paid during the policy year.
        Total premiums paid to Insurance Company during the policy year: $12,013.49
        See below for total commissions and fees paid by Insurance Company.
        """

        fields = parse_schedule_a_text(text)
        by_name = {field.field_name: field.value for field in fields}

        self.assertEqual(
            by_name["10a. Total premiums or subscription charges paid to carrier"],
            "12,013.49",
        )

    def test_groundx_ocr_maps_nonparticipating_subscription_charge_wording_to_line_10a(self):
        text = """
        Official ERISA Notification
        7. NON-PARTICIPATING CONTRACTS (PREMIUMS):
        (A) TOTAL PREMIUM OR SUBSCRIPTION CHARGES PAID
        TO CARRIER. $49,429.65
        (B) PREMIUMS DUE AND UNPAID AT END OF THE PLAN YEAR. $.00
        """

        fields = parse_schedule_a_text(text)
        by_name = {field.field_name: field.value for field in fields}

        self.assertEqual(
            by_name["10a. Total premiums or subscription charges paid to carrier"],
            "49,429.65",
        )
        mapped = map_extraction_to_rules(
            "oxford-test",
            fields,
            form_type=FormType.SCHEDULE_A,
            source_document_type=DocumentType.SCHEDULE_A,
        )["fields"]
        self.assertEqual(classify_schedule_a_fields(mapped).contract_type.value, "NONEXPERIENCE_RATED")

    def test_groundx_ocr_maps_experience_section_amounts_to_line_9(self):
        text = """
        Schedule A Part III
        9 Experience-rated contracts
        9a. Premiums: (1) Amount Received $1,739,422
        9b(1). Benefit Charges (1) Claims paid $1,503,774
        9c(1)(H). Total retention $235,648
        """

        fields = parse_schedule_a_text(text)
        by_name = {field.field_name: field.value for field in fields}

        self.assertEqual(by_name["9a. Premiums: (1) Amount Received"], "1,739,422")
        self.assertEqual(by_name["9b(1). Benefit Charges (1) Claims paid"], "1,503,774")
        self.assertEqual(by_name["9c(1)(H). Total retention"], "235,648")
        mapped = map_extraction_to_rules(
            "experience-test",
            fields,
            form_type=FormType.SCHEDULE_A,
            source_document_type=DocumentType.SCHEDULE_A,
        )["fields"]
        self.assertEqual(classify_schedule_a_fields(mapped).contract_type.value, "EXPERIENCE_RATED")

    def test_schedule_a_parser_extracts_fee_org_code_and_derived_purpose(self):
        text = """
        SCHEDULE A (Form 5500) 2024
        A Name of plan MIDWEST HOSE AND SPECIALTY HEALTH AND WELFARE BENEFITS PLAN
        B Three-digit plan number (PN) 501
        C Plan sponsor's name as shown on line 2a of Form 5500
        MIDWEST HOSE & SPECIALTY INC.
        D Employer Identification Number (EIN) 73-1185740

        (a) Name of insurance carrier
        UNITEDHEALTHCARE INSURANCE COMPANY
        (b) EIN 36-2739571
        (c) NAIC code 79413
        (d) Contract or identification number 1246876
        (e) Approximate number of persons covered at end of policy or contract year * 61
        (f) From 10/01/2024
        (g) To 09/30/2025

        fees paid/amount:  $0.00
        (a) Name and address of the agents, brokers or other persons to whom commissions or fees were paid:
        NFP CORPORATION SERVICES (OK) LLC
        4811 GAILLARDIA PKWY STE 300
        OKLAHOMA CITY OK 73142-1875
        (b) Amount of commissions paid: $111,892.96
        (c) Fees paid / Amount: $0.00
        (d) Purpose: N/A
        (e) Organizational Code: 3

        10a. Total premiums or subscription charges paid to carrier: $3,102,445.38
        """

        fields = parse_schedule_a_text(text)
        by_name = {field.field_name: field.value for field in fields}

        self.assertEqual(by_name["1a. Name of Insurance Company"], "UNITEDHEALTHCARE INSURANCE COMPANY")
        self.assertEqual(by_name["1b. Insurance Carrier EIN"], "36-2739571")
        self.assertEqual(by_name["1c. NAIC Code"], "79413")
        self.assertEqual(by_name["1d. Contract/Policy Number"], "1246876")
        self.assertEqual(by_name["3a. Name of Agent/Broker/Person"], "NFP CORPORATION SERVICES (OK) LLC")
        self.assertEqual(by_name["3b. Amount of Commissions"], "111,892.96")
        self.assertEqual(by_name["3c. Amount of Fees"], "0.00")
        self.assertEqual(by_name["3d. Purpose"], "COMMISSIONS")
        self.assertEqual(by_name["3e. Organizational Code"], "3")
        self.assertEqual(by_name["10a. Total premiums or subscription charges paid to carrier"], "3,102,445.38")

    def test_schedule_a_parser_extracts_kaiser_carrier_specific_labels(self):
        text = """
        INSURANCE INFORMATION
        Part I: Information Concerning Insurance Coverage, Fees, and Commissions
        Name of Insurance Carrier: Kaiser Foundation Health Plan, Inc.
        Plan Sponsor's Name: SPECIAL SERVICE FOR GROUPS, INC.
        Information Concerning Insurance Contract Coverage
        Kaiser Foundation Health Plan Region: CA
        Insurance Carrier: Kaiser Foundation Health Plan, Inc.
        Insurance Carrier Employer Identification Number: 94-1340523
        Insurance Carrier NAIC Code: 00000
        Plan Sponsor Contract or Identification Number: 608066
        Approximate number of persons covered at end of policy contract year: 37
        Contract Year from 01/2025 - 12/2025
        Information Concerning Insurance Contract Fees and Commissions
        Total Amount of Commissions Paid: $4,810.38
        Total Amount of Fees Paid: $0.00
        1) Name and address of the agent, broker, or other person to whom commissions or fees were paid:
        Gallagher Benefit Services, Inc.
        500 N BRAND BLVD STE 100
        GLENDALE, CA 91203-3931
        Amount of sales and base commissions paid to Gallagher Benefit Services, Inc.: $4,810.38
        Fees and other compensation paid to Gallagher Benefit Services, Inc.: $0.00
        Part III: Welfare Benefit Contract Information
        Premium applied by Kaiser Foundation Health Plan, Inc. during your plan's contract year: $262,735.11
        """

        fields = parse_schedule_a_text(text)
        by_name = {field.field_name: field.value for field in fields}

        self.assertEqual(by_name["1a. Name of Insurance Company"], "Kaiser Foundation Health Plan, Inc.")
        self.assertEqual(by_name["1b. Insurance Carrier EIN"], "94-1340523")
        self.assertEqual(by_name["1c. NAIC Code"], "00000")
        self.assertEqual(by_name["1d. Contract/Policy Number"], "608066")
        self.assertEqual(by_name["1e. Persons Covered (End of Policy Year)"], "37")
        self.assertEqual(by_name["1f. Policy Year Beginning Date"], "01/01/2025")
        self.assertEqual(by_name["1g. Policy Year Ending Date"], "12/31/2025")
        self.assertEqual(by_name["3a. Name of Agent/Broker/Person"], "Gallagher Benefit Services, Inc.")
        self.assertEqual(by_name["3b. Amount of Commissions"], "4,810.38")
        self.assertEqual(by_name["3c. Amount of Fees"], "0.00")
        self.assertEqual(by_name["10a. Total premiums or subscription charges paid to carrier"], "262,735.11")

    def test_schedule_a_parser_extracts_aetna_table_values_without_swapping_columns(self):
        text = """
        INSURANCE INFORMATION
        AETNA LIFE INSURANCE COMPANY AND AFFILIATES
        For Fiscal Plan Year beginning 01/01/2025 and ending 12/31/2025
        PART I Information Concerning Insurance Contract Coverage, Fees, and Commissions.
        1. Coverage: HNO Prospective
        (a) Name of Insurance Carrier:
        Aetna Health, Inc.
        (b) EIN: See Attached
        (c) NAIC Code: See Attached Listing
        (d) Contract Number
        or Identification:
        0847233HNO
        (e) Approximate Number of
        persons covered at the end
        of policy or contract year:
        610
        Policy or contract Year
        (f) From:
        01/01/2025
        (g) To:
        12/31/2025
        2. Insurance Fees and commissions paid to agents and brokers:
        Contract or Identification
        (a) Name and address of the agents or brokers to whom commissions or fees were paid.
        (b) Amount of commissions paid
        (c) & (d) Fees Paid Amount Purpose
        0847233HNO
        GALLAGHER BENEFIT SERVICES INC 505 N BRAND BLVD GLENDALE, CA 91203
        $47,063.49
        """

        fields = parse_schedule_a_text(text)
        by_name = {field.field_name: field.value for field in fields}

        self.assertEqual(by_name["1d. Contract/Policy Number"], "0847233HNO")
        self.assertEqual(by_name["1e. Persons Covered (End of Policy Year)"], "610")
        self.assertEqual(by_name["1f. Policy Year Beginning Date"], "01/01/2025")
        self.assertEqual(by_name["1g. Policy Year Ending Date"], "12/31/2025")
        self.assertEqual(by_name["3b. Amount of Commissions"], "47,063.49")

        mapped = map_extraction_to_rules(
            "test-filing",
            fields,
            form_type=FormType.SCHEDULE_A,
            source_document_type=DocumentType.SCHEDULE_A,
        )["fields"]
        mapped_by_label = {field.mapped_label: field.proposed_value for field in mapped}
        self.assertEqual(mapped_by_label["1d. Contract/Policy Number"], "0847233HNO")
        self.assertNotEqual(mapped_by_label["1d. Contract/Policy Number"], "610")

    def test_schedule_a_parser_prioritizes_metlife_broker_totals_over_page_fragments(self):
        pages = [
            """
            Schedule A
            Insurance Information
            Cover Letter
            Customer Name: THE ADVERTISING COUNCIL, INC.
            Attention: NANCY WING

            METROPOLITAN LIFE INSURANCE COMPANY
            EIN NAIC Code Contract or identification # Approximate number of persons covered at end of policy or contract year Policy or contract year
            13-5581829 65978 5955240 255 01/01/2025 12/31/2025
            """,
            """
            Totals
            Total Amount of commissions paid:1,998 Total fees paid/amount:345
            """,
            """
            Name and address of the agents, brokers or other persons to whom commissions or fees were paid
            Name: NFP CORPORATE SERVICES NY LLCAddress Line 1:PO BOX 9101
            Zip Code: 11803-9001 Organization
            code: 03

            Commissions Paid
            Coverage Amount Purpose
            Vision 1,576Base Commissions
            1,576Sub Total

            Fees Paid
            Coverage Amount Purpose
            Multiple 44 Non-Monetary
            Compensation
            44 Sub Total
            """,
            """
            Zip Code: 10166-3201 Organization
            code: 03
            Commissions Paid
            Coverage Amount Purpose
            0 Sub Total
            Fees Paid
            Coverage Amount Purpose
            Vision 2 Marketing Fees
            2 Sub Total

            Part III
            8. Experience-rated contracts
            N/A
            9. Nonexperience-rated contracts
            a. Total premiums or subscription charges paid to carrier:
            Vision 15,870
            """,
        ]
        fields = []
        for page, text in enumerate(pages, start=1):
            fields.extend(parse_schedule_a_text(text, page))
        fields.extend(parse_schedule_a_text("\n\n".join(pages), None))

        mapped = map_extraction_to_rules(
            "test-filing",
            fields,
            form_type=FormType.SCHEDULE_A,
            source_document_type=DocumentType.SCHEDULE_A,
        )["fields"]
        mapped_by_label = {field.mapped_label: field.proposed_value for field in mapped}

        self.assertEqual(mapped_by_label["3a. Name of Agent/Broker/Person"], "NFP CORPORATE SERVICES NY LLC")
        self.assertEqual(mapped_by_label["1d. Contract/Policy Number"], "5955240")
        self.assertNotEqual(mapped_by_label["1d. Contract/Policy Number"], "576Ba")
        self.assertEqual(mapped_by_label["3b. Amount of Commissions"], "1,998")
        self.assertEqual(mapped_by_label["3c. Amount of Fees"], "345")
        self.assertEqual(mapped_by_label["3d. Purpose"], "COMMISSIONS & FEES")
        self.assertEqual(mapped_by_label["3e. Organizational Code"], "03")
        self.assertEqual(mapped_by_label["10a. Total premiums or subscription charges paid to carrier"], "15,870")
        self.assertEqual(
            mapped_by_label["11. Did the insurance company fail to provide any information necessary to complete Schedule A?"],
            "No",
        )

    def test_schedule_a_parser_extracts_metlife_repeatable_broker_rows(self):
        text = """
        Name and address of the agents, brokers or other persons to whom commissions or fees were paid
        Name: NFP CORPORATE SERVICES NY LLCAddress Line 1:PO BOX 9101
        City: PLAINVIEW State: NY
        Zip Code: 11803-9001 Organization
        code: 03
        Commissions Paid
        Coverage Amount Purpose
        Vision 1,576Base Commissions
        1,576Sub Total
        Fees Paid
        Coverage Amount Purpose
        Multiple 44 Non-Monetary
        Compensation
        44 Sub Total

        Name and address of the agents, brokers or other persons to whom commissions or fees were paid
        Name: NFP INS SERVICES INC Address Line 1: 1250 S CAPITAL OF TEXAS HWY
        Address Line 2: BLDG 2 STE 125 City: AUSTIN State: TX
        Zip Code: 78746-6446 Organization
        code: 03
        Commissions Paid
        Coverage Amount Purpose
        Dental 289Base Commissions
        Vision 133Base Commissions
        422Sub Total
        Fees Paid
        Coverage Amount Purpose
        0 Sub Total

        Name and address of the agents, brokers or other persons to whom commissions or fees were paid
        Name: NFP CORPORATE SERVICES NY LLC Address Line 1: 200 PARK AVE RM 3202
        Address Line 2: ATTN ACCOUNTING City: NEW YORK State: NY
        Zip Code: 10166-3201 Organization
        code: 03
        Commissions Paid
        Coverage Amount Purpose
        0 Sub Total
        Fees Paid
        Coverage Amount Purpose
        Vision 299Supplemental
        Compensation
        299 Sub Total

        Name and address of the agents, brokers or other persons to whom commissions or fees were paid
        Name: NFP CORPORATE SERVICES LLC Address Line 1: 200 PARK AVE RM 3202
        Address Line 2: ATTN ACCOUNTING City: NEW YORK State: NY
        Zip Code: 10166-3201 Organization
        code: 03
        Commissions Paid
        Coverage Amount Purpose
        0 Sub Total
        Fees Paid
        Coverage Amount Purpose
        Vision 2 Marketing Fees
        2 Sub Total
        """

        rows = extract_schedule_a_broker_rows(text)

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0].name, "NFP CORPORATE SERVICES NY LLC")
        self.assertEqual(rows[0].commission_total, "1,576")
        self.assertEqual(rows[0].fee_total, "44")
        self.assertEqual(rows[1].name, "NFP INS SERVICES INC")
        self.assertEqual(rows[1].commission_total, "422")
        self.assertEqual(rows[1].fee_total, "0")
        self.assertEqual(rows[2].fee_total, "299")
        self.assertEqual(rows[3].fee_total, "2")

    def test_schedule_a_parser_stops_last_broker_before_part_iii(self):
        text = """
        Name and address of the agents, brokers or other persons to whom commissions or fees were paid
        Name: NFP CORPORATE SERVICES LLC Address Line 1: 200 PARK AVE RM 3202
        Address Line 2: ATTN ACCOUNTING City: NEW YORK State: NY
        Zip Code: 10166-3201 Organization code: 03
        Commissions Paid
        Coverage Amount Purpose
        0 Sub Total
        Fees Paid
        Coverage Amount Purpose
        Vision 2 Marketing Fees
        2 Sub Total
        Part III Welfare Benefit Contract Information
        Vision 15,870
        If more than one contract covers the same group of employees, complete the information below.
        """

        rows = extract_schedule_a_broker_rows(text)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].fee_total, "2")
        self.assertEqual(rows[0].fee_rows, [])
        self.assertNotIn("15,870", str(rows[0].model_dump()))
        self.assertNotIn("Welfare Benefit Contract Information", str(rows[0].model_dump()))

    def test_schedule_a_parser_extracts_bcbsma_worksheet_experience_rated_fields(self):
        text = """
        ACCOUNT NAME: R. H. White Construction Co. I
        ACCOUNT #: 0123307
        PERIOD: 01/01/2025 - 12/31/2025 @ 03/31/2026
        NAIC CODE: 53228
        EIN CODE: 04-1045815
        MEDICAL DENTAL SENIOR
        LAST MONTH OF PERIOD ENROLLMENT
        Employees 361 0 0
        Employee & Dependents 773 0 0
        PREMIUM
        Total Premium $6,554,192 $0 $0
        BENEFIT CHARGES
        Incurred Claims $5,498,349 $0 $0
        Incurred But Not Reported $44,878 $0 $0
        Claims Charged $5,543,227 $0 $0
        RETENTION ALLOCATION
        Base Commission $94,601 $0 $0
        Taxes $185 $0 $0
        Other Retention Charges $916,179 $0 $0
        Blue Cross Blue Shield of Massachusetts, Inc.
        FULLY INSURED #5500A WORKSHEET
        """

        fields = extract_bcbsma_schedule_a_worksheet_fields(text, page=1)
        by_name = {field.field_name: field.value for field in fields}
        summaries = extract_bcbsma_schedule_a_worksheet_summaries(text, page=1)

        self.assertEqual(by_name["1a. Name of Insurance Company"], "Blue Cross Blue Shield of Massachusetts, Inc.")
        self.assertEqual(by_name["1b. Insurance Carrier EIN"], "04-1045815")
        self.assertEqual(by_name["1c. NAIC Code"], "53228")
        self.assertEqual(by_name["1d. Contract/Policy Number"], "0123307")
        self.assertEqual(by_name["1e. Persons Covered (End of Policy Year)"], "773")
        self.assertEqual(by_name["9a. Premiums: (1) Amount Received"], "6,554,192")
        self.assertEqual(by_name["9b(1). Benefit Charges (1) Claims paid"], "5,498,349")
        self.assertEqual(by_name["9b(2). Increase (decrease) in claim reserves"], "44,878")
        self.assertEqual(by_name["9b(3). Incurred claims (add(1) and (2))"], "5,543,227")
        self.assertEqual(by_name["9c(1)(A). Commissions"], "94,601")
        self.assertEqual(by_name["9c(1)(E). Taxes"], "185")
        self.assertEqual(by_name["9c(1)(G). Other retention charges"], "916,179")
        self.assertEqual(by_name["9c(1)(H). Total retention"], "1,010,965")
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].source, "BCBSMA #5500A worksheet")
        self.assertEqual(summaries[0].account_number, "0123307")

    def test_schedule_a_parser_extracts_bcbsma_commission_breakdown_broker_row(self):
        text = """
        ACCOUNT NAME: R. H. White Construction Co. I
        ACCOUNT #: 0123307
        PERIOD: 01/01/2025 - 12/31/2025 @ 03/31/2026
        NAIC CODE: 53228
        EIN CODE: 04-1045815
        MEDICAL DENTAL SENIOR #VALUE!
        COMMISSION BREAKDOWN
        R S C INS BKGE DBA RISK STRATAGIES CO. $94,601.06 $8,932.00 $0.00
        ### OTHER COMMISSION *
        R S C INS BKGE DBA RISK STRATAGIES CO. $23,270.00
        ### NON MONETARY C0MPENSATION *
        R S C INS BKGE DBA RISK STRATAGIES CO. $209.15
        COMMISSIONS AND BONUS BREAKDOWN
        """

        rows = extract_bcbsma_commission_breakdown_broker_rows(text, page=3)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "R S C INS BKGE DBA RISK STRATAGIES CO.")
        self.assertEqual(rows[0].commission_total, "126,803")
        self.assertEqual(rows[0].fee_total, "209")
        self.assertEqual(rows[0].fee_rows[0].purpose, "Non-Monetary Compensation")

    def test_schedule_a_parser_extracts_bcbs_michigan_experience_addendum_without_10a_leak(self):
        pages = [
            (
                2,
                """
                SCHEDULE A (ERISA FORM 5500)
                INSURANCE INFORMATION
                GROUP NAME: OptimizeRX Corporation
                PART I: Insurance Information
                1. COVERAGE INFORMATION
                (a) NAME OF INSURANCE CARRIER BLUE CROSS BLUE SHIELD OF MICHIGAN
                (b) EMPLOYER IDENTIFICATION NUMBER (EIN) 38-2069753
                (c) NATIONAL ASSOCIATION OF INSURANCE COMMISSIONERS (NAIC) CODE 54291
                (d) CONTRACT OR IDENTIFICATION NUMBER 616888
                (e) APPROX. NUMBER OF PERSONS COVERED 250
                (f) POLICY OR CONTRACT YEAR FROM 12/1/2024
                (g) POLICY OR CONTRACT YEAR TO 11/30/2025
                2. INSURANCE FEE AND COMMISSION INFORMATION (SEE SCHEDULE A ADDENDUM)
                3. PERSONS RECEIVING COMMISSIONS AND FEES (SEE SCHEDULE A ADDENDUM)
                PART II: INVESTMENT AND ANNUITY CONTRACT INFORMATION NOT APPLICABLE
                PART III: WELFARE BENEFIT CONTRACT INFORMATION
                9. EXPERIENCE-RATED CONTRACTS
                (a) PREMIUMS:
                (i) AMOUNT RECEIVED $1,739,422
                (ii) AND (iii) NOT APPLICABLE
                (iv) AMOUNT EARNED $1,739,422
                (b) BENEFIT CHARGES:
                (i) CLAIMS PAID $2,088,380
                (ii) INCREASE (DECREASE) IN CLAIM RESERVES $2,012
                (iii) INCURRED CLAIMS (ADD (i) AND (ii)) $2,090,392
                (iv) CLAIMS CHARGED (NET OF EXCESS CLAIMS) $2,032,510
                (c) REMAINDER OF PREMIUM
                (i) RETENTION CHARGES
                A. COMMISSIONS NOT APPLICABLE
                B. ADMINISTRATIVE SERVICE OR OTHER FEES $199,707
                C. OTHER SPECIFIC ACQUISITION COSTS $0
                D. OTHER EXPENSES (SUBSIDIES, ETC.) $0
                E. ESTIMATED TAXES, FEES AND ASSESSMENTS $21,924
                F. CHARGES FOR RISK OR OTHER CONTINGENCIES $60,433
                G. OTHER RETENTION CHARGES (POOLING CHARGE) $178,752
                H. TOTAL RETENTION $460,817
                (ii) DIVIDENDS OR RETROACTIVE RATE REFUNDS (CREDITED) $0
                (d) STATUS OF POLICYHOLDER RESERVES AT END OF YEAR
                (i) AMOUNT HELD TO PROVIDE BENEFITS AFTER RETIREMENT NOT APPLICABLE
                (ii) CLAIMS RESERVES $109,724
                (iii) OTHER RESERVES $0
                (e) DIVIDENDS OR RETROACTIVE RATE REFUNDS DUE $0
                10. NONEXPERIENCE-RATED CONTRACTS NOT APPLICABLE
                PART IV: PROVISION OF INFORMATION
                """,
            ),
            (
                3,
                """
                Client Name: OPTIMIZERX CORPORATION
                Group Number: 007049950
                CID: 616888
                Contract Year From: 12/01/2024
                Contract Year To: 11/30/2025
                AGENT/BROKER COMMISSION & INCENTIVE PAYMENTS
                 -- Name and address of agent or broker: NFP CORPORATE SERVICES NY LLC
                340 MADISON AVE 21ST FLOOR
                NEW YORK, NY 10173-0173
                 -- Amount of Sales and Base Commissions Paid $0.00
                 -- Fees and Other Commissions Paid Amount $1,255.50
                 -- Non-Monetary Compensations to Plan
                 (gifts, meals, entertainments, etc.) $0.00
                 -- Organization Code (for Schedule (A) 3
                AGENT/BROKER COMMISSION & INCENTIVE PAYMENTS
                 -- Name and address of agent or broker: KATHERINE HENRY
                Nfp Corporate Services (ny), Llc 340 Madison Avenue
                New York, NY -
                 -- Amount of Sales and Base Commissions Paid $53,816.53
                 -- Fees and Other Commissions Paid Amount $0.00
                 -- Non-Monetary Compensations to Plan
                 (gifts, meals, entertainments, etc.) $0.00
                 -- Organization Code ( for Schedule (A) 3
                Blue Cross Blue Shield Michigan
                ADDENDUM TO SCHEDULE A/C (ERISA FORM 5500)
                """,
            ),
        ]

        fields = extract_bcbs_michigan_schedule_a_fields(pages)
        by_name = {field.field_name: field.value for field in fields}
        rows = extract_bcbs_michigan_addendum_broker_rows("\n".join(text for _, text in pages))
        summaries = extract_bcbs_michigan_schedule_a_summaries(pages)

        self.assertEqual(by_name["1a. Name of Insurance Company"], "BLUE CROSS BLUE SHIELD OF MICHIGAN")
        self.assertEqual(by_name["1d. Contract/Policy Number"], "616888")
        self.assertEqual(by_name["9a. Premiums: (1) Amount Received"], "1,739,422")
        self.assertEqual(by_name["9b(1). Benefit Charges (1) Claims paid"], "2,088,380")
        self.assertEqual(by_name["9c(1)(B). Administrative service or other fees"], "199,707")
        self.assertEqual(by_name["9c(1)(H). Total retention"], "460,817")
        self.assertEqual(by_name["9d(2). Claim reserves"], "109,724")
        self.assertEqual(by_name["3b. Amount of Commissions"], "53,816.53")
        self.assertEqual(by_name["3c. Amount of Fees"], "1,255.50")
        self.assertNotIn("10a. Total premiums or subscription charges paid to carrier", by_name)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].name, "NFP CORPORATE SERVICES NY LLC")
        self.assertEqual(rows[0].fee_total, "1,255.50")
        self.assertEqual(rows[1].name, "KATHERINE HENRY")
        self.assertEqual(rows[1].commission_total, "53,816.53")
        self.assertEqual(summaries[0].source, "BCBS Michigan Schedule A/C addendum")

    def test_schedule_a_parser_extracts_eyemed_worksheet_as_combined_ftw_record(self):
        pages = [
            (
                1,
                """
                Vision Insurance Information For Form 5500
                Report Start DateReport End Date
                1/1/25 12/31/25
                Information Compiled By: EyeMed Vision Care on behalf of the Fidelity Security Life Insurance Company
                Name of Plan Contract orID # Enrollment Group
                Approximate number ofsubscribers covered atend of policy or contractyear:
                Approximate number ofsubscribers anddependents covered at endof policy or contract year:EIN NAIC Amount
                RH WHITE CONSTRUCTION COMPANIES10258481001
                RH WHITECONSTRUCTIONCOMPANIES 175 366 43094984471870 $23,875.21
                RH WHITE CONSTRUCTION COMPANIESCOBRA 10258491001
                RH WHITECONSTRUCTIONCOMPANIES COBRA 1 1 43094984471870 $315.31
                176 367 Total: $24,190.52
                Payee Name Contract or ID # Address Line 1 City StateZip Code Amount
                RSC Insurance Brokerage, Inc. - Boston,10258481001160 Federal Street Boston MA 02110 $2,979.55
                RSC Insurance Brokerage, Inc. - Boston,10258491001160 Federal Street Boston MA 02110 $32.42
                Selman & Company, LLC 10258481001One Integrity Parkway Cleveland OH 44143 $1,845.07
                Selman & Company, LLC 10258491001One Integrity Parkway Cleveland OH 44143 $19.50
                Total: $4,876.54
                Commissions or fees paid by carrier to agents, brokers or other persons:
                Payments Received by carrier from plan or plan sponsor:
                """,
            )
        ]

        fields = extract_eyemed_schedule_a_fields(pages)
        by_name = {field.field_name: field.value for field in fields}
        summaries = extract_eyemed_schedule_a_summaries(pages)
        rows = extract_eyemed_broker_rows(pages)

        self.assertEqual(by_name["1a. Name of Insurance Company"], "Fidelity Security Life Insurance Company")
        self.assertEqual(by_name["1b. Insurance Carrier EIN"], "43-0949844")
        self.assertEqual(by_name["1c. NAIC Code"], "71870")
        self.assertEqual(by_name["1d. Contract/Policy Number"], "1025848/9-1001")
        self.assertEqual(by_name["1e. Persons Covered (End of Policy Year)"], "367")
        self.assertEqual(by_name["1f. Policy Year Beginning Date"], "01/01/2025")
        self.assertEqual(by_name["1g. Policy Year Ending Date"], "12/31/2025")
        self.assertEqual(by_name["3b. Amount of Commissions"], "4,877")
        self.assertEqual(by_name["3c. Amount of Fees"], "0")
        self.assertEqual(by_name["10a. Total premiums or subscription charges paid to carrier"], "24,191")
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].source, "EyeMed vision worksheet")
        self.assertEqual(len(summaries[0].benefit_rows), 2)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0].name, "RSC Insurance Brokerage, Inc. - Boston")
        self.assertEqual(rows[0].address_line_1, "160 Federal Street")
        self.assertEqual(rows[0].city, "Boston")
        self.assertEqual(rows[0].commission_total, "2,980")
        self.assertEqual(rows[-1].commission_total, "20")

    def test_schedule_a_parser_extracts_eyemed_rows_with_blank_identifier_cells(self):
        pages = [
            (
                1,
                """
                Vision Insurance Information For Form 5500
                Information Compiled By: EyeMed Vision Care on behalf of the Fidelity Security Life Insurance Company
                Report Start DateReport End Date
                1/1/2025 12/31/2025
                Name of Plan Contract orID # Enrollment Group
                Approximate number ofsubscribers covered atend of policy or contractyear:
                Approximate number ofsubscribers anddependents covered at endof policy or contract year:EIN NAIC Amount
                OXFORD BIOMEDICA (US) LLC10420751001OXFORD BIOMEDICA (US)LLC 134 335 43094984471870 $11,646.30
                OXFORD BIOMEDICA (US) LLC10420751002OXFORD BIOMEDICA US,INC. 0 0 $0.00
                OXFORD BIOMEDICA (US) LLC COBRA10420761001OXFORD BIOMEDICA (US)LLC COBRA 0 0 $94.40
                OXFORD BIOMEDICA (US) LLC COBRA10420761002OXFORD BIOMEDICA US,INC. COBRA 1 4 43094984471870 $224.48
                Total: $11,965.18
                Payee Name Contract or ID # Address Line 1 City StateZip Code Amount
                RSC Insurance Brokerage, Inc. - Boston,10420751001160 Federal Street Boston MA 02110 $4,870.24
                RSC Insurance Brokerage, Inc. - Boston,10420761001160 Federal Street Boston MA 02110 $18.88
                RSC Insurance Brokerage, Inc. - Boston,10420761002160 Federal Street Boston MA 02110 $31.05
                Total: $4,920.17
                Commissions or fees paid by carrier to agents, brokers or other persons:
                Payments Received by carrier from plan or plan sponsor:
                """,
            )
        ]

        fields = extract_eyemed_schedule_a_fields(pages)
        by_name = {field.field_name: field.value for field in fields}
        summaries = extract_eyemed_schedule_a_summaries(pages)
        rows = extract_eyemed_broker_rows(pages)

        self.assertEqual(by_name["1a. Name of Insurance Company"], "Fidelity Security Life Insurance Company")
        self.assertEqual(by_name["1b. Insurance Carrier EIN"], "43-0949844")
        self.assertEqual(by_name["1c. NAIC Code"], "71870")
        self.assertEqual(by_name["1d. Contract/Policy Number"], "1042075/6-1001/1002")
        self.assertEqual(by_name["1e. Persons Covered (End of Policy Year)"], "339")
        self.assertEqual(by_name["10a. Total premiums or subscription charges paid to carrier"], "11,965")
        self.assertEqual(by_name["3b. Amount of Commissions"], "4,920")
        self.assertEqual(len(summaries), 1)
        self.assertEqual(len(summaries[0].benefit_rows), 4)
        self.assertEqual(len(rows), 3)

    def test_schedule_a_parser_extracts_standard_long_form_separate_benefits(self):
        pages = self._standard_long_form_pages()

        records = extract_standard_schedule_a_records(pages)
        summaries = extract_standard_schedule_a_summaries(pages)
        rows = extract_standard_broker_rows(pages)

        by_coverage = {record["coverage"]: record for record in records}
        self.assertEqual(set(by_coverage), {"DENTAL", "LIFE INSURANCE", "LONG TERM DISABILITY"})

        dental = by_coverage["DENTAL"]
        self.assertEqual(dental["carrier_name"], "Standard Insurance Company")
        self.assertEqual(dental["contract_number"], "168262")
        self.assertEqual(dental["persons_covered"], "63")
        self.assertEqual(dental["ein"], "93-0242990")
        self.assertEqual(dental["naic_code"], "69019")
        self.assertEqual(dental["commission_total"], "1,704.75")
        self.assertEqual(dental["fee_total"], "0.00")
        self.assertEqual(dental["experience_values"]["9a. Premiums: (1) Amount Received"], "30,312.84")
        self.assertEqual(dental["experience_values"]["9b(1). Benefit Charges (1) Claims paid"], "22,882.90")
        self.assertEqual(dental["experience_values"]["9c(1)(H). Total retention"], "9,627.22")

        life = by_coverage["LIFE INSURANCE"]
        self.assertEqual(life["persons_covered"], "107")
        self.assertEqual(life["commission_total"], "1,967.56")
        self.assertEqual(life["experience_values"]["9a(3). Increase (decrease) in unearned premium reserve"], "-2,104.00")

        ltd = by_coverage["LONG TERM DISABILITY"]
        self.assertEqual(ltd["commission_total"], "1,456.88")
        self.assertEqual(ltd["experience_values"]["9b(2). Increase (decrease) in claim reserves"], "1,610.73")

        self.assertEqual(len(summaries), 3)
        self.assertEqual([summary.coverage for summary in summaries], ["DENTAL", "LIFE INSURANCE", "LONG TERM DISABILITY"])
        self.assertEqual(len(rows), 3)
        self.assertEqual([row.commission_total for row in rows], ["1,704.75", "1,967.56", "1,456.88"])
        self.assertEqual(rows[0].name, "LEAHY CONSULTING SERVICES")
        self.assertEqual(rows[0].organization_code, "3")

    def test_schedule_a_parser_maps_standard_fields_and_overrides_selected_ftw_schedule(self):
        pages = self._standard_long_form_pages()
        fields = extract_standard_schedule_a_fields(pages)
        mapped = map_extraction_to_rules(
            "test-filing",
            fields,
            form_type=FormType.SCHEDULE_A,
            source_document_type=DocumentType.SCHEDULE_A,
        )["fields"]
        mapped_by_label = {field.mapped_label: field.proposed_value for field in mapped}

        self.assertEqual(mapped_by_label["1d. Contract/Policy Number"], "168262")
        self.assertEqual(mapped_by_label["1e. Persons Covered (End of Policy Year)"], "63")
        self.assertEqual(mapped_by_label["3b. Amount of Commissions"], "1,704.75")
        self.assertEqual(mapped_by_label["9a. Premiums: (1) Amount Received"], "30,312.84")

        summaries = extract_standard_schedule_a_summaries(pages)
        service = FTWilliamsReviewService()
        life_fields = service._fields_with_schedule_a_summary_override(mapped, summaries, "Schedule A-LIFE")
        life_by_label = {field.mapped_label: field.proposed_value for field in life_fields}
        self.assertEqual(life_by_label["1e. Persons Covered (End of Policy Year)"], "107")
        self.assertEqual(life_by_label["3b. Amount of Commissions"], "1,967.56")
        self.assertEqual(life_by_label["9a. Premiums: (1) Amount Received"], "23,945.79")
        self.assertEqual(life_by_label["9a(3). Increase (decrease) in unearned premium reserve"], "-2,104.00")

        ltd_fields = service._fields_with_schedule_a_summary_override(mapped, summaries, "Schedule A-LTD")
        ltd_by_label = {field.mapped_label: field.proposed_value for field in ltd_fields}
        self.assertEqual(ltd_by_label["1e. Persons Covered (End of Policy Year)"], "107")
        self.assertEqual(ltd_by_label["3b. Amount of Commissions"], "1,456.88")
        self.assertEqual(ltd_by_label["9a. Premiums: (1) Amount Received"], "12,383.55")
        self.assertEqual(ltd_by_label["9b(2). Increase (decrease) in claim reserves"], "1,610.73")

    def _standard_long_form_pages(self):
        def pdf_text(value):
            return "\n".join(line.strip() for line in value.splitlines())

        def part_i(page, coverage, persons, commission, base, contingent):
            return (
                page,
                pdf_text(f"""
                PAGE: {page}
                (C) PLAN SPONSOR:
                PART I
                1) COVERAGE -
                (a) CARRIER:
                (b) EIN:
                (c) NAIC CODE:
                (d) CONTRACT NUMBER:
                (e) NUMBER OF PERSONS COVERED:
                (f) FROM:
                (g) TO:
                2) INSURANCE FEES AND COMMISSIONS PAID TO AGENTS, BROKERS AND OTHER PERSONS:
                AMOUNT OF COMMISSIONS PAID:
                FEES PAID / AMOUNT:
                TO
                (B) AMOUNT OF COMMISSION PAID FEES PAIDA) NAME & ADDRESS OF AGENT OR
                BROKER TO WHOM COMMISSION OR
                FEES WERE PAID
                COMM. CONT. COMP* GA OVR. (C) AMOUNT (D) PURPOSE
                (E) ORG.
                CODE
                LEAHY CONSULTING SERVICES
                14031 STEEPLESTONE DR
                STE A
                MIDLOTHIAN, VA 23113
                ${base} ${contingent} $0.00 $0.00 3
                TOTAL COMMISSIONS PAID ${base}
                TOTAL CONTINGENT COMP PAID ${contingent}
                TOTAL GA OVERRIDES PAID $0.00
                Standard Insurance Company
                PELLA WINDOW AND DOORS
                12/1/2024
                11/30/2025
                {persons}
                93-0242990
                000-69019
                ${commission}
                $0.00
                THE FINANCIAL DATA BELOW IS PROVIDED FOR YOUR INFORMATION
                IT CAN BE USED TO COMPLETE THE SCHEDULE A FOR THE FORM 5500
                IF YOUR PLAN IS REQUIRED TO FILE SUCH A SCHEDULE
                {coverage}
                PLAN INFORMATION REPORT FOR THE PERIOD OF
                168262
                LONG FORM INFORMATION
                12/1/2024
                11/30/2025
                """),
            )

        def part_iii(page, coverage, premium, values):
            return (
                page,
                pdf_text(f"""
                TO
                PART III -
                7) BENEFIT TYPE:
                EXPERIENCE RATED CONTRACTS
                (a) PREMIUMS: (1) AMOUNT RECEIVED:
                (2) INCREASE (DECREASE) IN DUE BUT UNPAID:
                (3) INCREASE (DECREASE) IN UNEARNED PREMIUM RESERVE:
                (4) EARNED PREMIUM ((1)+(2) - (3)):
                (b) BENEFIT CHARGES: (1) CLAIMS PAID:
                (2) INCREASE (DECREASE) CLAIM RESERVES:
                (3) INCURRED CLAIMS ((1)+(2)):
                (4) CLAIMS CHARGED:
                (c) REMAINDER OF PREMIUM: (1) RETENTION CHARGES:
                PLAN INFORMATION REPORT FOR THE PERIOD OF
                Standard Insurance Company HEREBY CERTIFIES THAT THIS INFORMATION IS COMPLETE AND ACCURATE
                {chr(10).join(values)}
                168262
                {coverage}
                ${premium}
                LONG FORM INFORMATION
                12/1/2024
                11/30/2025
                """),
            )

        return [
            part_i(1, "DENTAL", "63", "1,704.75", "1,506.01", "198.74"),
            part_iii(
                2,
                "DENTAL",
                "30,312.84",
                [
                    "$0.00",
                    "$0.00",
                    "$30,312.84",
                    "$22,882.90",
                    "($78.00)",
                    "$22,804.90",
                    "$22,804.90",
                    "$1,704.75",
                    "$0.00",
                    "$0.00",
                    "$6,601.15",
                    "$682.07",
                    "$639.26",
                    "$0.00",
                    "$9,627.22",
                    "$0.00",
                    "$0.00",
                    "$0.00",
                    "$0.00",
                ],
            ),
            part_i(3, "LIFE INSURANCE", "107", "1,967.56", "1,731.10", "236.46"),
            part_iii(
                4,
                "LIFE INSURANCE",
                "23,945.79",
                [
                    "$112.00",
                    "($2,104.00)",
                    "$26,161.79",
                    "$0.00",
                    "$280.00",
                    "$280.00",
                    "$280.00",
                    "$1,967.56",
                    "$0.00",
                    "$0.00",
                    "$3,388.94",
                    "$588.78",
                    "$2,024.00",
                    "$17,918.72",
                    "$25,888.00",
                    "$0.00",
                    "$0.00",
                    "$0.00",
                    "$0.00",
                ],
            ),
            part_i(5, "LONG TERM DISABILITY", "107", "1,456.88", "1,308.98", "147.90"),
            part_iii(
                6,
                "LONG TERM DISABILITY",
                "12,383.55",
                [
                    "$62.00",
                    "($1,101.00)",
                    "$13,546.55",
                    "$1,200.00",
                    "$1,610.73",
                    "$2,810.73",
                    "$2,810.73",
                    "$1,456.88",
                    "$0.00",
                    "$0.00",
                    "$2,413.15",
                    "$304.83",
                    "$1,495.00",
                    "$5,067.41",
                    "$10,737.27",
                    "$0.00",
                    "$0.00",
                    "$0.00",
                    "$0.00",
                ],
            ),
        ]

    def test_schedule_a_parser_extracts_united_omaha_support_worksheet_pages(self):
        pages = self._united_omaha_pages()

        records = extract_united_omaha_schedule_a_records(pages)
        summaries = extract_united_omaha_schedule_a_summaries(pages)
        rows = extract_united_omaha_broker_rows(pages)

        self.assertEqual(parse_schedule_a_text(pages[0][1]), [])
        by_legacy = {record["legacy_group_id"]: record for record in records}
        self.assertEqual(set(by_legacy), {"GLTD0B432", "GLUG0B432", "GUDH0B432", "GUG0B432"})

        ltd = by_legacy["GLTD0B432"]
        self.assertEqual(ltd["carrier_name"], "United of Omaha Life Insurance Company")
        self.assertEqual(ltd["ein"], "47-0322111")
        self.assertEqual(ltd["naic_code"], "69868")
        self.assertEqual(ltd["group_id"], "G000B432")
        self.assertEqual(ltd["coverage"], "Long Term Disability Insured")
        self.assertEqual(ltd["persons_covered"], "126")
        self.assertEqual(ltd["premium"], "27,628")
        self.assertEqual(ltd["period_begin"], "12/01/2024")
        self.assertEqual(ltd["period_end"], "12/01/2025")

        self.assertEqual(len(summaries), 4)
        self.assertEqual(summaries[0].account_number, "GLTD0B432")
        self.assertEqual(summaries[0].benefit_rows[0].premium, "27,628")
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0].name, "GALLAGHER BENEFIT SERVICES INC")
        self.assertEqual(rows[0].commission_total, "4,144")
        self.assertEqual(rows[1].name, "GALLAGHER BENEFIT SERVICES INC NATIONAL INCENTIVE")
        self.assertEqual(rows[1].fee_total, "1,424")

    def test_schedule_a_parser_maps_united_omaha_and_overrides_selected_ftw_schedule(self):
        pages = self._united_omaha_pages()
        fields = extract_united_omaha_schedule_a_fields(pages)
        mapped = map_extraction_to_rules(
            "test-filing",
            fields,
            form_type=FormType.SCHEDULE_A,
            source_document_type=DocumentType.SCHEDULE_A,
        )["fields"]
        mapped_by_label = {field.mapped_label: field.proposed_value for field in mapped}

        self.assertEqual(mapped_by_label["1a. Name of Insurance Company"], "United of Omaha Life Insurance Company")
        self.assertEqual(mapped_by_label["1b. Insurance Carrier EIN"], "47-0322111")
        self.assertEqual(mapped_by_label["1c. NAIC Code"], "69868")
        self.assertEqual(mapped_by_label["1d. Contract/Policy Number"], "GLTD0B432")
        self.assertEqual(mapped_by_label["1e. Persons Covered (End of Policy Year)"], "126")
        self.assertEqual(mapped_by_label["3b. Amount of Commissions"], "4,144")
        self.assertEqual(mapped_by_label["3c. Amount of Fees"], "1,424")
        self.assertEqual(mapped_by_label["10a. Total premiums or subscription charges paid to carrier"], "27,628")

        summaries = extract_united_omaha_schedule_a_summaries(pages)
        service = FTWilliamsReviewService()
        life_fields = service._fields_with_schedule_a_summary_override(mapped, summaries, "Schedule A-LIFE")
        life_by_label = {field.mapped_label: field.proposed_value for field in life_fields}
        self.assertEqual(life_by_label["1d. Contract/Policy Number"], "GLUG0B432")
        self.assertEqual(life_by_label["1e. Persons Covered (End of Policy Year)"], "125")
        self.assertEqual(life_by_label["3b. Amount of Commissions"], "997")
        self.assertEqual(life_by_label["3c. Amount of Fees"], "338")
        self.assertEqual(life_by_label["10a. Total premiums or subscription charges paid to carrier"], "6,649")

        std_fields = service._fields_with_schedule_a_summary_override(mapped, summaries, "Schedule A-STD")
        std_by_label = {field.mapped_label: field.proposed_value for field in std_fields}
        self.assertEqual(std_by_label["1d. Contract/Policy Number"], "GUG0B432")
        self.assertEqual(std_by_label["3b. Amount of Commissions"], "7,008")
        self.assertEqual(std_by_label["3c. Amount of Fees"], "2,375")
        self.assertEqual(std_by_label["10a. Total premiums or subscription charges paid to carrier"], "46,722")

    def _united_omaha_pages(self):
        def pdf_text(value):
            return "\n".join(line.strip() for line in value.splitlines())

        def page(page_number, legacy_group_id, coverage, persons, commission, fee, premium):
            return (
                page_number,
                pdf_text(f"""
                SUPPORT FOR FORM 5500, SCHEDULE A, INSURANCE INFORMATION
                INFORMATION FOR COMPLETION OF PART I
                CAMINO HEALTH CENTER
                SAN JUAN CAPISTRANO, CA
                Name of Carrier: United of Omaha Life Insurance Company - NAIC Code 69868
                EIN Number: 47-0322111
                Group Identification
                Number:
                G000B432 Data for Period: 12-01-2024 to 12-01-2025
                Legacy Group ID: {legacy_group_id}
                Type of Contract: NON-RETENTION
                Benefits Provided Persons Covered
                {coverage} {persons}
                Name of Each Recipient
                Amount of
                Commission
                Paid
                Amount of Service
                Fees Paid or Other
                Fees
                Purpose for
                Which Paid
                Organization
                Type
                GALLAGHER BENEFIT SERVICES INC {commission} Agent or Broker of Record 3
                505 N BRAND BLVD FL 6
                GLENDALE, CA 91203
                GALLAGHER BENEFIT SERVICES INC 0 Other Compensation 3
                NATIONAL INCENTIVE {fee}
                736 S STONE AVE
                LA GRANGE, IL 60525
                INFORMATION FOR COMPLETION OF PART III
                10. Non-experience Rated Contracts:
                Premiums . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . {premium}
                Memo Items: Benefit Charges - Claims Paid . . . . . . . . . . . . . . . . 0
                Administrative Service Fees . . . . . . . . . . . . . . . . . . 0
                Group Office: SOUTHERN CALIFORNIA
                """),
            )

        return [
            page(3, "GLTD0B432", "Long Term Disability Insured", "126", "4,144", "1,424", "27,628"),
            page(4, "GLUG0B432", "Life & AD&D", "125", "997", "338", "6,649"),
            page(5, "GUDH0B432", "Accident only Voluntary", "25", "702", "215", "4,683"),
            page(6, "GUG 0B432", "Short Term Disability Insured", "126", "7,008", "2,375", "46,722"),
        ]

    def test_schedule_a_parser_groups_prudential_same_contract_benefit_pages(self):
        pages = [
            (
                2,
                """
                Insurance Information For SCHEDULE A (Form 5500) Insured Welfare Plan Data
                R.H. White Companies Inc.
                (Item numbers shown correspond to those on Schedule (A) 1 (a) Prudential Insurance Company of America
                1 (b) Prudential's EIN: 22-1211670 1 (c) NAIC code: 68241 1 (d) Contract number or identification: 71492
                1(e) Approximate number 2 Insurance fees and
                of persons covered at end Policy Contract Year commissions paid to
                7 Type of benefit of policy or contract year 1 (f) From 1 (g) To agents or brokers
                1/1/2025 12/31/2025 See Form 27722
                9 Non experience rated contracts:
                a. Total premiums or subscription charges paid to carrier $ 4,759
                GRP 32064 - Rev 1999
                Basic AD&D Insurance 507
                """,
            ),
            (
                3,
                """
                Insurance Information For SCHEDULE A (Form 5500) Insured Welfare Plan Data
                R.H. White Companies Inc.
                (Item numbers shown correspond to those on Schedule (A) 1 (a) Prudential Insurance Company of America
                1 (b) Prudential's EIN: 22-1211670 1 (c) NAIC code: 68241 1 (d) Contract number or identification: 71492
                1/1/2025 12/31/2025 See Form 27722
                9 Non experience rated contracts:
                a. Total premiums or subscription charges paid to carrier $ 37,278
                GRP 32064 - Rev 1999
                Basic Life Insurance 507
                """,
            ),
            (
                4,
                """
                Insurance Information For SCHEDULE A (Form 5500) Insured Welfare Plan Data
                R.H. White Companies Inc.
                (Item numbers shown correspond to those on Schedule (A) 1 (a) Prudential Insurance Company of America
                1 (b) Prudential's EIN: 22-1211670 1 (c) NAIC code: 68241 1 (d) Contract number or identification: 71492
                1/1/2025 12/31/2025 See Form 27722
                9 Non experience rated contracts:
                a. Total premiums or subscription charges paid to carrier $ 69,104
                GRP 32064 - Rev 1999
                Long-Term Disability 198
                """,
            ),
        ]

        fields = extract_prudential_schedule_a_fields(pages)
        by_name = {field.field_name: field.value for field in fields}
        summaries = extract_prudential_schedule_a_summaries(pages)

        self.assertEqual(by_name["1a. Name of Insurance Company"], "Prudential Insurance Company of America")
        self.assertEqual(by_name["1b. Insurance Carrier EIN"], "22-1211670")
        self.assertEqual(by_name["1c. NAIC Code"], "68241")
        self.assertEqual(by_name["1d. Contract/Policy Number"], "71492")
        self.assertEqual(by_name["1e. Persons Covered (End of Policy Year)"], "507")
        self.assertEqual(by_name["10a. Total premiums or subscription charges paid to carrier"], "111,141")
        self.assertEqual(len(summaries), 1)
        self.assertEqual(len(summaries[0].benefit_rows), 3)
        self.assertEqual(summaries[0].benefit_rows[2].benefit_type, "Long-Term Disability")

    def test_schedule_a_parser_extracts_prudential_commission_information_rows(self):
        pages = [
            (
                14,
                """
                ANNUAL REPORT SCHEDULE A(Form 5500) -
                Insurance Information
                (Insured Welfare Plan Commission Information)
                2 Insurance fees and commissions paid to general agents, brokers or other persons:
                71492 RSC INSURANCE BROKERAGE
                INC $45,757
                4TH FLOOR
                160 FEDERAL ST
                BOSTON, MA 2110
                71492 RSC INSURANCE BROKERAGE
                INC
                $12,839
                4TH FLOOR
                160 FEDERAL ST
                BOSTON, MA 2110
                71492 IMG $108
                2960 North Meridian Street
                Indianapolis, IN 46208
                71492 Selman & Company, LLC $13,099
                One Integrity Parkway
                Cleveland, OH 44143
                Includes amounts paid to general agents
                GRP 27722 - Rev 1999 The Prudential Insurance Company of America
                Third Party Administration Fees
                Sales and Service Compensation
                Supplemental Commissions
                Sales and Service Compensation
                """,
            ),
            (15, "12/31/2025\nOrganization \ncode\n3\n3\n5"),
        ]

        rows = extract_prudential_broker_rows(pages)
        by_name = {row.name: row for row in rows}

        self.assertEqual(len(rows), 3)
        self.assertEqual(by_name["RSC INSURANCE BROKERAGE INC"].commission_total, "58,596")
        self.assertEqual(by_name["RSC INSURANCE BROKERAGE INC"].fee_total, "0")
        self.assertEqual(by_name["RSC INSURANCE BROKERAGE INC"].zip_code, "02110")
        self.assertEqual(by_name["IMG"].fee_total, "108")
        self.assertEqual(by_name["IMG"].organization_code, "5")
        self.assertEqual(by_name["Selman & Company, LLC"].fee_total, "13,099")

    def test_schedule_a_parser_extracts_summary_table_broker_rows(self):
        pages = [
            (
                1,
                """
                Commissions
                Total commissions
                The following figure represents commissions that are to be reported on Schedule A, Line 3, Element (b):
                Contract ID Contract name Commissions paid
                000FG530 PREFERRED BENEFITS GROUP $16,464.93
                Total commissions for plan $16,464.93

                Group insurance coverages Commissions paid
                AD&D 736.11
                Dental (Insured) 3125.84
                Life 2785.21

                Fees
                Total fees
                The following figure represents fees that are to be reported on Schedule A, Line 3, Element (c):
                Contract ID Contract name Amount
                000F5894 NFP CORPORATE SERVICES NY LLC $2,645.25
                Total Fees Paid $2,645.25

                Group insurance coverages Gross premium paid
                Vision (Insured) $8,796.05
                Total premium paid $186,242.73
                """,
            )
        ]

        rows = extract_summary_table_broker_rows(pages)
        by_name = {row.name: row for row in rows}

        self.assertEqual(set(by_name), {"PREFERRED BENEFITS GROUP", "NFP CORPORATE SERVICES NY LLC"})
        self.assertEqual(by_name["PREFERRED BENEFITS GROUP"].commission_total, "16,464.93")
        self.assertEqual(by_name["PREFERRED BENEFITS GROUP"].organization_code, "3")
        self.assertEqual(by_name["NFP CORPORATE SERVICES NY LLC"].fee_total, "2,645.25")
        self.assertEqual(by_name["NFP CORPORATE SERVICES NY LLC"].organization_code, "3")

    def test_schedule_a_parser_merges_summary_table_broker_sections(self):
        pages = [
            (
                1,
                """
                The following figure represents commissions that are to be reported on Schedule A, Line 3, Element (b):
                Contract ID Contract name Commissions paid
                000FG530 PREFERRED BENEFITS GROUP $10.00

                The following figure represents fees that are to be reported on Schedule A, Line 3, Element (c):
                Contract ID Contract name Amount
                000FG530 PREFERRED BENEFITS GROUP $2.50
                """,
            )
        ]

        rows = extract_summary_table_broker_rows(pages)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "PREFERRED BENEFITS GROUP")
        self.assertEqual(rows[0].commission_total, "10")
        self.assertEqual(rows[0].fee_total, "2.50")


if __name__ == "__main__":
    unittest.main()
