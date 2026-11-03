"""Client feedback rules at the review and FTW serialization boundaries."""
import unittest
import xml.etree.ElementTree as ET

from app.models import ExtractedField, FieldPriority, FormType, ScheduleABrokerRow, ExtractionValidationResult
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.ftwilliams_contract import normalize_ftw_update_value
from app.services.ftwilliams_tags import values_meaningfully_different
from app.services.xml_builder import build_single_document_update_xml, schedule_a_broker_update_values, build_schedule_a_records_update_xml


def field(rule, value, form=FormType.FORM_5500):
    return ExtractedField(filing_id="disposable-test", source_field_name=rule,
        normalized_field_name=rule, mapped_rule_key=rule, mapped_label=rule,
        form_type=form, priority=FieldPriority.HIGH, value=value, proposed_value=value)


class AetnaFeedbackRequirementsTests(unittest.TestCase):
    def test_only_commas_and_periods_are_ignored_in_plan_and_sponsor_names(self):
        for tag in ("PlanName", "SDName", "PlanSponsorName"):
            for current, proposed, changed in (
                ("A.B.C. PLAN", "ABC PLAN", False),
                ("FREEDOM GROUP, LLC", "FREEDOM GROUP LLC", False),
                ("FREEDOM GROUP: LLC", "FREEDOM GROUP LLC", True),
                ("A & B PLAN", "A AND B PLAN", True),
                ("FREEDOM SOLUTION GROUP", "FREEDOM SOLUTIONS GROUP", True),
            ):
                with self.subTest(tag=tag, current=current):
                    self.assertEqual(values_meaningfully_different(current, proposed, tag=tag), changed)

        proposal = field("form_5500_part_i_1a_plan_name", "ABC PLAN")
        comparison = FTWilliamsReviewService()._comparison_fields(
            [proposal], {"PlanName": "A.B.C. PLAN"}, {})[0]
        self.assertFalse(comparison.changed)
        xml = build_single_document_update_xml("DOL5500Data", [proposal], FormType.FORM_5500,
            transaction_type="1", year="2025", current_values={"PlanName": "A.B.C. PLAN"})
        self.assertIsNone(ET.fromstring(xml).find(".//PlanName"))
        schedule_proposal = field("schedule_a_part_iv_4a_plan_name", "ABC PLAN", FormType.SCHEDULE_A)
        replacement = build_schedule_a_records_update_xml([
            {"ftw_seq_no": "1", "query_results": {"ScheduleDesc": "TEST", "PlanName": "A.B.C. PLAN"}}
        ], "1", [schedule_proposal], year="2025")
        self.assertEqual(ET.fromstring(replacement).findtext(".//PlanName"), "A.B.C. PLAN")

    def test_outbound_names_use_first_verified_limit_characters_without_changing_source(self):
        carrier = "AETNA " + "X" * 80
        proposal = field("schedule_a_part_i_1a_name_of_insurance_company", carrier, FormType.SCHEDULE_A)
        xml = build_single_document_update_xml("DOLScheduleAData", [proposal], FormType.SCHEDULE_A,
            transaction_type="2", year="2025")
        self.assertEqual(ET.fromstring(xml).findtext(".//InsCarrierName"), carrier[:70])
        self.assertEqual(proposal.proposed_value, carrier)
        broker = "MY HEALTH AND BENEFITS ADMINISTRATION LLC"
        values = schedule_a_broker_update_values([{"name": broker, "commission_total": "50", "fee_total": "0"}])
        self.assertEqual(values["Name1"], broker[:35])
        for tag, limit in (("PlanName", 140), ("SDName", 70)):
            with self.subTest(tag=tag):
                self.assertEqual(normalize_ftw_update_value(FormType.FORM_5500, tag, "X" * (limit + 1)), "X" * limit)
        # A real name difference beyond the destination's limit still needs review.
        comparison = FTWilliamsReviewService()._comparison_fields([
            field("form_5500_part_i_1d_plan_sponsor_name", "X" * 70 + " DIFFERENT")
        ], {"SDName": "X" * 70}, {})[0]
        self.assertTrue(comparison.changed)
        self.assertFalse(comparison.validation_blocking)

    def test_proposed_broker_purpose_comes_from_nonzero_payment_categories(self):
        for commission, fees, expected in (
            ("10", "0", "COMMISSIONS"), ("0", "12", "FEES"),
            ("10", "12", "COMMISSIONS AND FEES"),
            ("-557", "0", "COMMISSIONS"), ("10", "-5", "COMMISSIONS AND FEES"),
            ("0", "0", "COMMISSION"),
        ):
            with self.subTest(commission=commission, fees=fees):
                original = {"name": "Test Broker", "commission_total": commission,
                    "fee_total": fees, "purpose": "Broker Bonus"}
                reviewed = FTWilliamsReviewService()._normalized_schedule_a_broker_rows([original])[0]
                self.assertEqual(reviewed.purpose, expected)
                values = schedule_a_broker_update_values([original])
                self.assertEqual(values["FeesPdText1"], expected)
                self.assertEqual(values["CommPdAmt1"], commission)
                self.assertEqual(values["FeesPdAmt1"], fees)
                self.assertEqual(original["purpose"], "Broker Bonus")

    def test_new_schedule_fills_missing_headers_from_its_parent_only(self):
        parent = {"PlanName": "PARENT PLAN", "SDName": "PARENT SPONSOR",
            "SDEIN": "12-3456789", "SponsDfePlanNum": "501",
            "PlanYearBeginDate": "01/01/2025", "PlanYearEndDate": "12/31/2025"}
        existing = [{"ftw_seq_no": "3", "query_results": {
            "ScheduleDesc": "DENTAL", "PlanSponsorName": "SIBLING SPONSOR", "InsContractNum": "OLD"}}]
        proposals = [field("schedule_a_part_i_1a_name_of_insurance_company", "AETNA", FormType.SCHEDULE_A),
            field("schedule_a_part_iv_4a_plan_name", "SOURCE PLAN", FormType.SCHEDULE_A)]
        xml = build_schedule_a_records_update_xml(existing, None, [], add_new_fields=proposals,
            new_schedule_desc="QAONLY", year="2025", form_5500_current_values=parent)
        schedules = ET.fromstring(xml).findall(".//DOLScheduleAData")
        new = schedules[-1]
        self.assertEqual(new.findtext("PlanSponsorName"), "PARENT SPONSOR")
        self.assertEqual(new.findtext("PlanName"), "SOURCE PLAN")
        self.assertEqual(new.findtext("EIN"), "12-3456789")
        self.assertEqual(new.findtext("PlanNum"), "501")
        self.assertEqual(new.findtext("PlanYearBeginDate"), parent["PlanYearBeginDate"])
        self.assertEqual(schedules[0].findtext("PlanSponsorName"), "SIBLING SPONSOR")
        self.assertEqual(parent["PlanName"], "PARENT PLAN")
        service_xml = FTWilliamsReviewService()._build_schedule_a_update_xml(
            proposals, existing, None, {"year": "2025"}, add_new_schedule_a=True,
            new_schedule_desc="QAONLY", form_5500_current_values=parent)
        self.assertEqual(ET.fromstring(service_xml).findall(".//DOLScheduleAData")[-1].findtext("PlanSponsorName"), "PARENT SPONSOR")

    def test_payment_purpose_does_not_clear_holds_or_change_snapshot_brokers(self):
        held = ScheduleABrokerRow(name="Held Broker", commission_total="10", fee_total="0",
            purpose="Unclassified", decision="REVIEW_REQUIRED", validation_results=[
                ExtractionValidationResult(validator="source", status="ERROR", reason="Missing evidence")])
        reviewed = FTWilliamsReviewService()._normalized_schedule_a_broker_rows([held])[0]
        self.assertEqual(reviewed.purpose, "Unclassified")
        self.assertEqual(reviewed.decision, "REVIEW_REQUIRED")
        records = [{"ftw_seq_no": "1", "query_results": {"ScheduleDesc": "TEST"}, "query_subparts": {"Broker": [
            {"Name1": "CURRENT ONE", "CommPdAmt1": "10", "FeesPdAmt1": "0", "FeesPdText1": "Old purpose"},
            {"Name1": "CURRENT TWO", "CommPdAmt1": "20", "FeesPdAmt1": "0", "FeesPdText1": "Other purpose"}]}}]
        restore = build_schedule_a_records_update_xml(records, None, [], year="2025")
        self.assertEqual([r.findtext("FeesPdTextXX") for r in ET.fromstring(restore).findall(".//Broker")], ["Old purpose", "Other purpose"])
        updated = build_schedule_a_records_update_xml(records, "1", [], year="2025", schedule_a_broker_rows=[
            {"name": "CURRENT ONE", "commission_total": "15", "fee_total": "0", "purpose": "Broker Bonus"}])
        brokers = ET.fromstring(updated).findall(".//Broker")
        self.assertEqual([r.findtext("FeesPdTextXX") for r in brokers], ["Other purpose", "COMMISSIONS"])

    def test_schema_rejects_unshortened_raw_payload_and_accepts_serialized_names(self):
        from app.services.ftwilliams_schema import FTWilliamsSchemaService
        raw = '<ftwLink><DataBatch><DOLScheduleAData><Year>2025</Year><InsCarrierName>' + 'X' * 71 + '</InsCarrierName></DOLScheduleAData></DataBatch></ftwLink>'
        service = FTWilliamsSchemaService()
        self.assertFalse(service.validate_outgoing_xml(FormType.SCHEDULE_A, "2025", raw).valid)
        serialized = build_single_document_update_xml("DOLScheduleAData", [
            field("schedule_a_part_i_1a_name_of_insurance_company", "X" * 71, FormType.SCHEDULE_A)
        ], FormType.SCHEDULE_A, transaction_type="2", year="2025")
        self.assertTrue(service.validate_outgoing_xml(FormType.SCHEDULE_A, "2025", serialized).valid)


if __name__ == "__main__":
    unittest.main()
