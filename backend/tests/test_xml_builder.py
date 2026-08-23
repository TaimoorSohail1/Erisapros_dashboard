from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import ExtractedField, FieldPriority, FormType
from app.services.ftwilliams_contract import FTWPayloadValidationError
from app.services.xml_builder import (
    build_ftw_update_xml,
    build_single_document_update_xml,
    build_schedule_a_records_update_xml,
    schedule_a_replacement_data_gaps,
)


class XmlBuilderTests(unittest.TestCase):
    def test_discovered_comparison_field_never_enters_ftw_xml(self):
        field = ExtractedField(
            filing_id="filing",
            source_field_name="Carrier explanation for missing information",
            normalized_field_name="carrier_explanation_for_missing_information",
            mapped_rule_key="ftw_discovered_schedule_a_ins_fail_provide_info_text",
            mapped_label="Reason information was not provided",
            ftw_field="Insurance Carrier Missing Information Explanation",
            xml_tag="InsFailProvideInfoText",
            form_type=FormType.SCHEDULE_A,
            priority=FieldPriority.MEDIUM,
            value="Carrier records were incomplete",
            proposed_value="Carrier records were incomplete",
        )

        xml = build_single_document_update_xml(
            "DOLScheduleAData",
            [field],
            FormType.SCHEDULE_A,
            transaction_type="2",
            customer_id="12-3456789",
            plan_id="12-3456789501",
            year="2025",
        )

        self.assertNotIn("InsFailProvideInfoText", xml)
        self.assertNotIn("Carrier records were incomplete", xml)
        self.assertIn("No approved FT Williams fields are available yet", xml)

    def test_extraction_only_fields_never_enter_ftw_xml(self):
        field = ExtractedField(
            filing_id="filing",
            source_field_name="Policy Category",
            normalized_field_name="policy_category",
            mapped_rule_key="custom_policy_category",
            mapped_label="Policy Category",
            ftw_field=None,
            xml_tag=None,
            form_type=FormType.SCHEDULE_A,
            priority=FieldPriority.MEDIUM,
            value="Medical",
            proposed_value="Medical",
        )

        xml = build_single_document_update_xml(
            "DOLScheduleAData",
            [field],
            FormType.SCHEDULE_A,
            transaction_type="2",
            customer_id="12-3456789",
            plan_id="12-3456789501",
            year="2025",
        )

        self.assertNotIn("Policy Category", xml)
        self.assertNotIn("Medical", xml)
        self.assertIn("No approved FT Williams fields are available yet", xml)

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

    def test_5500_documented_identity_and_contact_fields_are_sent(self):
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
                source_field_name="1d. Plan Sponsor Name",
                normalized_field_name="sponsor_name",
                mapped_rule_key="form_5500_part_i_1d_plan_sponsor_name",
                mapped_label="1d. Plan Sponsor Name",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="New Sponsor Name",
                proposed_value="New Sponsor Name",
            ),
            ExtractedField(
                filing_id="filing",
                source_field_name="1e. Plan Sponsor EIN",
                normalized_field_name="sponsor_ein",
                mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                mapped_label="1e. Plan Sponsor EIN",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="12-3456789",
                proposed_value="12-3456789",
            ),
            ExtractedField(
                filing_id="filing",
                source_field_name="1f. Plan Sponsor Address",
                normalized_field_name="sponsor_address",
                mapped_rule_key="form_5500_part_i_1f_plan_sponsor_address",
                mapped_label="1f. Plan Sponsor Address",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="490B Boston Post Road, Sudbury MA 01776",
                proposed_value="490B Boston Post Road, Sudbury MA 01776",
            ),
            ExtractedField(
                filing_id="filing",
                source_field_name="2a. Plan Administrator Name",
                normalized_field_name="administrator_name",
                mapped_rule_key="form_5500_part_i_2a_plan_administrator_name",
                mapped_label="2a. Plan Administrator Name",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="New Administrator",
                proposed_value="New Administrator",
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
            current_values={
                "PlanName": "Old Plan Name",
                "SDName": "Old Sponsor Name",
                "SDEIN": "98-7654321",
                "SDAddressLine1": "Old Address",
                "SDCity": "Sudbury",
                "SDState": "MA",
                "SDZipCode": "01776",
                "ADMINName": "Old Administrator",
            },
        )

        self.assertIn("<PLAN_NAME0>New Plan Name</PLAN_NAME0>", xml)
        self.assertIn("<SPONSOR_DFE_NAME0>New Sponsor Name</SPONSOR_DFE_NAME0>", xml)
        self.assertIn("<SPONS_DFE_EIN>12-3456789</SPONS_DFE_EIN>", xml)
        self.assertIn("<SPONS_DFE_MAIL_STR_ADDRESS>490B Boston Post Road</SPONS_DFE_MAIL_STR_ADDRESS>", xml)
        self.assertIn("<ADMIN_NAME0>New Administrator</ADMIN_NAME0>", xml)

    def test_5500_sponsor_ein_uses_documented_update_tag(self):
        field = ExtractedField(
            filing_id="filing",
            source_field_name="1e. Plan Sponsor EIN",
            normalized_field_name="sponsor_ein",
            mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
            mapped_label="1e. Plan Sponsor EIN",
            form_type=FormType.FORM_5500,
            priority=FieldPriority.HIGH,
            value="12-3456789",
            proposed_value="12-3456789",
        )

        xml = build_single_document_update_xml(
            "DOL5500Data",
            [field],
            FormType.FORM_5500,
            transaction_type="1",
            customer_id="12-3456789",
            plan_id="12-3456789501",
            year="2025",
            current_values={"SDEIN": "98-7654321"},
        )

        self.assertIn("<SPONS_DFE_EIN>12-3456789</SPONS_DFE_EIN>", xml)

    def test_5500_combined_address_updates_street_without_corrupting_locality(self):
        field = ExtractedField(
            filing_id="filing",
            source_field_name="1f. Plan Sponsor Address",
            normalized_field_name="sponsor_address",
            mapped_rule_key="form_5500_part_i_1f_plan_sponsor_address",
            mapped_label="1f. Plan Sponsor Address",
            form_type=FormType.FORM_5500,
            priority=FieldPriority.HIGH,
            value="750 E MAIN ST SUITE 200 STAMFORD CT 069023831",
            proposed_value="750 E MAIN ST SUITE 200 STAMFORD CT 069023831",
        )

        xml = build_single_document_update_xml(
            "DOL5500Data",
            [field],
            FormType.FORM_5500,
            transaction_type="1",
            customer_id="12-3456789",
            plan_id="12-3456789501",
            year="2025",
            current_values={
                "SDAddressLine1": "OLD MAIN ST",
                "SDAddressLine2": "SUITE 200",
                "SDCity": "STAMFORD",
                "SDState": "CT",
                "SDZipCode": "06902-3831",
            },
        )

        self.assertIn("<SPONS_DFE_MAIL_STR_ADDRESS>750 E MAIN ST</SPONS_DFE_MAIL_STR_ADDRESS>", xml)
        self.assertNotIn("<SPONS_DFE_CITY>", xml)
        self.assertNotIn("<SPONS_DFE_STATE>", xml)
        self.assertNotIn("<SPONS_DFE_ZIP_CODE>", xml)

    def test_5500_combined_address_parses_common_unseparated_us_address(self):
        field = ExtractedField(
            filing_id="filing",
            source_field_name="1f. Plan Sponsor Address",
            normalized_field_name="sponsor_address",
            mapped_rule_key="form_5500_part_i_1f_plan_sponsor_address",
            mapped_label="1f. Plan Sponsor Address",
            form_type=FormType.FORM_5500,
            priority=FieldPriority.HIGH,
            value="18 CHESTNUT ST. SUITE 500 WORCESTER MA 01608",
            proposed_value="18 CHESTNUT ST. SUITE 500 WORCESTER MA 01608",
        )

        xml = build_single_document_update_xml(
            "DOL5500Data",
            [field],
            FormType.FORM_5500,
            transaction_type="1",
            customer_id="12-3456789",
            plan_id="12-3456789501",
            year="2025",
            current_values={},
        )

        self.assertIn("<SPONS_DFE_MAIL_STR_ADDRESS>18 CHESTNUT ST. SUITE 500</SPONS_DFE_MAIL_STR_ADDRESS>", xml)
        self.assertIn("<SPONS_DFE_CITY>WORCESTER</SPONS_DFE_CITY>", xml)
        self.assertIn("<SPONS_DFE_STATE>MA</SPONS_DFE_STATE>", xml)
        self.assertIn("<SPONS_DFE_ZIP_CODE>01608</SPONS_DFE_ZIP_CODE>", xml)

    def test_unknown_schedule_a_tag_is_blocked_by_default(self):
        field = ExtractedField(
            filing_id="filing",
            source_field_name="Unknown extracted field",
            normalized_field_name="unknown_field",
            mapped_rule_key="custom_unknown_rule",
            mapped_label="Unknown extracted field",
            xml_tag="UnknownFTWTag",
            form_type=FormType.SCHEDULE_A,
            priority=FieldPriority.HIGH,
            value="123",
            proposed_value="123",
        )

        xml = build_single_document_update_xml(
            "DOLScheduleAData",
            [field],
            FormType.SCHEDULE_A,
            transaction_type="2",
            customer_id="12-3456789",
            plan_id="12-3456789501",
            year="2025",
        )

        self.assertNotIn("UnknownFTWTag", xml)
        self.assertIn("No approved FT Williams fields are available yet", xml)

    def test_alphabetic_value_for_numeric_ftw_field_is_blocked_before_xml(self):
        field = ExtractedField(
            filing_id="filing",
            source_field_name="14. Active Participants at End",
            normalized_field_name="active_participants_end",
            mapped_rule_key="form_5500_part_ii_14_active_participants_at_end",
            mapped_label="14. Active Participants at End",
            form_type=FormType.FORM_5500,
            priority=FieldPriority.HIGH,
            value="one hundred",
            proposed_value="one hundred",
        )

        with self.assertRaisesRegex(FTWPayloadValidationError, "TotActivePartcpCnt"):
            build_single_document_update_xml(
                "DOL5500Data",
                [field],
                FormType.FORM_5500,
                transaction_type="1",
                customer_id="12-3456789",
                plan_id="12-3456789501",
                year="2025",
                current_values={"TotActivePartcpCnt": "99"},
            )

    def test_schedule_a_uses_ftw_canonical_carrier_name_when_identity_matches(self):
        def schedule_field(rule_key: str, label: str, value: str) -> ExtractedField:
            return ExtractedField(
                filing_id="filing",
                source_field_name=label,
                normalized_field_name=label.lower(),
                mapped_rule_key=rule_key,
                mapped_label=label,
                form_type=FormType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value=value,
                proposed_value=value,
            )

        fields = [
            schedule_field(
                "schedule_a_part_i_1a_name_of_insurance_company",
                "1a. Name of Insurance Company",
                'Cigna Health and Life Insurance Company and affiliates ("Cigna")',
            ),
            schedule_field(
                "schedule_a_part_i_1b_insurance_carrier_ein",
                "1b. Insurance Carrier EIN",
                "59-1031071",
            ),
            schedule_field(
                "schedule_a_part_i_1d_contract_policy_number",
                "1d. Contract/Policy Number",
                "1234567",
            ),
            schedule_field(
                "schedule_a_part_i_3b_amount_of_commissions",
                "3b. Amount of Commissions",
                "100",
            ),
        ]

        xml = build_single_document_update_xml(
            "DOLScheduleAData",
            fields,
            FormType.SCHEDULE_A,
            transaction_type="2",
            customer_id="12-3456789",
            plan_id="12-3456789501",
            year="2025",
            current_values={
                "InsCarrierName": "CIGNA HEALTH AND LIFE INSURANCE COMPANY",
                "InsCarrierEIN": "59-1031071",
                "InsContractNum": "1234567",
                "CommPdAmt01": "90",
            },
            preserve_current_values=True,
        )

        self.assertIn("<InsCarrierName>CIGNA HEALTH AND LIFE INSURANCE COMPANY</InsCarrierName>", xml)
        self.assertNotIn("affiliates", xml)

    def test_schedule_a_carrier_alias_text_is_blocked_when_no_ftw_canonical_match_exists(self):
        field = ExtractedField(
            filing_id="filing",
            source_field_name="1a. Name of Insurance Company",
            normalized_field_name="carrier",
            mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
            mapped_label="1a. Name of Insurance Company",
            form_type=FormType.SCHEDULE_A,
            priority=FieldPriority.HIGH,
            value='Cigna Health and Life Insurance Company and affiliates ("Cigna")',
            proposed_value='Cigna Health and Life Insurance Company and affiliates ("Cigna")',
        )

        with self.assertRaisesRegex(FTWPayloadValidationError, "exact legal carrier name"):
            build_single_document_update_xml(
                "DOLScheduleAData",
                [field],
                FormType.SCHEDULE_A,
                transaction_type="2",
                customer_id="12-3456789",
                plan_id="12-3456789501",
                year="2025",
            )

    def test_5500_documented_plan_administrator_tag_is_sent_with_valid_fields(self):
        fields = [
            ExtractedField(
                filing_id="filing",
                source_field_name="2a. Plan Administrator Name",
                normalized_field_name="plan_administrator_name",
                mapped_rule_key="form_5500_part_i_2a_plan_administrator_name",
                mapped_label="2a. Plan Administrator Name",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="Charlotte Tallon",
                proposed_value="Charlotte Tallon",
            ),
            ExtractedField(
                filing_id="filing",
                source_field_name="14. Active Participants at End",
                normalized_field_name="active_participants_end",
                mapped_rule_key="form_5500_part_ii_14_active_participants_at_end",
                mapped_label="14. Active Participants at End",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="125",
                proposed_value="125",
            ),
        ]

        xml = build_single_document_update_xml(
            "DOL5500Data",
            fields,
            FormType.FORM_5500,
            transaction_type="1",
            ftw_customer_id="748817358",
            ftw_plan_id="920031353",
            year="2025",
            current_values={"ADMINName": "AMERICAN SECURITIES LLC", "TotActivePartcpCnt": "120"},
        )

        self.assertIn("<ADMIN_NAME0>Charlotte Tallon</ADMIN_NAME0>", xml)
        self.assertIn("<TotActivePartcpCnt>125</TotActivePartcpCnt>", xml)

    def test_5500_participant_totals_use_the_same_ftw_tags_returned_by_current_query(self):
        fields = [
            ExtractedField(
                filing_id="filing",
                source_field_name="12. Total Participants at End of Year",
                normalized_field_name="total_participants_end",
                mapped_rule_key="form_5500_part_ii_12_total_participants_at_end_of_year",
                mapped_label="12. Total Participants at End of Year",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="156",
                proposed_value="156",
            ),
            ExtractedField(
                filing_id="filing",
                source_field_name="13. Active Participants at Beginning",
                normalized_field_name="active_participants_beginning",
                mapped_rule_key="form_5500_part_ii_13_active_participants_at_beginning",
                mapped_label="13. Active Participants at Beginning",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="160",
                proposed_value="160",
            ),
        ]

        xml = build_single_document_update_xml(
            "DOL5500Data",
            fields,
            FormType.FORM_5500,
            transaction_type="1",
            ftw_customer_id="748817358",
            ftw_plan_id="920031353",
            year="2025",
            current_values={"SubtlActRtdSepCnt": "148", "TotActPartcpBoyCnt": "157"},
        )

        self.assertIn("<SubtlActRtdSepCnt>156</SubtlActRtdSepCnt>", xml)
        self.assertIn("<TotActPartcpBoyCnt>160</TotActPartcpBoyCnt>", xml)
        self.assertNotIn("PartcpAccountBalCnt", xml)

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
        self.assertIn("<DOLSubPartData>", xml)
        self.assertIn("<CommPdAmtXX>222000</CommPdAmtXX>", xml)
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
        self.assertIn("<NameXX>New Broker</NameXX>", xml)
        self.assertIn("<InsCarrierName>Principal</InsCarrierName>", xml)
        self.assertIn("<NameXX>Principal Broker</NameXX>", xml)
        self.assertNotIn("<Name1>Old Broker</Name1>", xml)

    def test_schedule_a_replace_preserves_all_current_fields_and_uses_broker_multipart_rows(self):
        fields = [
            ExtractedField(
                filing_id="filing",
                source_field_name="3b. Amount of Commissions",
                normalized_field_name="commissions",
                mapped_rule_key="schedule_a_part_i_3b_amount_of_commissions",
                mapped_label="3b. Amount of Commissions",
                form_type=FormType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="250",
                proposed_value="250",
            ),
        ]
        records = [
            {
                "ftw_seq_no": "1",
                "query_results": {
                    "ScheduleDesc": "CIGNA",
                    "InsCarrierName": "Cigna",
                    "InsContractNum": "ABC123",
                    "PlanSponsorName": "Existing Sponsor",
                    "FootNotePage1": "Existing footnote",
                    "PensionEndPrevBalAmt": "1000",
                    "WlfrTypeBnftOthText": "Existing other benefit",
                    "Name1": "First Broker",
                    "CommPdAmt01": "100",
                    "AddressLine101": "100 Main Street",
                },
                "query_subparts": {
                    "Broker": [
                        {
                            "Name1": "First Broker",
                            "CommPdAmt01": "100",
                            "AddressLine101": "100 Main Street",
                        }
                    ]
                },
            }
        ]

        xml = build_schedule_a_records_update_xml(
            records,
            "1",
            fields,
            ftw_customer_id="1852103620",
            ftw_plan_id="2239036729",
            year="2025",
        )

        root = ET.fromstring(xml)
        schedule = root.find(".//DOLScheduleAData")
        self.assertIsNotNone(schedule)
        self.assertEqual(schedule.findtext("PlanSponsorName"), "Existing Sponsor")
        self.assertEqual(schedule.findtext("FootNotePage1"), "Existing footnote")
        self.assertEqual(schedule.findtext("PensionEndPrevBalAmt"), "1000")
        self.assertEqual(schedule.findtext("WlfrTypeBnftOthText"), "Existing other benefit")
        self.assertIsNone(schedule.find("Name1"))
        broker = schedule.find("./DOLSubPartData/Broker")
        self.assertIsNotNone(broker)
        self.assertEqual(broker.findtext("NameXX"), "First Broker")
        self.assertEqual(broker.findtext("CommPdAmtXX"), "250")
        self.assertEqual(broker.findtext("AddressLine1XX"), "100 Main Street")

    def test_schedule_a_replace_preflight_reports_dropped_fields_and_broker_values(self):
        records = [
            {
                "ftw_seq_no": "1",
                "query_results": {
                    "InsCarrierName": "Cigna",
                    "PlanSponsorName": "Existing Sponsor",
                    "Name1": "First Broker",
                },
                "query_subparts": {"Broker": [{"Name1": "First Broker"}]},
            }
        ]
        incomplete_xml = """<ftwLink><DataBatch><DOLScheduleAData>
          <TransactionType>2</TransactionType>
          <InsCarrierName>Cigna</InsCarrierName>
        </DOLScheduleAData></DataBatch></ftwLink>"""

        gaps = schedule_a_replacement_data_gaps(records, incomplete_xml)

        self.assertIn("sequence 1 missing field PlanSponsorName", gaps)
        self.assertIn("sequence 1 missing broker row 1 field NameXX", gaps)

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
