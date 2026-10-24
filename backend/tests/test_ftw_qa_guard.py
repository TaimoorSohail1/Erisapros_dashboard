from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models import FTWilliamsReview
from app.services.ftw_qa_guard import QAWriteGuard
from app.services.ftwilliams import FTWilliamsService


class QAWriteGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.case = {'target': {'ftw_customer_id':'11','ftw_plan_id':'22','year':'2025','ftw_seq_no':'2'},
            'baseline_records': [
                {'ftw_seq_no':'1','query_results':{'ScheduleDesc':'Other','InsContractNum':'000111'},'query_subparts':{}},
                {'ftw_seq_no':'2','query_results':{'ScheduleDesc':'Selected','InsContractNum':'000222'},'query_subparts':{}}],
            'baseline_form_5500': {'PlanName':'Original'},
            'allowed_parent_values': {'TotActivePartcpCnt':['123','']}}
        self.run = {'run_id':'run','state':'ACTIVE','expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
        self.review = FTWilliamsReview(filing_id='filing',ftw_customer_id='11',ftw_plan_id='22',year='2025',ftw_seq_no='2')
        self.guard = QAWriteGuard(self.run, self.case, self.review)

    def document(self, content, *, kind='DOLScheduleAData', customer='11'):
        return f'<{kind}><TransactionType>{"2" if kind=="DOLScheduleAData" else "1"}</TransactionType><FTWCustomerID>{customer}</FTWCustomerID><FTWPlanID>22</FTWPlanID><Year>2025</Year>{content}</{kind}>'

    def replacement(self, *, sibling='000111', extra='', customer='11'):
        return '<ftwLink><DataBatch>' + self.document(f'<ScheduleDesc>Other</ScheduleDesc><InsContractNum>{sibling}</InsContractNum>',customer=customer) + self.document('<ScheduleDesc>Selected</ScheduleDesc><InsContractNum>000222</InsContractNum><WlfrTotChargesPaidAmt>100.49</WlfrTotChargesPaidAmt>') + extra + '</DataBatch></ftwLink>'

    async def test_valid_selected_update_preserves_sibling(self):
        self.guard.validate_xml(self.replacement())

    async def test_wrong_customer_plan_year_or_selected_sequence_rejected(self):
        with self.assertRaises(ValueError): self.guard.validate_xml(self.replacement(customer='99'))
        for attr, value in [('ftw_plan_id','99'),('year','2024'),('ftw_seq_no','1')]:
            with self.subTest(attr=attr):
                review = self.review.model_copy(update={attr:value})
                with self.assertRaises(ValueError): QAWriteGuard(self.run,self.case,review).validate_xml(self.replacement())

    async def test_identifier_digits_in_sibling_must_not_change(self):
        with self.assertRaises(ValueError): self.guard.validate_xml(self.replacement(sibling='111'))

    async def test_added_or_removed_sibling_rejected(self):
        with self.assertRaises(ValueError): self.guard.validate_xml(self.replacement(extra=self.document('<ScheduleDesc>Extra</ScheduleDesc>')))
        with self.assertRaises(ValueError): self.guard.validate_xml('<ftwLink><DataBatch>'+self.document('<ScheduleDesc>Selected</ScheduleDesc>')+'</DataBatch></ftwLink>')

    async def test_parent_changes_are_exactly_allowed_or_preserved(self):
        allowed = '<ftwLink><DataBatch>'+self.document('<PlanName>Original</PlanName><TotActivePartcpCnt>123</TotActivePartcpCnt>',kind='DOL5500Data')+'</DataBatch></ftwLink>'
        self.guard.validate_xml(allowed)
        for before, after in [('123','124'),('Original','Different')]:
            with self.subTest(after=after):
                with self.assertRaises(ValueError): self.guard.validate_xml(allowed.replace(before,after))

    async def test_closed_or_expired_run_rejects_delayed_write(self):
        for change in [{'state':'CLOSED'}, {'expires_at':'2020-01-01T00:00:00+00:00'}]:
            with self.assertRaises(ValueError): QAWriteGuard({**self.run,**change}, self.case,self.review).validate_xml(self.replacement())

    async def test_common_transport_enforces_guard_before_network(self):
        service = FTWilliamsService()
        service.qa_write_guard = AsyncMock(side_effect=ValueError('QA write denied'))
        service._get_client = AsyncMock()
        with self.assertRaises(ValueError): await service._post_xml('https://vendor.invalid',self.replacement())
        service._get_client.assert_not_awaited()

    async def test_sibling_broker_component_change_rejected(self):
        case = deepcopy(self.case)
        case['baseline_records'][0]['query_subparts'] = {'Broker':[{'Name01':'Broker','CommPdAmt01':'25.10'}]}
        xml = self.replacement().replace('<InsContractNum>000111</InsContractNum>',
            '<InsContractNum>000111</InsContractNum><DOLSubPartData><Broker><NameXX>Broker</NameXX><CommPdAmtXX>25.11</CommPdAmtXX></Broker></DOLSubPartData>')
        with self.assertRaises(ValueError): QAWriteGuard(self.run,case,self.review).validate_xml(xml)

    async def test_reordered_records_or_explicit_sequence_change_rejected(self):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(self.replacement())
        batch = root.find('DataBatch')
        first = batch[0]
        batch.remove(first)
        batch.append(first)
        with self.assertRaises(ValueError): self.guard.validate_xml(ET.tostring(root,encoding='unicode'))
        with self.assertRaises(ValueError): self.guard.validate_xml(self.replacement().replace('<ScheduleDesc>Selected</ScheduleDesc>', '<FTWSeqNo>9</FTWSeqNo><ScheduleDesc>Selected</ScheduleDesc>'))
