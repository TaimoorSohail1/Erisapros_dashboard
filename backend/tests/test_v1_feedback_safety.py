import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models import DocumentType, ExtractedField, ExtractedFieldStatus, FormType, ScheduleABrokerRow, ScheduleABrokerMatch, FTWilliamsReview, FTWilliamsBrokerMatchesRequest
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.ftwilliams_contract import normalize_ftw_update_value, FTWPayloadValidationError
from app.services.schedule_a_broker_matching import match_schedule_a_brokers
from app.services.xml_builder import build_schedule_a_records_update_xml
from app.services.extractor import extract_vsp_broker_rows, extract_vsp_schedule_a_fields
from app.services.schedule_a_semantic_layer import SemanticDocument, enrich_schedule_a_result
from app.models import NormalizedExtractionField, NormalizedExtractionResult, SourceEvidence


class V1FeedbackSafetyTests(unittest.TestCase):
    def test_empty_replacement_roster_readback_must_confirm_old_rows_are_gone(self):
        xml = build_schedule_a_records_update_xml([{"ftw_seq_no": "1", "query_results": {
            "ScheduleDesc": "TEST"}, "query_subparts": {"Broker": [{
                "NameXX": "Old broker", "CommPdAmtXX": "100"}]}}], "1", [], year="2025",
            schedule_a_broker_rows=[], replace_broker_roster=True)
        service = FTWilliamsReviewService()
        expected = service._update_documents(xml, "DOLScheduleAData")[0]
        self.assertEqual(expected.get("__subparts__"), {"Broker": []})
        mismatch = service._compare_readback_document(FormType.SCHEDULE_A, expected,
            {"ScheduleDesc": "TEST", "OverrideCommissionsAndFees": "1"},
            actual_subparts={"Broker": [{"NameXX": "Old broker", "CommPdAmtXX": "100"}]})
        self.assertTrue(any(row.get("tag") == "DOLSubPartData/Broker" for row in mismatch))
        self.assertEqual(service._compare_readback_document(FormType.SCHEDULE_A, expected,
            {"ScheduleDesc": "TEST", "OverrideCommissionsAndFees": "1"}, actual_subparts={}), [])

    def test_confirmed_empty_roster_does_not_reuse_stale_vendor_payment_totals(self):
        records = [{"ftw_seq_no": "1", "query_results": {"ScheduleDesc": "TEST",
            "InsBrokerCommTotAmt": "100", "InsBrokerFeesTotAmt": "20", "PlanYearBeginDate": "01/01/2025"},
            "query_subparts": {"Broker": [{"NameXX": "Old broker", "CommPdAmtXX": "100"}]}}]
        xml = build_schedule_a_records_update_xml(records, "1", [], year="2025",
            schedule_a_broker_rows=[], replace_broker_roster=True)
        document = ET.fromstring(xml).find(".//DOLScheduleAData")
        self.assertEqual(document.findtext("OverrideCommissionsAndFees"), "1")
        self.assertIsNone(document.find("InsBrokerCommTotAmt"))
        self.assertIsNone(document.find("InsBrokerFeesTotAmt"))
        self.assertEqual(document.findtext("PlanYearBeginDate"), "01/01/2025")
        self.assertEqual(document.findall(".//Broker"), [])

    def test_all_supported_free_text_limits_are_applied_only_at_send_boundary(self):
        for form, tag, size in ((FormType.FORM_5500, "SDName", 70),
                (FormType.FORM_5500, "SDCity", 30), (FormType.SCHEDULE_A, "FeesPdText1", 70),
                (FormType.SCHEDULE_A, "Name1", 35), (FormType.SCHEDULE_A, "City1", 30)):
            with self.subTest(tag=tag):
                source = "X" * (size + 10)
                self.assertEqual(normalize_ftw_update_value(form, tag, source), source[:size])
                self.assertEqual(len(source), size + 10)
                with self.assertRaises(FTWPayloadValidationError):
                    normalize_ftw_update_value(form, tag, source, truncate_text=False)
        with self.assertRaises(FTWPayloadValidationError):
            normalize_ftw_update_value(FormType.SCHEDULE_A, "InsContractNum", "1" * 41)
        with self.assertRaises(FTWPayloadValidationError):
            normalize_ftw_update_value(FormType.SCHEDULE_A, "AddressLine11", "X" * 40)

    def test_payment_purpose_change_is_an_actual_broker_update(self):
        review = FTWilliamsReview(filing_id="qa", schedule_a_broker_rows=[ScheduleABrokerRow(
            name="Example Broker", commission_total="100", fee_total="20", purpose="COMMISSIONS AND FEES")],
            schedule_a_broker_matches=[ScheduleABrokerMatch(extracted_index=0, ftw_index=0,
                status="AUTO_MATCHED", resolved=True, current_row=ScheduleABrokerRow(name="Example Broker",
                    commission_total="100", fee_total="20", purpose="Broker Bonus"))])
        self.assertTrue(FTWilliamsReviewService()._review_has_broker_updates(review))

    def test_complete_source_roster_removes_carried_forward_and_zero_payment_rows_only_on_selected_schedule(self):
        from app.services.schedule_a_broker_roster import attest_source_broker_roster, broker_roster_is_complete
        source_rows = [ScheduleABrokerRow(name="Current broker", address_line_1="10 Main St",
            commission_total="100", fee_total="0", organization_code="3", source_page=1,
            evidence=[SourceEvidence(provider="Local source", page=1, source_text="Current broker 10 Main St 100 0")]),
            ScheduleABrokerRow(name="Unpaid broker", commission_total="0", fee_total="0", source_page=1,
            evidence=[SourceEvidence(provider="Local source", page=1, source_text="Unpaid broker 0 0")])]
        result = NormalizedExtractionResult(provider="test", fields=[
            NormalizedExtractionField(field_name="3b. Amount of Commissions", value="100"),
            NormalizedExtractionField(field_name="3c. Amount of Fees", value="0")], schedule_a_broker_rows=source_rows,
            raw={"extraction_stages": [{"stage": "structured_provider_normalized",
                "broker_rows": [row.model_dump(mode="json") for row in source_rows]}]})
        result.raw['independent_source_page_coverage']={'source_sha256':'a'*64,'page_count':1,
            'pages':[{'page':1,'text_sha256':'b'*64,'readable':True}]}
        attest_source_broker_roster(result, [row.model_copy(deep=True) for row in source_rows], "a" * 64)
        self.assertTrue(broker_roster_is_complete(result.schedule_a_broker_rows))
        current = [{"ftw_seq_no": "1", "query_results": {"ScheduleDesc": "TEST"}, "query_subparts": {"Broker": [
            {"NameXX": "Old broker", "CommPdAmtXX": "250"},
            {"NameXX": "Current broker", "AddressLine1XX": "10 Main St", "CommPdAmtXX": "100", "FeesPdAmtXX": "0"}]}},
            {"ftw_seq_no": "2", "query_results": {"ScheduleDesc": "SIBLING"}, "query_subparts": {"Broker": [
                {"NameXX": "Sibling broker", "CommPdAmtXX": "20"}]}}]
        service = FTWilliamsReviewService()
        _, resolved = service._resolve_schedule_a_brokers(result.schedule_a_broker_rows, current, "1", [], create_new=False)
        xml = service._build_schedule_a_update_xml([], current, "1", {"year": "2025"},
            schedule_a_broker_rows=resolved, replace_broker_roster=True)
        documents = ET.fromstring(xml).findall(".//DOLScheduleAData")
        self.assertEqual([row.findtext("NameXX") for row in documents[0].findall(".//Broker")], ["CURRENT BROKER"])
        self.assertEqual(documents[1].findtext(".//Broker/NameXX"), "Sibling broker")
        result.schedule_a_broker_rows[0].commission_total = "101"
        self.assertFalse(broker_roster_is_complete(result.schedule_a_broker_rows), "A later edit must invalidate completeness evidence.")

    def test_client_approved_vsp_combined_label_is_commission_only(self):
        source = """VSP vision care Schedule A Form (5500) Insurance Information
Insurance Carrier: Vision Service Plan
Commissions/Fees Paid for Policy Agent or Broker or Contract Year
Example Advisory Inc $1,234.5650 Other RdBoston MA 02110
Total Payments Made to Carrier: $39,000.12
"""
        rows = extract_vsp_broker_rows([(1, source)])
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0].decision, "REVIEW_REQUIRED")
        self.assertEqual(rows[0].commission_total, "1,234.56")
        self.assertEqual(rows[0].fee_total, "0")
        result = enrich_schedule_a_result(NormalizedExtractionResult(provider="local",
            fields=extract_vsp_schedule_a_fields([(1, source)]), schedule_a_broker_rows=rows),
            SemanticDocument.from_page_texts([(1, source)]), rules=[])
        self.assertFalse(any(item.get("type") == "combined_commission_fee_source"
            for item in result.raw.get("semantic_resolution", {}).get("ambiguities", [])))
        values = {field.field_name: field.value for field in result.fields}
        self.assertEqual(values["10a. Total premiums or subscription charges paid to carrier"], "39,000.12")

    def test_verified_worksheet_beginning_counts_update_without_conflict(self):
        service = FTWilliamsReviewService()
        for rule, tag in (("form_5500_part_ii_11_total_participants_at_beginning_of_year", "TotPartcpBoyCnt"),
                          ("form_5500_part_ii_13_active_participants_at_beginning", "TotActPartcpBoyCnt")):
            proposal = ExtractedField(filing_id="qa", source_field_name=rule, normalized_field_name=rule,
                form_type=FormType.FORM_5500, source_document_type=DocumentType.PLAN_WORKSHEET,
                mapped_rule_key=rule, value="149", proposed_value="149", confidence=.99,
                status=ExtractedFieldStatus.MATCHED, source_text="Worksheet beginning count: 149")
            result = service._comparison_fields([proposal], {tag: "142"}, {}, update_fields=[proposal])[0]
            self.assertEqual(result.ftw_tag, tag)
            self.assertEqual(result.decision, "WILL_UPDATE")
            self.assertTrue(result.update_included)

    def test_same_broker_name_with_conflicting_location_requires_confirmation(self):
        source = ScheduleABrokerRow(name="Example Broker", address_line_1="10 Main St",
            city="Boston", state="MA", zip_code="02199", commission_total="100")
        for current in (
            {"NameXX": "Example Broker", "AddressLine1XX": "20 Other St", "ZipCodeXX": "02199"},
            {"NameXX": "Example Broker", "AddressLine1XX": "10 Main St", "ZipCodeXX": "10001"},
            {"NameXX": "Example Broker", "AddressLine1XX": "10 Main St", "StateXX": "NY"},
        ):
            with self.subTest(current=current):
                match = match_schedule_a_brokers([source], [current])[0]
                self.assertFalse(match.resolved)
                self.assertEqual(match.status, "NEEDS_CONFIRMATION")
                self.assertIn(0, match.candidate_ftw_indexes)

    def test_shared_address_alone_does_not_prove_two_different_brokers_are_the_same(self):
        source = ScheduleABrokerRow(name="Independent Brokerage LLC", address_line_1="10 Main St", zip_code="10001")
        match = match_schedule_a_brokers([source], [{"NameXX": "Other Agency LLC",
            "AddressLine1XX": "10 Main St", "ZipCodeXX": "10001"}])[0]
        self.assertFalse(match.resolved)
        self.assertEqual(match.status, "NEEDS_CONFIRMATION")

    def test_explicit_broker_confirmation_can_resolve_location_difference(self):
        source = ScheduleABrokerRow(name="Example Broker", address_line_1="10 Main St")
        current = {"NameXX": "Example Broker", "AddressLine1XX": "20 Other St"}
        match = match_schedule_a_brokers([source], [current], decisions={0: {"ftw_index": 0}})[0]
        self.assertTrue(match.resolved)
        self.assertEqual(match.status, "CONFIRMED")

    def test_reviewer_selected_row_cannot_be_taken_by_earlier_auto_match(self):
        rows = [ScheduleABrokerRow(name="Example Broker"), ScheduleABrokerRow(name="Other Broker")]
        matches = match_schedule_a_brokers(rows, [{"NameXX": "Example Broker"}],
            decisions={1: {"ftw_index": 0}})
        self.assertEqual(matches[1].ftw_index, 0)
        self.assertEqual(matches[1].status, "CONFIRMED")
        self.assertEqual(matches[0].status, "AUTO_NEW")

    def test_existing_duplicate_rows_do_not_shift_the_selected_payment_target(self):
        previous = {"NameXX": "Existing broker", "CommPdAmtXX": "10", "FeesPdAmtXX": "0"}
        selected = {"NameXX": "Selected broker", "CommPdAmtXX": "5", "FeesPdAmtXX": "0"}
        records = [{"ftw_seq_no": "1", "query_results": {"ScheduleDesc": "TEST"},
            "query_subparts": {"Broker": [previous, dict(previous), selected]}}]
        xml = build_schedule_a_records_update_xml(records, "1", [], year="2025",
            schedule_a_broker_rows=[None, None, ScheduleABrokerRow(name="Selected broker",
                commission_total="20", fee_total="0", organization_code="3")])
        rows = ET.fromstring(xml).findall(".//Broker")
        recipients = [row for row in rows if row.findtext("NameXX") == "SELECTED BROKER"]
        self.assertEqual(len(recipients), 1)
        self.assertEqual(recipients[0].findtext("CommPdAmtXX"), "20")
        self.assertFalse(any(row.findtext("NameXX", "").lower() == "selected broker"
            and row.findtext("CommPdAmtXX") == "5" for row in rows))

    def test_compensation_send_still_derives_payment_indicator(self):
        for amount, expected in (("100", "0"), ("0", "1")):
            with self.subTest(amount=amount):
                row = ScheduleABrokerRow(name="Example Broker", commission_total=amount,
                    fee_total="0", organization_code="3")
                records = [{"ftw_seq_no": "1", "query_results": {"ScheduleDesc": "MEDICAL",
                    "InsBrokerCommTotAmt": amount, "InsBrokerFeesTotAmt": "0"}}]
                xml = build_schedule_a_records_update_xml(records, "1", [],
                    ftw_customer_id="111", ftw_plan_id="222", year="2025",
                    schedule_a_broker_rows=[row])
                self.assertEqual(ET.fromstring(xml).findtext(".//OverrideCommissionsAndFees"), expected)

    def test_count_only_send_preserves_existing_payment_indicator_and_brokers(self):
        field = ExtractedField(id="covered", filing_id="filing", source_field_name="Persons covered",
            normalized_field_name="persons_covered", form_type=FormType.SCHEDULE_A,
            mapped_rule_key="schedule_a_part_i_1e_persons_covered_end_of_policy_year",
            value="21", proposed_value="21")
        for indicator in (None, "", "0", "1"):
            with self.subTest(indicator=indicator):
                current = {"ScheduleDesc": "MEDICAL", "InsPrsnCoveredEoyCnt": "20",
                    "InsBrokerCommTotAmt": "0", "InsBrokerFeesTotAmt": "0"}
                if indicator is not None:
                    current["OverrideCommissionsAndFees"] = indicator
                records = [{"ftw_seq_no": "1", "query_results": current,
                    "query_subparts": {"Broker": [{"NameXX": "Existing recipient", "CodeXX": "3", "CommPdAmtXX": "0", "FeesPdAmtXX": "0"}]}},
                    {"ftw_seq_no": "2", "query_results": {"ScheduleDesc": "DENTAL", "InsPrsnCoveredEoyCnt": "7"}}]
                xml = build_schedule_a_records_update_xml(records, "1", [field],
                    ftw_customer_id="111", ftw_plan_id="222", year="2025", schedule_a_broker_rows=[])
                documents = ET.fromstring(xml).findall(".//DOLScheduleAData")
                selected = documents[0]
                self.assertEqual(selected.findtext("InsPrsnCoveredEoyCnt"), "21")
                payment = selected.find("OverrideCommissionsAndFees")
                if indicator is None:
                    self.assertIsNone(payment)
                else:
                    self.assertIsNotNone(payment)
                    self.assertEqual(payment.text or "", indicator)
                self.assertEqual(selected.findtext(".//Broker/NameXX"), "Existing recipient")
                self.assertEqual(documents[1].findtext("InsPrsnCoveredEoyCnt"), "7")


class BrokerDecisionPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_broker_indexes_are_rejected_before_persisting(self):
        filing = SimpleNamespace(schedule_a_broker_rows=[ScheduleABrokerRow(name="Source broker")])
        review = FTWilliamsReview(filing_id="qa", ftw_seq_no="1", schedule_a_records=[
            {"ftw_seq_no": "1", "query_subparts": {"Broker": [{"NameXX": "Current broker"}]}}])
        repo = SimpleNamespace(get_filing=AsyncMock(return_value=filing),
            get_ftwilliams_review=AsyncMock(return_value=review), upsert_ftwilliams_review=AsyncMock(), add_audit=AsyncMock())
        service = FTWilliamsReviewService()
        with patch('app.services.ftwilliams_review.get_repository', return_value=repo), \
             patch.object(service, 'prepare_review', AsyncMock(return_value=review)):
            for decision in ({"extracted_index": 4, "ftw_index": 0},
                             {"extracted_index": 0, "ftw_index": 4},
                             {"extracted_index": -1, "create_new": True}):
                with self.subTest(decision=decision), self.assertRaises(ValueError):
                    await service.set_schedule_a_broker_matches("qa", FTWilliamsBrokerMatchesRequest(decisions=[decision]))
        repo.upsert_ftwilliams_review.assert_not_awaited()
        self.assertEqual(review.schedule_a_broker_matches, [])


if __name__ == "__main__":
    unittest.main()
