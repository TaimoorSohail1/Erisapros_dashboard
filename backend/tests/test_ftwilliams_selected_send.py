import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from app.models import (Filing, FilingStatus, FTWilliamsReview, FTWilliamsSendUpdateRequest,
    ExtractedField, FormType, FTWilliamsComparisonField, ScheduleABrokerRow)
from app.repositories import MemoryRepository
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.config import Settings
from app.models import DocumentType, FTWilliamsQueryResponse, FTWilliamsStatusItem
from test_ftwilliams_review import FakeFTWilliamsService
from datetime import datetime, timezone


class SelectedSendTests(unittest.TestCase):
    def test_public_schedule_send_ignores_unselected_broker_and_classification_review(self):
        class Vendor(FakeFTWilliamsService):
            def __init__(self):
                super().__init__()
                self.updated = False
                self.sent = []

            async def run_query(self, payload):
                response = await super().run_query(payload)
                if payload.operation == "query_5500":
                    response.statuses[0].query_results.update({"SponsDfePlanNum": "501",
                        "PlanYearEndDate": "12/31/2025", "LockedStatus": "Unlocked"})
                if payload.operation == "query_schedule_a" and payload.ftw_seq_no == "2":
                    response.statuses[0].query_results["InsPrsnCoveredEoyCnt"] = "12" if self.updated else "10"
                    response.statuses[0].query_subparts = {"Broker": [{"NameXX": "Current broker", "CodeXX": "3", "CommPdAmtXX": "25"}]}
                return response

            async def send_xml(self, operation, request_xml):
                self.updated = True
                self.sent.append((operation, self.mask_key_id(request_xml)))
                return FTWilliamsQueryResponse(operation=operation, configured=True, sent=True,
                    request_xml=self.mask_key_id(request_xml), success=True, raw_response="<ftwLinkResponse />",
                    statuses=[FTWilliamsStatusItem(type=operation, error_code="0")])

        async def scenario():
            repo = MemoryRepository()
            filing = await repo.create_filing(Filing(file_name="test.pdf", content_type="application/pdf",
                file_size=1, s3_key="test.pdf", status=FilingStatus.NEEDS_REVIEW,
                schedule_a_classification_signals=["EXPLICIT_EXPERIENCE_RATED"],
                schedule_a_broker_rows=[ScheduleABrokerRow(name="Unresolved proposed broker")]))
            selected = ExtractedField(filing_id=filing.id, source_field_name="Covered people", normalized_field_name="covered",
                form_type=FormType.SCHEDULE_A, source_document_type=DocumentType.SCHEDULE_A,
                mapped_rule_key="schedule_a_part_i_1e_persons_covered_end_of_policy_year", value="12", proposed_value="12")
            fields = [selected]
            for key, value, form in [
                ("form_5500_part_i_1e_plan_sponsor_ein", "73-0759701", FormType.FORM_5500),
                ("form_5500_part_i_1b_plan_number_pn", "501", FormType.FORM_5500),
                ("form_5500_part_i_7_plan_year_ending_date", "12/31/2025", FormType.FORM_5500),
                ("schedule_a_part_i_1a_name_of_insurance_company", "BlueCross BlueShield of Oklahoma", FormType.SCHEDULE_A),
                ("schedule_a_part_i_1b_insurance_carrier_ein", "36-1236610", FormType.SCHEDULE_A),
                ("schedule_a_part_i_1d_contract_policy_number", "Y00979", FormType.SCHEDULE_A)]:
                fields.append(ExtractedField(filing_id=filing.id, source_field_name=key, normalized_field_name=key,
                    mapped_rule_key=key, form_type=form, value=value, proposed_value=value))
            await repo.add_fields(fields)
            vendor = Vendor()
            settings = Settings(ftw_auto_edit_checks_enabled=False, ftw_pdf_audit_enabled=False,
                ftwlink_schedule_a_updates_enabled=True, ftw_schema_validation_enabled=False, _env_file=None)
            with patch("app.services.ftwilliams_review.get_repository", return_value=repo), \
                patch("app.services.ftwilliams_review.get_settings", return_value=settings):
                result = await FTWilliamsReviewService(vendor).send_approved_update(filing.id,
                    FTWilliamsSendUpdateRequest(selected_field_ids=[selected.id], include_broker_updates=False))
            self.assertEqual(len(vendor.sent), 1)
            self.assertEqual(vendor.sent[0][0], "update_schedule_a")
            self.assertIn("Current broker", vendor.sent[0][1])
            self.assertNotIn("Unresolved proposed broker", vendor.sent[0][1])
            self.assertTrue(result.update_verification_success, result.error_message)
            self.assertEqual(result.update_confirmed_count, 1)
            self.assertTrue(result.schedule_a_contract_type_mismatch)
        asyncio.run(scenario())

    def test_manual_send_route_does_not_dispatch_unselected_automatic_changes(self):
        from app.api.filings import send_approved_ftwilliams_update
        async def scenario():
            review = FTWilliamsReview(filing_id="f")
            service = AsyncMock()
            service.send_approved_update.return_value = review
            payload = FTWilliamsSendUpdateRequest(selected_field_ids=["chosen"])
            with patch("app.api.filings.FTWilliamsReviewService", return_value=service), \
                patch("app.api.filings.continue_ftw_automation", new=AsyncMock()) as automatic:
                result = await send_approved_ftwilliams_update("f", payload)
            self.assertIs(result["ftw_review"], review)
            service.send_approved_update.assert_awaited_once_with("f", payload)
            automatic.assert_not_awaited()
        asyncio.run(scenario())

    def test_schedule_selection_preserves_other_values_sibling_and_current_brokers(self):
        field = ExtractedField(id="covered", filing_id="f", source_field_name="Covered people",
            normalized_field_name="covered", mapped_label="Covered people", form_type=FormType.SCHEDULE_A,
            mapped_rule_key="schedule_a_part_i_1e_persons_covered_end_of_policy_year",
            proposed_value="12")
        review = FTWilliamsReview(filing_id="f", ftw_customer_id="customer", ftw_plan_id="plan", year="2025",
            ftw_seq_no="1", schedule_a_match={"ftw_seq_no": "1"},
            fields=[FTWilliamsComparisonField(field_id="covered", label="Covered people", form_type=FormType.SCHEDULE_A,
                changed=True, update_included=True, proposed_value="12")],
            schedule_a_records=[{"ftw_seq_no": "1", "query_results": {
                "InsCarrierName": "Existing carrier", "InsContractNum": "ABC", "InsCarrierEIN": "36-1236610",
                "InsPrsnCoveredEoyCnt": "10", "WlfrTotChargesPaidAmt": "500"},
                "query_subparts": {"Broker": [{"NameXX": "Existing broker", "CodeXX": "3", "CommPdAmtXX": "25"}]}},
                {"ftw_seq_no": "2", "query_results": {"InsCarrierName": "Sibling carrier",
                    "InsContractNum": "SIB", "InsCarrierEIN": "36-1236611", "InsPrsnCoveredEoyCnt": "9"}}],
            schedule_a_broker_rows=[ScheduleABrokerRow(name="Unresolved proposed broker")],
            schedule_a_broker_match_complete=False)
        FTWilliamsReviewService()._prepare_selected_update(review, [field], [field.id], include_broker_updates=False)
        review.update_xml_schedule_a = FTWilliamsReviewService().ftwilliams.mask_key_id(review.update_xml_schedule_a)
        self.assertEqual(review.update_xml_schedule_a.count("<DOLScheduleAData>"), 2)
        self.assertIn("<InsPrsnCoveredEoyCnt>12</InsPrsnCoveredEoyCnt>", review.update_xml_schedule_a)
        self.assertIn("<InsPrsnCoveredEoyCnt>9</InsPrsnCoveredEoyCnt>", review.update_xml_schedule_a)
        self.assertIn("Existing broker", review.update_xml_schedule_a)
        self.assertNotIn("Unresolved proposed broker", review.update_xml_schedule_a)
        self.assertIn("<WlfrTotChargesPaidAmt>500</WlfrTotChargesPaidAmt>", review.update_xml_schedule_a)

    def test_selection_errors_name_the_affected_field_and_require_explicit_selection(self):
        service = FTWilliamsReviewService()
        field = ExtractedField(id="bad", filing_id="f", source_field_name="NAIC", normalized_field_name="naic",
            form_type=FormType.SCHEDULE_A, proposed_value="1")
        review = FTWilliamsReview(filing_id="f", fields=[FTWilliamsComparisonField(field_id="bad", label="NAIC",
            form_type=FormType.SCHEDULE_A, changed=True, validation_blocking=True, validation_message="Expected 5 digits")])
        with self.assertRaisesRegex(ValueError, "Select at least one field"):
            service._prepare_selected_update(review, [field], [], include_broker_updates=False)
        with self.assertRaisesRegex(ValueError, "no longer belongs to this filing"):
            service._prepare_selected_update(review, [field], ["another-filing-field"], include_broker_updates=False)
        with self.assertRaisesRegex(ValueError, "NAIC: Expected 5 digits"):
            service._prepare_selected_update(review, [field], [field.id], include_broker_updates=False)

    def test_manual_approval_endpoints_are_retired_without_mutation(self):
        from app.api.filings import approve_filing, unapprove_filing
        from app.models import ApproveRequest
        from fastapi import HTTPException
        async def scenario():
            for operation in (approve_filing("f", ApproveRequest()), unapprove_filing("f")):
                with self.assertRaises(HTTPException) as raised:
                    await operation
                self.assertEqual(raised.exception.status_code, 410)
                self.assertIn("approval has been removed", raised.exception.detail)
        asyncio.run(scenario())

    def test_public_send_selects_changes_and_refuses_locked_vendor_target(self):
        class Vendor(FakeFTWilliamsService):
            def __init__(self, locked=False):
                super().__init__()
                self.sent = []
                self.updated = False
                self.locked = locked

            async def run_query(self, payload):
                response = await super().run_query(payload)
                if payload.operation == "query_5500":
                    response.statuses[0].query_results.update({
                        "TotActivePartcpCnt": "101" if self.updated else "100",
                        "SDName": "Existing sponsor", "SponsDfePlanNum": "501",
                        "PlanYearEndDate": "12/31/2025", "LockedStatus": "Locked" if self.locked else "Unlocked"})
                return response

            async def send_xml(self, operation, request_xml):
                self.sent.append((operation, request_xml))
                self.updated = True
                return FTWilliamsQueryResponse(operation=operation, configured=True, sent=True,
                    request_xml=request_xml, success=True, raw_response="<ftwLinkResponse />",
                    statuses=[FTWilliamsStatusItem(type=operation, error_code="0")])

        async def scenario():
            repo = MemoryRepository()
            filing = await repo.create_filing(Filing(file_name="test.pdf", content_type="application/pdf",
                file_size=1, s3_key="test.pdf", status=FilingStatus.NEEDS_REVIEW))
            def field(key, value):
                return ExtractedField(filing_id=filing.id, source_field_name=key, normalized_field_name=key,
                    mapped_rule_key=key, mapped_label=key, form_type=FormType.FORM_5500,
                    source_document_type=DocumentType.PLAN_WORKSHEET, value=value, proposed_value=value)
            selected = field("form_5500_part_ii_14_active_participants_at_end", "101")
            await repo.add_fields([selected,
                field("form_5500_part_i_1e_plan_sponsor_ein", "73-0759701"),
                field("form_5500_part_i_1b_plan_number_pn", "501"),
                field("form_5500_part_i_7_plan_year_ending_date", "12/31/2025"),
                field("form_5500_part_i_1d_plan_sponsor_name", "Do not send sponsor")])
            vendor = Vendor()
            settings = Settings(ftw_auto_edit_checks_enabled=False, ftw_pdf_audit_enabled=False,
                ftw_schema_validation_enabled=False, _env_file=None)
            with patch("app.services.ftwilliams_review.get_repository", return_value=repo), \
                patch("app.services.ftwilliams_review.get_settings", return_value=settings):
                result = await FTWilliamsReviewService(vendor).send_approved_update(filing.id,
                    FTWilliamsSendUpdateRequest(selected_field_ids=[selected.id]))
            self.assertEqual(len(vendor.sent), 1)
            self.assertEqual(vendor.sent[0][0], "update_5500")
            self.assertIn("<TotActivePartcpCnt>101</TotActivePartcpCnt>", vendor.sent[0][1])
            self.assertNotIn("Do not send sponsor", vendor.sent[0][1])
            self.assertTrue(result.update_verification_success, result.error_message)
            self.assertEqual(result.update_attempted_count, 1)
            self.assertEqual(result.update_confirmed_count, 1)
            self.assertIsNone((await repo.get_filing(filing.id)).approved_at)
            historical_approval = datetime(2025, 1, 1, tzinfo=timezone.utc)
            await repo.update_filing(filing.id, {"approved_at": historical_approval})
            locked_vendor = Vendor(locked=True)
            with patch("app.services.ftwilliams_review.get_repository", return_value=repo), \
                patch("app.services.ftwilliams_review.get_settings", return_value=settings):
                with self.assertRaisesRegex(ValueError, "locked and not editable"):
                    await FTWilliamsReviewService(locked_vendor).send_approved_update(filing.id,
                        FTWilliamsSendUpdateRequest(selected_field_ids=[selected.id]))
            self.assertEqual(locked_vendor.sent, [])
            self.assertEqual((await repo.get_filing(filing.id)).approved_at, historical_approval)
        asyncio.run(scenario())

    def test_manual_send_does_not_require_filing_approval(self):
        async def scenario():
            repo = MemoryRepository()
            filing = await repo.create_filing(Filing(file_name="test.pdf", content_type="application/pdf",
                file_size=1, s3_key="test.pdf", status=FilingStatus.NEEDS_REVIEW))
            review = FTWilliamsReview(filing_id=filing.id)
            service = FTWilliamsReviewService()
            service.approve_and_update = AsyncMock(return_value=review)
            with patch("app.services.ftwilliams_review.get_repository", return_value=repo):
                result = await service.send_approved_update(filing.id,
                    FTWilliamsSendUpdateRequest(selected_field_ids=["field-1"]))
            self.assertIs(result, review)
            self.assertEqual(service.approve_and_update.await_args.kwargs["selected_field_ids"], ["field-1"])
            self.assertEqual((await repo.get_filing(filing.id)).status, FilingStatus.NEEDS_REVIEW)
        asyncio.run(scenario())

    def test_selected_payload_ignores_unselected_invalid_fields_and_brokers(self):
        fields = [ExtractedField(id="name", filing_id="f", source_field_name="Sponsor",
            normalized_field_name="sponsor", mapped_rule_key="form_5500_part_i_1d_plan_sponsor_name",
            mapped_label="Sponsor", form_type=FormType.FORM_5500, proposed_value="New sponsor"),
            ExtractedField(id="bad", filing_id="f", source_field_name="NAIC",
            normalized_field_name="naic", mapped_rule_key="schedule_a_part_i_1c_naic_code",
            mapped_label="NAIC", form_type=FormType.SCHEDULE_A, proposed_value="1")]
        review = FTWilliamsReview(filing_id="f", ftw_customer_id="customer", ftw_plan_id="plan", year="2025",
            form_5500_current_values={"SDName": "Old sponsor"},
            fields=[FTWilliamsComparisonField(field_id="name", label="Sponsor", form_type=FormType.FORM_5500,
                changed=True, update_included=True, proposed_value="New sponsor"), FTWilliamsComparisonField(field_id="bad", label="NAIC",
                form_type=FormType.SCHEDULE_A, changed=True, validation_blocking=True)],
            schedule_a_broker_rows=[ScheduleABrokerRow(name="Unresolved broker")],
            schedule_a_broker_match_complete=False)
        FTWilliamsReviewService()._prepare_selected_update(review, fields, ["name"], include_broker_updates=False)
        self.assertIn("New sponsor", review.update_xml_5500)
        self.assertEqual(review.update_xml_schedule_a, "")
        self.assertFalse(review.fields[1].update_included)
        self.assertEqual(len(review.schedule_a_broker_rows), 1)
