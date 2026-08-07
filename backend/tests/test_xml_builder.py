from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import ExtractedField, FieldPriority, FormType
from app.services.xml_builder import (
    build_ftw_update_xml,
    build_single_document_update_xml,
    build_schedule_a_records_update_xml,
)


class XmlBuilderTests(unittest.TestCase):
    def test_skips_unchanged_5500_fields_when_current_ftw_values_match(self):
        fields = [
            ExtractedField(
                filing_id="filing",
                source_field_name="10a. Plan benefit arrangement",
                normalized_field_name="benefit_arrangement",
                mapped_rule_key="form_5500_part_ii_10a_plan_benefit_arrangement",
                mapped_label="10a. Plan benefit arrangement",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="Insurance",
                proposed_value="Insurance",
            ),
            ExtractedField(
                filing_id="filing",
                source_field_name="1a. Plan Name",
                normalized_field_name="plan_name",
                mapped_rule_key="form_5500_part_i_1a_plan_name",
                mapped_label="1a. Plan Name",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="MIDWEST HOSE AND SPECIALTY HEALTH AND WELFARE BENEFITS PLAN",
                proposed_value="MIDWEST HOSE AND SPECIALTY HEALTH AND WELFARE BENEFITS PLAN",
            ),
        ]

        xml = build_single_document_update_xml(
            "DOL5500Data",
            fields,
            FormType.FORM_5500,
            transaction_type="1",
            customer_id="73-1185740",
            plan_id="73-1185740501",
            year="2025",
            current_values={
                "BenefitInsuranceInd": "1",
                "PlanName": "MIDWEST HOSE AND SPECIALTY HEALTH AND WELFARE BENEFITS PLAN",
            },
        )

        self.assertNotIn("<DOL5500Data>", xml)
        self.assertNotIn("BENEFIT_CODE1", xml)
        self.assertNotIn("PLAN_NAME0", xml)

    def test_5500_checkbox_groups_replace_current_checked_values(self):
        fields = [
            ExtractedField(
                filing_id="filing",
                source_field_name="9. Plan funding arrangement",
                normalized_field_name="funding_arrangement",
                mapped_rule_key="form_5500_part_ii_9_plan_funding_arrangement",
                mapped_label="9. Plan funding arrangement",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="Insurance",
                proposed_value="Insurance",
            ),
            ExtractedField(
                filing_id="filing",
                source_field_name="10a. Plan benefit arrangement",
                normalized_field_name="benefit_arrangement",
                mapped_rule_key="form_5500_part_ii_10a_plan_benefit_arrangement",
                mapped_label="10a. Plan benefit arrangement",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="Insurance",
                proposed_value="Insurance",
            ),
        ]

        xml = build_single_document_update_xml(
            "DOL5500Data",
            fields,
            FormType.FORM_5500,
            transaction_type="1",
            customer_id="73-1185740",
            plan_id="73-1185740501",
            year="2025",
            current_values={
                "FundingInsuranceInd": "0",
                "FundingGeneralAssetInd": "1",
                "BenefitInsuranceInd": "0",
                "BenefitGeneralAssetInd": "1",
            },
        )

        self.assertIn("<DOL5500Data>", xml)
        self.assertIn("<FundingInsuranceInd>1</FundingInsuranceInd>", xml)
        self.assertIn("<FundingGeneralAssetInd>0</FundingGeneralAssetInd>", xml)
        self.assertIn("<FundingTrustInd>0</FundingTrustInd>", xml)
        self.assertIn("<FundingCdSection412Ind>0</FundingCdSection412Ind>", xml)
        self.assertIn("<BenefitInsuranceInd>1</BenefitInsuranceInd>", xml)
        self.assertIn("<BenefitGeneralAssetInd>0</BenefitGeneralAssetInd>", xml)
        self.assertIn("<BenefitTrustInd>0</BenefitTrustInd>", xml)
        self.assertIn("<BenefitCdSection412Ind>0</BenefitCdSection412Ind>", xml)

    def test_5500_welfare_plan_field_is_not_sent_with_invalid_ftw_tag(self):
        fields = [
            ExtractedField(
                filing_id="filing",
                source_field_name="6. Plan is a welfare plan?",
                normalized_field_name="is_welfare_plan",
                mapped_rule_key="form_5500_part_ii_6_plan_is_a_welfare_plan",
                mapped_label="6. Plan is a welfare plan?",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="Yes",
                proposed_value="Yes",
            ),
        ]

        xml = build_ftw_update_xml(
            fields,
            customer_id="04-2103905",
            plan_id="04-2103905502",
            year="2025",
            include_schedule_a=False,
        )

        self.assertNotIn("WELFARE_BENEFIT_PLAN_IND", xml)
        self.assertIn("No approved FT Williams fields are available yet", xml)

    def test_5500_query_only_and_unsupported_static_tags_are_not_sent(self):
        fields = [
            ExtractedField(
                filing_id="filing",
                source_field_name="1a. Plan Name",
                normalized_field_name="plan_name",
                mapped_rule_key="form_5500_part_i_1a_plan_name",
                mapped_label="1a. Plan Name",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="New Plan Name",
                proposed_value="New Plan Name",
            ),
            ExtractedField(
                filing_id="filing",
                source_field_name="1f. Plan Sponsor Address",
                normalized_field_name="sponsor_address",
                mapped_rule_key="form_5500_part_i_1f_plan_sponsor_address",
                mapped_label="1f. Plan Sponsor Address",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="490B Boston Post Road",
                proposed_value="490B Boston Post Road",
            ),
        ]

        xml = build_single_document_update_xml(
            "DOL5500Data",
            fields,
            FormType.FORM_5500,
            transaction_type="1",
            customer_id="04-2909410",
            plan_id="04-2909410501",
            year="2024",
            current_values={"PlanName": "Old Plan Name", "SDAddressLine1": "Old Address"},
        )

        self.assertNotIn("PLAN_NAME0", xml)
        self.assertNotIn("SPONS_DFE_MAIL_STR_ADDRESS", xml)
        self.assertIn("No approved FT Williams fields are available yet", xml)

    def test_schedule_a_full_replace_preserves_current_values_and_overlays_changes(self):
        fields = [
            ExtractedField(
                filing_id="filing",
                source_field_name="3b. Amount of Commissions",
                normalized_field_name="commissions",
                mapped_rule_key="schedule_a_part_i_3b_amount_of_commissions",
                mapped_label="3b. Amount of Commissions",
                form_type=FormType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="222000",
                proposed_value="222000",
            ),
        ]

        xml = build_single_document_update_xml(
            "DOLScheduleAData",
            fields,
            FormType.SCHEDULE_A,
            transaction_type="2",
            customer_id="73-1185740",
            plan_id="73-1185740501",
            year="2024",
            current_values={
                "InsCarrierName": "Existing Carrier",
                "InsCarrierEIN": "36-2739571",
                "InsContractNum": "1246876",
                "CommPdAmt01": "111893",
                "PlanYearBeginDate": "10-01-2024",
                "PlanYearEndDate": "09-30-2025",
            },
            ftw_seq_no="4",
            preserve_current_values=True,
        )

        self.assertIn("<DOLScheduleAData>", xml)
        self.assertIn("<TransactionType>2</TransactionType>", xml)
        self.assertNotIn("<FTWSeqNo>", xml)
        self.assertIn("<InsCarrierName>Existing Carrier</InsCarrierName>", xml)
        self.assertIn("<InsCarrierEIN>36-2739571</InsCarrierEIN>", xml)
        self.assertIn("<InsContractNum>1246876</InsContractNum>", xml)
        self.assertIn("<CommPdAmt1>222000</CommPdAmt1>", xml)
        self.assertNotIn("<CommPdAmt01>", xml)
        self.assertIn("<PlanYearBeginDate>10/01/2024</PlanYearBeginDate>", xml)
        self.assertIn("<PlanYearEndDate>09/30/2025</PlanYearEndDate>", xml)

    def test_schedule_a_full_replace_preserves_indicator_fields(self):
        xml = build_schedule_a_records_update_xml(
            [
                {
                    "ftw_seq_no": "1",
                    "query_results": {
                        "ScheduleDesc": "EQUITABL",
                        "InsCarrierName": "Existing Carrier",
                        "InsCarrierEIN": "86-0222062",
                        "InsContractNum": "021960",
                        "LifeInsurInd": "1",
                        "LongTermDisabInd": "1",
                        "OtherInd": "0",
                        "InsFailProvideInfoInd": "2",
                    },
                }
            ],
            "1",
            [],
            customer_id="04-2103905",
            plan_id="04-2103905502",
            year="2025",
        )

        self.assertIn("<LifeInsurInd>1</LifeInsurInd>", xml)
        self.assertIn("<LongTermDisabInd>1</LongTermDisabInd>", xml)
        self.assertIn("<OtherInd>0</OtherInd>", xml)
        self.assertIn("<InsFailProvideInfoInd>2</InsFailProvideInfoInd>", xml)
        self.assertIn("<ScheduleDesc>EQUITABL</ScheduleDesc>", xml)

    def test_schedule_a_batch_normalizes_fail_to_provide_yes_no_to_ftw_codes(self):
        xml = build_schedule_a_records_update_xml(
            [
                {
                    "ftw_seq_no": "1",
                    "query_results": {
                        "ScheduleDesc": "PRUDNTL",
                        "InsCarrierName": "Prudential Insurance Company of America",
                        "InsFailProvideInfoInd": "No",
                    },
                },
                {
                    "ftw_seq_no": "2",
                    "query_results": {
                        "ScheduleDesc": "TESTYES",
                        "InsCarrierName": "Test Carrier",
                        "InsFailProvideInfoInd": "Yes",
                    },
                },
            ],
            "1",
            [],
            ftw_customer_id="1683573117",
            ftw_plan_id="2031322679",
            year="2025",
        )

        self.assertEqual(xml.count("<DOLScheduleAData>"), 2)
        self.assertIn("<InsFailProvideInfoInd>2</InsFailProvideInfoInd>", xml)
        self.assertIn("<InsFailProvideInfoInd>1</InsFailProvideInfoInd>", xml)
        self.assertNotIn("<InsFailProvideInfoInd>No</InsFailProvideInfoInd>", xml)
        self.assertNotIn("<InsFailProvideInfoInd>Yes</InsFailProvideInfoInd>", xml)

    def test_schedule_a_full_replace_is_empty_when_there_are_no_changes(self):
        fields = [
            ExtractedField(
                filing_id="filing",
                source_field_name="1a. Name of Insurance Company",
                normalized_field_name="carrier",
                mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                mapped_label="1a. Name of Insurance Company",
                form_type=FormType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="Existing Carrier",
                proposed_value="Existing Carrier",
            ),
        ]

        xml = build_single_document_update_xml(
            "DOLScheduleAData",
            fields,
            FormType.SCHEDULE_A,
            transaction_type="2",
            customer_id="73-1185740",
            plan_id="73-1185740501",
            year="2024",
            current_values={"InsCarrierName": "Existing Carrier"},
            preserve_current_values=True,
        )

        self.assertNotIn("<DOLScheduleAData>", xml)

    def test_schedule_a_records_update_preserves_all_existing_records_and_updates_selected(self):
        fields = [
            ExtractedField(
                filing_id="filing",
                source_field_name="3a. Name of Agent/Broker/Person",
                normalized_field_name="broker_name",
                mapped_rule_key="schedule_a_part_i_3a_name_of_agent_broker_person",
                mapped_label="3a. Name of Agent/Broker/Person",
                form_type=FormType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="New Broker",
                proposed_value="New Broker",
            ),
        ]
        records = [
            {
                "ftw_seq_no": "1",
                "query_results": {
                    "InsCarrierName": "Kaiser",
                    "InsCarrierEIN": "94-1340523",
                    "InsContractNum": "236163",
                    "Name1": "Old Broker",
                },
            },
            {
                "ftw_seq_no": "2",
                "query_results": {
                    "InsCarrierName": "Principal",
                    "InsCarrierEIN": "42-0127290",
                    "InsContractNum": "1149477",
                    "Name1": "Principal Broker",
                },
            },
        ]

        xml = build_schedule_a_records_update_xml(
            records,
            "1",
            fields,
            customer_id="04-2909410",
            plan_id="04-2909410501",
            year="2025",
        )

        self.assertEqual(xml.count("<DOLScheduleAData>"), 2)
        self.assertNotIn("<FTWSeqNo>", xml)
        self.assertIn("<InsCarrierName>Kaiser</InsCarrierName>", xml)
        self.assertIn("<Name1>New Broker</Name1>", xml)
        self.assertIn("<InsCarrierName>Principal</InsCarrierName>", xml)
        self.assertIn("<Name1>Principal Broker</Name1>", xml)
        self.assertNotIn("<Name1>Old Broker</Name1>", xml)

    def test_schedule_a_records_update_preserves_all_existing_records_with_no_selected_changes(self):
        records = [
            {
                "ftw_seq_no": "1",
                "query_results": {
                    "ScheduleDesc": "KAISER",
                    "InsCarrierName": "Kaiser",
                    "InsCarrierEIN": "94-1340523",
                    "InsContractNum": "236163",
                },
            },
            {
                "ftw_seq_no": "2",
                "query_results": {
                    "ScheduleDesc": "PRINCPL",
                    "InsCarrierName": "Principal",
                    "InsCarrierEIN": "42-0127290",
                    "InsContractNum": "1149477",
                },
            },
        ]

        xml = build_schedule_a_records_update_xml(
            records,
            "1",
            [],
            customer_id="04-2909410",
            plan_id="04-2909410501",
            year="2025",
        )

        self.assertEqual(xml.count("<DOLScheduleAData>"), 2)
        self.assertIn("<ScheduleDesc>KAISER</ScheduleDesc>", xml)
        self.assertIn("<InsCarrierName>Kaiser</InsCarrierName>", xml)
        self.assertIn("<ScheduleDesc>PRINCPL</ScheduleDesc>", xml)
        self.assertIn("<InsCarrierName>Principal</InsCarrierName>", xml)

    def test_schedule_a_records_update_can_append_new_schedule_without_dropping_existing_records(self):
        fields = [
            ExtractedField(
                filing_id="filing",
                source_field_name="1a. Name of Insurance Company",
                normalized_field_name="carrier",
                mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                mapped_label="1a. Name of Insurance Company",
                form_type=FormType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="New Carrier",
                proposed_value="New Carrier",
            ),
            ExtractedField(
                filing_id="filing",
                source_field_name="1d. Contract/Policy Number",
                normalized_field_name="contract",
                mapped_rule_key="schedule_a_part_i_1d_contract_policy_number",
                mapped_label="1d. Contract/Policy Number",
                form_type=FormType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="NEW123",
                proposed_value="NEW123",
            ),
        ]
        records = [
            {
                "ftw_seq_no": "1",
                "query_results": {
                    "ScheduleDesc": "KAISER",
                    "InsCarrierName": "Kaiser",
                    "InsContractNum": "236163",
                },
            },
            {
                "ftw_seq_no": "2",
                "query_results": {
                    "ScheduleDesc": "VISION",
                    "InsCarrierName": "Vision Service Plan",
                    "InsContractNum": "30098552",
                },
            },
        ]

        xml = build_schedule_a_records_update_xml(
            records,
            None,
            [],
            add_new_fields=fields,
            new_schedule_desc="NEWCARR",
            customer_id="04-2909410",
            plan_id="04-2909410501",
            year="2025",
        )

        self.assertEqual(xml.count("<DOLScheduleAData>"), 3)
        self.assertIn("<ScheduleDesc>KAISER</ScheduleDesc>", xml)
        self.assertIn("<InsCarrierName>Kaiser</InsCarrierName>", xml)
        self.assertIn("<ScheduleDesc>VISION</ScheduleDesc>", xml)
        self.assertIn("<InsCarrierName>Vision Service Plan</InsCarrierName>", xml)
        self.assertIn("<ScheduleDesc>NEWCARR</ScheduleDesc>", xml)
        self.assertIn("<InsCarrierName>New Carrier</InsCarrierName>", xml)
        self.assertIn("<InsContractNum>NEW123</InsContractNum>", xml)

    def test_normalizes_ftw_update_dates_to_slash_format(self):
        fields = [
            ExtractedField(
                filing_id="filing",
                source_field_name="6. Plan Year Beginning Date",
                normalized_field_name="plan_year_begin",
                mapped_rule_key="form_5500_part_i_6_plan_year_beginning_date",
                mapped_label="6. Plan Year Beginning Date",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="10-01-2024",
                proposed_value="10-01-2024",
            ),
            ExtractedField(
                filing_id="filing",
                source_field_name="7. Plan Year Ending Date",
                normalized_field_name="plan_year_end",
                mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                mapped_label="7. Plan Year Ending Date",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="09-30-2025",
                proposed_value="09-30-2025",
            ),
            ExtractedField(
                filing_id="filing",
                source_field_name="4d. Plan Year Beginning Date",
                normalized_field_name="schedule_plan_year_begin",
                mapped_rule_key="schedule_a_part_iv_4d_plan_year_beginning_date",
                mapped_label="4d. Plan Year Beginning Date",
                form_type=FormType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="2024-10-01",
                proposed_value="2024-10-01",
            ),
            ExtractedField(
                filing_id="filing",
                source_field_name="4e. Plan Year Ending Date",
                normalized_field_name="schedule_plan_year_end",
                mapped_rule_key="schedule_a_part_iv_4e_plan_year_ending_date",
                mapped_label="4e. Plan Year Ending Date",
                form_type=FormType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="2025-09-30",
                proposed_value="2025-09-30",
            ),
        ]

        xml_5500 = build_single_document_update_xml(
            "DOL5500Data",
            fields,
            FormType.FORM_5500,
            transaction_type="1",
            customer_id="73-1185740",
            plan_id="73-1185740501",
            year="2024",
        )
        xml_schedule_a = build_single_document_update_xml(
            "DOLScheduleAData",
            fields,
            FormType.SCHEDULE_A,
            transaction_type="2",
            customer_id="73-1185740",
            plan_id="73-1185740501",
            year="2024",
        )

        self.assertIn("<FORM_PLAN_YEAR_BEGIN_DATE>10/01/2024</FORM_PLAN_YEAR_BEGIN_DATE>", xml_5500)
        self.assertIn("<FORM_TAX_PRD>09/30/2025</FORM_TAX_PRD>", xml_5500)
        self.assertIn("<PlanYearBeginDate>10/01/2024</PlanYearBeginDate>", xml_schedule_a)
        self.assertIn("<PlanYearEndDate>09/30/2025</PlanYearEndDate>", xml_schedule_a)


if __name__ == "__main__":
    unittest.main()
