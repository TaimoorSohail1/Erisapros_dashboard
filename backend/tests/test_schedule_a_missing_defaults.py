"""Focused regressions for the owner's missing-value policy (2026-10-29)."""
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models import (ExtractedField, ExtractionValidationResult, FieldPriority, FormType,
                        NormalizedExtractionField, NormalizedExtractionResult, ScheduleABrokerRow,
                        ScheduleABrokerMoneyRow, SourceEvidence)
from app.services.schedule_a_customer_rules import apply_customer_defaults
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.xml_builder import build_schedule_a_records_update_xml, schedule_a_broker_update_values
from app.services.ftwilliams_contract import FTWPayloadValidationError
from app.services.mapping import map_extraction_to_rules


def header():
    return NormalizedExtractionField(field_name="1a. Name of Insurance Company", value="Example Life",
        page=1, source_text="Carrier Example Life", confidence=.99)


def field(key, value):
    return ExtractedField(filing_id="test", source_field_name=key, normalized_field_name=key,
        mapped_rule_key=key, mapped_label=key, form_type=FormType.SCHEDULE_A,
        priority=FieldPriority.HIGH, value=value, proposed_value=value)


class MissingScheduleADefaultTests(unittest.TestCase):
    def test_missing_covered_count_gets_zero_with_rule_provenance(self):
        result=resolve_schedule_a_result(NormalizedExtractionResult(provider="Test",fields=[header()]))
        count=next(f for f in result.fields if f.field_name.startswith("1e."))
        self.assertEqual(count.value,"0")
        self.assertEqual(count.decision,"AUTOMATIC")
        self.assertIsNone(count.page)
        self.assertEqual(count.evidence[0].provider,"Customer configured default")

    def test_existing_counts_and_other_missing_fields_are_preserved(self):
        for value in ["208","0","N/A","unreadable"]:
            result=NormalizedExtractionResult(provider="Test",fields=[header(),NormalizedExtractionField(field_name="1e. Persons Covered",value=value)])
            apply_customer_defaults(result)
            self.assertEqual(result.fields[1].value,value)
        result=NormalizedExtractionResult(provider="Test",fields=[header(),NormalizedExtractionField(field_name="1b. EIN",value="")])
        apply_customer_defaults(result)
        self.assertEqual(result.fields[1].value,"")

    def test_conflicting_unreadable_and_failed_results_are_not_defaulted(self):
        for values in [dict(decision="REVIEW_REQUIRED"),dict(candidate_values=["12"]),dict(validation_results=[ExtractionValidationResult(validator="source",status="ERROR")])]:
            blank=NormalizedExtractionField(field_name="1e. Persons Covered",value="",**values)
            result=NormalizedExtractionResult(provider="Test",fields=[header(),blank]);apply_customer_defaults(result)
            self.assertEqual(blank.value,"")
        result=NormalizedExtractionResult(provider="Local fallback - manual review required",fields=[header()])
        apply_customer_defaults(result)
        self.assertFalse(any(f.field_name.startswith("1e.")for f in result.fields))

    def test_missing_broker_values_are_defaulted_without_mutating_input(self):
        row=ScheduleABrokerRow(name="TEST BROKER",organization_code="3",confidence=.94)
        result=FTWilliamsReviewService()._normalized_schedule_a_broker_rows([row])[0]
        self.assertEqual((result.commission_total,result.fee_total,result.purpose),("0","0","Commission"))
        self.assertEqual((row.commission_total,row.fee_total,row.purpose),(None,None,None))
        self.assertEqual(result.confidence,.94)

    def test_provided_values_components_and_review_holds_are_not_erased(self):
        supplied=ScheduleABrokerRow(name="TEST",commission_total="-12.30",fee_total="4.25",purpose="Consulting",organization_code="3")
        result=FTWilliamsReviewService()._normalized_schedule_a_broker_rows([supplied])[0]
        self.assertEqual((result.commission_total,result.fee_total,result.purpose),("-12.30","4.25","Consulting"))
        held=supplied.model_copy(update={"commission_total":None,"purpose":None,"decision":"REVIEW_REQUIRED"})
        result=FTWilliamsReviewService()._normalized_schedule_a_broker_rows([held])[0]
        self.assertIsNone(result.commission_total);self.assertIsNone(result.purpose)
        parts=ScheduleABrokerRow(name="TEST",commission_rows=[ScheduleABrokerMoneyRow(amount="12.30")],organization_code="3")
        result=FTWilliamsReviewService()._normalized_schedule_a_broker_rows([parts])[0]
        self.assertIsNone(result.commission_total)

    def test_default_zeros_and_purpose_survive_repeated_normalization(self):
        result=NormalizedExtractionResult(provider="Test",fields=[header()],schedule_a_broker_rows=[
            ScheduleABrokerRow(name="TEST",organization_code="3",source_page=1,
                evidence=[SourceEvidence(page=1,source_text="TEST broker")],confidence=.95)])
        apply_customer_defaults(result);apply_customer_defaults(result)
        self.assertEqual(len(result.schedule_a_broker_rows),1)
        self.assertEqual(result.schedule_a_broker_rows[0].purpose,"Commission")

    def test_outbound_defaults_and_supplied_purposes_survive_zero_fees(self):
        values=schedule_a_broker_update_values([{"name":"TEST BROKER","organization_code":"3"}])
        self.assertEqual((values.get("CommPdAmt1"),values.get("FeesPdAmt1"),values.get("FeesPdText1")),("0","0","COMMISSION"))
        values=schedule_a_broker_update_values([{"name":"TEST","commission_total":"12.30","fee_total":"0","purpose":"Consulting","organization_code":"3"}])
        self.assertEqual(values["FeesPdText1"],"CONSULTING")
        self.assertEqual(values["CommPdAmt1"],"12.3")
        self.assertEqual(schedule_a_broker_update_values([None]),{})

    def test_selected_schedule_defaults_preserved_brokers_but_not_siblings_or_restores(self):
        records=[{"ftw_seq_no":n,"query_results":{"ScheduleDesc":"TEST"+n,"InsCarrierName":"Example Life","Name1":"EXISTING BROKER","Code1":"3"}}for n in ["1","2"]]
        updates=[field("schedule_a_part_i_1e_persons_covered_end_of_policy_year","0")]
        xml=build_schedule_a_records_update_xml(records,"1",updates,schedule_a_broker_rows=[None],year="2025")
        documents=ET.fromstring(xml).findall(".//DOLScheduleAData")
        selected=documents[0].find("./DOLSubPartData/Broker");sibling=documents[1].find("./DOLSubPartData/Broker")
        self.assertEqual(selected.findtext("CommPdAmtXX"),"0")
        self.assertEqual(selected.findtext("FeesPdAmtXX"),"0")
        self.assertEqual(selected.findtext("FeesPdTextXX"),"COMMISSION")
        self.assertEqual(documents[0].findtext("InsPrsnCoveredEoyCnt"),"0")
        self.assertIsNone(sibling.find("CommPdAmtXX"));self.assertIsNone(sibling.find("FeesPdTextXX"))
        restored=ET.fromstring(build_schedule_a_records_update_xml(records,None,[],year="2025"))
        self.assertFalse(restored.findall(".//FeesPdTextXX"));self.assertFalse(restored.findall(".//InsPrsnCoveredEoyCnt"))

    def test_field_only_send_does_not_add_unselected_broker_or_count_defaults(self):
        records=[{"ftw_seq_no":"1","query_results":{"ScheduleDesc":"TEST","Name1":"KEEP BROKER","Code1":"3"}}]
        xml=build_schedule_a_records_update_xml(records,"1",[field("schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier","100")],year="2025")
        root=ET.fromstring(xml)
        self.assertIsNone(root.find(".//FeesPdTextXX"));self.assertIsNone(root.find(".//CommPdAmtXX"));self.assertIsNone(root.find(".//InsPrsnCoveredEoyCnt"))

    def test_components_without_a_total_and_invalid_amounts_remain_blocked(self):
        for row in [ScheduleABrokerRow(name="TEST",commission_rows=[ScheduleABrokerMoneyRow(amount="12")],organization_code="3"),
                    ScheduleABrokerRow(name="TEST",commission_total="unknown",organization_code="3")]:
            with self.assertRaises(FTWPayloadValidationError):schedule_a_broker_update_values([row])

    def test_readback_never_fills_defaults_to_claim_a_success(self):
        service=FTWilliamsReviewService()
        expected={"__subparts__":{"Broker":[{"NameXX":"TEST","CodeXX":"3","CommPdAmtXX":"0","FeesPdAmtXX":"0","FeesPdTextXX":"COMMISSION"}]}}
        incomplete={"Broker":[{"Name1":"TEST","Code1":"3"}]}
        complete={"Broker":[{"Name1":"TEST","Code1":"3","CommPdAmt1":"0","FeesPdAmt1":"0","FeesPdText1":"COMMISSION"}]}
        self.assertTrue(service._compare_readback_document(FormType.SCHEDULE_A,expected,{},actual_subparts=incomplete))
        self.assertFalse(service._compare_readback_document(FormType.SCHEDULE_A,expected,{},actual_subparts=complete))

    def test_source_components_prevent_false_scalar_zero_or_purpose_defaults(self):
        result=NormalizedExtractionResult(provider="Test",fields=[header()],schedule_a_broker_rows=[
            ScheduleABrokerRow(name="TEST",commission_rows=[ScheduleABrokerMoneyRow(amount="12")],
                fee_rows=[ScheduleABrokerMoneyRow(amount="7",purpose="Consulting")])])
        apply_customer_defaults(result)
        self.assertFalse(any(f.field_name.startswith(("3b.","3c.","3d."))for f in result.fields))

    def test_review_hold_does_not_erase_a_supplied_scalar_purpose(self):
        source="COMMISSIONS 12; Fees Paid 0"
        result=resolve_schedule_a_result(NormalizedExtractionResult(provider="Test",fields=[
            NormalizedExtractionField(field_name=name,value=value,page=1,source_text=source,confidence=.99)
            for name,value in [("3b. Amount of Commissions","12"),("3c. Amount of Fees","0"),("3d. Purpose","COMMISSIONS")]],
            schedule_a_broker_rows=[ScheduleABrokerRow(name="TEST",commission_total="12",fee_total="0",decision="REVIEW_REQUIRED",
                validation_results=[ExtractionValidationResult(validator="business_rule_confirmation",status="ERROR",reason="Review source")])]))
        self.assertTrue(any(f.field_name.startswith("3d.")and f.value=="COMMISSIONS"for f in result.fields))
        self.assertEqual(result.schedule_a_broker_rows[0].decision,"REVIEW_REQUIRED")

    def test_later_supplied_duplicate_prevents_a_false_default(self):
        blank=NormalizedExtractionField(field_name="1e. Persons Covered",value="")
        supplied=NormalizedExtractionField(field_name="1e. Persons Covered (End of Policy Year)",value="142")
        result=NormalizedExtractionResult(provider="Test",fields=[header(),blank,supplied])
        apply_customer_defaults(result)
        self.assertEqual(blank.value,"");self.assertEqual(supplied.value,"142")

    def test_default_count_reaches_mapping_and_keeps_a_nonblank_current_count_under_review(self):
        result=resolve_schedule_a_result(NormalizedExtractionResult(provider="Test",fields=[header()]))
        mapped=map_extraction_to_rules("test",result.fields,form_type=FormType.SCHEDULE_A)["fields"]
        count=next(f for f in mapped if f.mapped_rule_key=="schedule_a_part_i_1e_persons_covered_end_of_policy_year")
        self.assertEqual(count.proposed_value,"0")
        service=FTWilliamsReviewService()
        empty=service._comparison_fields([count],{}, {})[0]
        existing=service._comparison_fields([count],{}, {"InsPrsnCoveredEoyCnt":"142"})[0]
        self.assertEqual(empty.decision,"WILL_UPDATE")
        self.assertEqual(existing.decision,"CONFLICT")


if __name__=="__main__":unittest.main()
