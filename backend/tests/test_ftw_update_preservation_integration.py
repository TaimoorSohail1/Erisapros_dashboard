"""Exercise review decisions through dispatch and read-back, with no vendor I/O."""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.repositories as repositories
from app.api.filings import update_field
from app.config import get_settings
from app.models import (
    DocumentType, ExtractedField, FieldEditRequest, FieldPriority, Filing, FormType,
    FTWFieldDecision, FTWilliamsQueryResponse, FTWilliamsReviewStatus, FTWilliamsStatusItem,
)
from app.services.ftwilliams import FTWilliamsService
from app.services.ftwilliams_review import FTWilliamsReviewService, clear_ftw_current_snapshot_cache


class DetachedReviewRepository(repositories.MemoryRepository):
    """Model the independent objects returned by durable repository reads."""

    async def get_ftwilliams_review(self, filing_id):
        review = await super().get_ftwilliams_review(filing_id)
        return review.model_copy(deep=True) if review else None

    async def upsert_ftwilliams_review(self, review):
        stored = await super().upsert_ftwilliams_review(review.model_copy(deep=True))
        return stored.model_copy(deep=True)


class SavedValueVendor(FTWilliamsService):
    """A deterministic external-system boundary, not a mocked review pipeline."""

    def __init__(self):
        self.sent = []
        self.ignored_schedule_tags = set()
        self.parent = {
            'PlanName': 'Example Benefits Plan', 'SDEIN': '12-3456789', 'SponsDfePlanNum': '501',
            'PlanYearBeginDate': '01/01/2025', 'PlanYearEndDate': '12/31/2025',
            'LockedStatus': 'Unlocked', 'TotActivePartcpCnt': '100', 'SubtlActRtdSepCnt': '200',
        }
        self.schedules = {
            '1': {'ScheduleDesc': 'Sibling', 'InsCarrierName': 'Other Insurance', 'InsContractNum': 'OTHER-01'},
            '2': {
                'ScheduleDesc': 'Selected', 'InsCarrierName': 'Example Insurance Company',
                'InsCarrierEIN': '98-7654321', 'InsContractNum': 'POLICY-02',
                'InsPrsnCoveredEoyCnt': '10', 'WlfrTotChargesPaidAmt': '90',
                'PlanYearBeginDate': '01/01/2025', 'PlanYearEndDate': '12/31/2025',
            },
        }

    def status(self):
        return {'configured': True}

    async def run_query(self, payload):
        common = {'error_code': '0', 'ftw_customer_id': '11', 'ftw_plan_id': '22'}
        if payload.operation == 'query_plan':
            statuses = [FTWilliamsStatusItem(type='Plan', query_results={
                'PlanNumber': '501', 'PlanLine1': self.parent['PlanName'],
            }, **common)]
        elif payload.operation == 'query_5500':
            statuses = [FTWilliamsStatusItem(type='5500', query_results=deepcopy(self.parent), **common)]
        elif payload.operation == 'query_schedule_a':
            statuses = [FTWilliamsStatusItem(type='ScheduleA', ftw_seq_no=seq,
                query_results=deepcopy(values), **common) for seq, values in self.schedules.items()
                if not payload.ftw_seq_no or payload.ftw_seq_no == seq]
            if not statuses:
                statuses = [FTWilliamsStatusItem(type='DOLScheduleAData', error_code='59')]
        else:
            raise AssertionError(f'Unexpected vendor operation: {payload.operation}')
        return FTWilliamsQueryResponse(operation=payload.operation, configured=True, sent=True,
            request_xml='<query />', success=any(s.error_code == '0' for s in statuses),
            raw_response='<fake-vendor-response />', statuses=statuses)

    async def send_xml(self, operation, request_xml):
        self.sent.append((operation, request_xml))
        controls = {'TransactionType', 'EditCheck', 'FTWCustomerID', 'FTWPlanID', 'FTWSeqNo', 'Year', 'Type'}
        for node in ET.fromstring(request_xml).findall('./DataBatch/*'):
            values = {child.tag: child.text or '' for child in node if child.tag not in controls}
            if node.tag == 'DOL5500Data':
                self.parent.update(values)
            elif node.tag == 'DOLScheduleAData':
                seq = next(seq for seq, original in self.schedules.items()
                    if original['ScheduleDesc'] == values['ScheduleDesc'])
                for tag in self.ignored_schedule_tags:
                    if tag in self.schedules[seq]:
                        values[tag] = self.schedules[seq][tag]
                # Simulate FTWilliam's documented whole-dollar saved representation.
                if 'WlfrTotChargesPaidAmt' in values:
                    values['WlfrTotChargesPaidAmt'] = str(Decimal(values['WlfrTotChargesPaidAmt'])
                        .quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                self.schedules[seq] = values
            else:
                raise AssertionError(f'Unexpected write: {node.tag}')
        return FTWilliamsQueryResponse(operation=operation, configured=True, sent=True,
            request_xml='<masked-request />', success=True, http_status=200,
            raw_response='<ftwLinkResponse><Status><ErrorCode>0</ErrorCode></Status></ftwLinkResponse>',
            statuses=[FTWilliamsStatusItem(type=operation, error_code='0')])


class UpdatePreservationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        repositories._repository = DetachedReviewRepository()
        self.repo = repositories.get_repository()
        clear_ftw_current_snapshot_cache()
        self.settings_patch = patch.multiple(get_settings(), ftw_automation_enabled=False,
            ftwlink_schedule_a_updates_enabled=True, ftw_auto_edit_checks_enabled=False,
            ftw_pdf_audit_enabled=False, ftw_qa_guard_folder_ids_json='[]')
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.vendor = SavedValueVendor()
        self.vendor_patch = patch('app.services.ftwilliams_review.FTWilliamsService', return_value=self.vendor)
        self.vendor_patch.start()
        self.addCleanup(self.vendor_patch.stop)
        self.service = FTWilliamsReviewService(self.vendor)
        self.filing = await self.repo.create_filing(Filing(file_name='source.pdf',
            content_type='application/pdf', file_size=10, s3_key='source-v1',
            document_type=DocumentType.SCHEDULE_A))
        self.fields = {}
        for key, value, form in [
            ('form_5500_part_i_1e_plan_sponsor_ein', '12-3456789', FormType.FORM_5500),
            ('form_5500_part_i_1b_plan_number_pn', '501', FormType.FORM_5500),
            ('form_5500_part_i_7_plan_year_ending_date', '12/31/2025', FormType.FORM_5500),
            ('form_5500_part_ii_14_active_participants_at_end', '101', FormType.FORM_5500),
            ('form_5500_part_ii_12_total_participants_at_end_of_year', '201', FormType.FORM_5500),
            ('schedule_a_part_i_1a_name_of_insurance_company', 'Example Insurance Company', FormType.SCHEDULE_A),
            ('schedule_a_part_i_1b_insurance_carrier_ein', '98-7654321', FormType.SCHEDULE_A),
            ('schedule_a_part_i_1d_contract_policy_number', 'POLICY-02', FormType.SCHEDULE_A),
            ('schedule_a_part_i_1e_persons_covered_end_of_policy_year', '12', FormType.SCHEDULE_A),
            ('schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier', '100.49', FormType.SCHEDULE_A),
        ]:
            field = ExtractedField(filing_id=self.filing.id, source_field_name=key,
                normalized_field_name=key, mapped_rule_key=key, mapped_label=key,
                value=value, proposed_value=value, form_type=form, priority=FieldPriority.HIGH,
                source_document_type=DocumentType.PLAN_WORKSHEET if form == FormType.FORM_5500 else DocumentType.SCHEDULE_A)
            self.fields[key] = (await self.repo.add_fields([field]))[0]

    async def asyncTearDown(self):
        clear_ftw_current_snapshot_cache()
        repositories._repository = None

    async def test_keep_current_survives_full_update_of_other_fields_in_both_forms(self):
        before_sibling = deepcopy(self.vendor.schedules['1'])
        review = await self.service.prepare_review(self.filing.id, send_queries=True)
        self.assertEqual(review.ftw_seq_no, '2')
        for key, current in [
            ('form_5500_part_ii_14_active_participants_at_end', '100'),
            ('schedule_a_part_i_1e_persons_covered_end_of_policy_year', '10'),
        ]:
            result = await update_field(self.filing.id, self.fields[key].id,
                FieldEditRequest(proposed_value=current, decision='KEEP_CURRENT'))
            comparison = next(c for c in result['ftw_review'].fields if c.field_id == self.fields[key].id)
            self.assertEqual(comparison.decision, FTWFieldDecision.KEEP_CURRENT)
            preview = ET.fromstring(result['proposed_xml'])
            if current == '100':
                self.assertIsNone(preview.find('.//DOL5500Data/TotActivePartcpCnt'))
            else:
                selected = next(node for node in preview.findall('.//DOLScheduleAData')
                    if node.findtext('ScheduleDesc') == 'Selected')
                self.assertEqual(selected.findtext('InsPrsnCoveredEoyCnt'), '10')
        result = await self.service.approve_and_update(self.filing.id, send_to_ftw=True)
        self.assertEqual(result.status, FTWilliamsReviewStatus.UPDATE_SENT, result.error_message)
        self.assertEqual(self.vendor.parent['TotActivePartcpCnt'], '100')
        self.assertEqual(self.vendor.schedules['2']['InsPrsnCoveredEoyCnt'], '10')
        self.assertEqual(self.vendor.parent['SubtlActRtdSepCnt'], '201')
        self.assertEqual(self.vendor.schedules['2']['WlfrTotChargesPaidAmt'], '100')
        self.assertEqual(self.vendor.schedules['1'], before_sibling)
        self.assertEqual(result.update_attempted_count, 2)

    async def test_actual_saved_values_survive_reconciliation_storage_and_refresh(self):
        result = await self.service.approve_and_update(self.filing.id, send_to_ftw=True)
        self.assertEqual(result.status, FTWilliamsReviewStatus.UPDATE_SENT, result.error_message)
        self.assertTrue(result.update_verification_success)
        self.assertTrue(result.update_readback_observations)
        saved = {item['tag']: item for item in result.update_results}
        premium = saved['WlfrTotChargesPaidAmt']
        self.assertEqual(premium['sent_value'], '100.49')
        self.assertEqual(premium['returned_value'], '100')
        self.assertEqual(premium['returned_value_source'], 'VENDOR_READBACK')
        self.assertEqual(saved['TotActivePartcpCnt']['returned_value'], '101')
        stored = await self.repo.get_ftwilliams_review(self.filing.id)
        refreshed = await self.service.prepare_review(self.filing.id, send_queries=True)
        self.assertEqual(stored.update_readback_observations, result.update_readback_observations)
        self.assertEqual(refreshed.update_readback_observations, result.update_readback_observations)
        refreshed_premium = next(item for item in refreshed.update_results if item['tag'] == 'WlfrTotChargesPaidAmt')
        self.assertEqual(refreshed_premium['returned_value'], '100')

    async def test_explicit_edit_replaces_a_previous_keep_current_decision(self):
        await self.service.prepare_review(self.filing.id, send_queries=True)
        field = self.fields['form_5500_part_ii_14_active_participants_at_end']
        await update_field(self.filing.id, field.id, FieldEditRequest(proposed_value='100', decision='KEEP_CURRENT'))
        edited = await update_field(self.filing.id, field.id, FieldEditRequest(proposed_value='102'))
        self.assertIsNone(edited['field'].keep_current_context)
        result = await self.service.approve_and_update(self.filing.id, send_to_ftw=True)
        self.assertEqual(result.status, FTWilliamsReviewStatus.UPDATE_SENT, result.error_message)
        self.assertEqual(self.vendor.parent['TotActivePartcpCnt'], '102')

    async def test_failed_readback_retains_actual_value_and_next_attempt_replaces_it(self):
        self.vendor.ignored_schedule_tags.add('InsPrsnCoveredEoyCnt')
        with patch('app.services.ftwilliams_review.asyncio.sleep', AsyncMock()):
            failed = await self.service.approve_and_update(self.filing.id, send_to_ftw=True)
        self.assertEqual(failed.status, FTWilliamsReviewStatus.UPDATE_FAILED)
        self.assertFalse(failed.update_verification_success)
        rejected = next(item for item in failed.update_results if item['tag'] == 'InsPrsnCoveredEoyCnt')
        self.assertEqual(rejected['status'], 'NEEDS_CORRECTION')
        self.assertEqual(rejected['sent_value'], '12')
        self.assertEqual(rejected['returned_value'], '10')
        self.assertEqual(rejected['returned_value_source'], 'VENDOR_READBACK')
        self.vendor.ignored_schedule_tags.clear()
        succeeded = await self.service.approve_and_update(self.filing.id, send_to_ftw=True)
        self.assertEqual(succeeded.status, FTWilliamsReviewStatus.UPDATE_SENT, succeeded.error_message)
        saved = next(item for item in succeeded.update_results if item['tag'] == 'InsPrsnCoveredEoyCnt')
        self.assertEqual(saved['returned_value'], '12')
        self.assertNotEqual(failed.update_readback_observations, succeeded.update_readback_observations)
