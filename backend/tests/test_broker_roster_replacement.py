"""Broker-list replacement and source-section regressions."""
import unittest
from app.models import NormalizedExtractionResult, ScheduleABrokerRow, NormalizedExtractionField, SourceEvidence, FTWilliamsReview, ScheduleABrokerMatch
from app.services.extractor import reconcile_aetna_part_i_compensation
from app.services.schedule_a_broker_roster import attest_source_broker_roster
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.schedule_a_customer_rules import apply_customer_defaults, canonical_broker_payment_purpose
from app.services.xml_builder import build_schedule_a_records_update_xml
from app.services.schedule_a_broker_roster import separate_service_provider_disclosures, source_roster_review, bind_roster_review
import xml.etree.ElementTree as ET


class BrokerRosterReplacementTests(unittest.TestCase):
    def test_same_identity_with_unknown_amount_remains_reviewable_without_sort_failure(self):
        rows=[ScheduleABrokerRow(name='Example',commission_total=value,fee_total='0') for value in ('100',None)]
        self.assertEqual(source_roster_review(rows).mode,'PRESERVE')

    def test_repeated_verified_roster_is_noop_without_positional_matches(self):
        result=self.verified()
        service=FTWilliamsReviewService()
        records=[{'ftw_seq_no':'1','query_results':{'ScheduleDesc':'QA'}}]
        sent=build_schedule_a_records_update_xml(records,'1',[],year='2025',schedule_a_broker_rows=result.schedule_a_broker_rows,replace_broker_roster=True)
        document=service._update_documents(sent,'DOLScheduleAData')[0]
        current={'ftw_seq_no':'1','query_results':{k:v for k,v in document.items() if not k.startswith('__')},'query_subparts':document['__subparts__']}
        review=FTWilliamsReview(filing_id='qa',ftw_seq_no='1',year='2025',schedule_a_records=[current],schedule_a_broker_rows=result.schedule_a_broker_rows)
        self.assertFalse(service._review_has_broker_updates(review))
        current['query_subparts']['Broker'][0]['FeesPdTextXX']='Incorrect purpose'
        self.assertTrue(service._review_has_broker_updates(review))

    def verified(self, count=1, commission='100', fee='0'):
        rows=[ScheduleABrokerRow(name=f'Example Advisory {i}',address_line_1=f'{i+1} Main St',city='Boston',
            state='MA',zip_code='02110',organization_code='3',commission_total=commission,fee_total=fee,
            purpose=canonical_broker_payment_purpose(commission,fee),source_page=i+1,confidence=.99,
            evidence=[SourceEvidence(page=i+1,source_text=f'Broker {i} COMMISSIONS {commission} FEES {fee}')]) for i in range(count)]
        from decimal import Decimal
        result=NormalizedExtractionResult(provider='source',fields=[
            NormalizedExtractionField(field_name='3b. Commissions',value=str(Decimal(commission)*count)),
            NormalizedExtractionField(field_name='3c. Fees',value=str(Decimal(fee)*count))],schedule_a_broker_rows=rows,
            raw={'extraction_stages':[{'stage':'structured_provider_normalized','broker_rows':[r.model_dump(mode='json') for r in rows]}]})
        attest_source_broker_roster(result,[r.model_copy(deep=True) for r in rows],'a'*64)
        return result

    def test_verified_zero_recipients_remain_in_preview_but_prove_empty_replacement(self):
        result=self.verified(commission='0',fee='0')
        status=source_roster_review(result.schedule_a_broker_rows)
        self.assertEqual((status.state,status.replacement_count),('VERIFIED_EMPTY',0))
        matches,roster=FTWilliamsReviewService()._resolve_schedule_a_brokers(result.schedule_a_broker_rows,
            [{'ftw_seq_no':'1','query_results':{'ScheduleDesc':'QA'}}],'1',[],create_new=False)
        self.assertTrue(all(m.resolved for m in matches))
        self.assertEqual(roster,[])

    def test_target_binding_changes_with_broker_data_and_destination(self):
        result=self.verified()
        record={'ftw_seq_no':'1','query_results':{'ScheduleDesc':'QA'},'query_subparts':{'Broker':[{'NameXX':'Old broker'}]}}
        first=bind_roster_review(result.schedule_a_broker_rows,None,[record],{'ftw_customer_id':'1','ftw_plan_id':'2','year':'2025','ftw_seq_no':'1'},True)
        changed=bind_roster_review(result.schedule_a_broker_rows,None,[record],{'ftw_customer_id':'1','ftw_plan_id':'3','year':'2025','ftw_seq_no':'1'},True)
        self.assertNotEqual(first.current_snapshot_sha256,changed.current_snapshot_sha256)
        record['query_subparts']['Broker'][0]['CommPdAmtXX']='5'
        changed=bind_roster_review(result.schedule_a_broker_rows,None,[record],{'ftw_customer_id':'1','ftw_plan_id':'2','year':'2025','ftw_seq_no':'1'},True)
        self.assertNotEqual(first.current_snapshot_sha256,changed.current_snapshot_sha256)
        result.schedule_a_broker_rows[0].commission_total='101'
        self.assertEqual(source_roster_review(result.schedule_a_broker_rows).mode,'PRESERVE')

    def test_more_than_seven_recipients_need_zero_row_assignments(self):
        result=self.verified(count=9)
        records=[{'ftw_seq_no':'1','query_results':{'ScheduleDesc':'QA'}}]
        service=FTWilliamsReviewService()
        matches,rows=service._resolve_schedule_a_brokers(result.schedule_a_broker_rows,records,'1',[],create_new=False)
        self.assertTrue(all(m.resolved and m.ftw_index is None for m in matches))
        xml=build_schedule_a_records_update_xml(records,'1',[],year='2025',schedule_a_broker_rows=rows,replace_broker_roster=True)
        self.assertEqual(len(ET.fromstring(xml).findall('.//Broker')),9)
        review=FTWilliamsReview(filing_id='qa',schedule_a_broker_rows=result.schedule_a_broker_rows,
            schedule_a_broker_roster=source_roster_review(result.schedule_a_broker_rows),schedule_a_broker_matches=matches)
        self.assertEqual(service._review_decision_count_updates(review)['decision_required_count'],0)

    def test_legacy_row_confirmation_cannot_authorize_unverified_roster(self):
        review=FTWilliamsReview(filing_id='qa',schedule_a_broker_rows=[ScheduleABrokerRow(name='Example',commission_total='100',fee_total='0')],
            schedule_a_broker_matches=[ScheduleABrokerMatch(extracted_index=0,ftw_index=0,status='CONFIRMED',resolved=True)])
        self.assertIn('completeness/payment review',FTWilliamsReviewService()._review_validation_blocking_error(review,action='sending'))

    def test_replacement_does_not_reintroduce_flattened_old_brokers(self):
        records=[{'ftw_seq_no':'1','query_results':{'ScheduleDesc':'QA','Name1':'Old Broker','CommPdAmt1':'900'},
                  'query_subparts':{'Broker':[{'NameXX':'Old Broker','CommPdAmtXX':'900'}]}}]
        xml=build_schedule_a_records_update_xml(records,'1',[],year='2025',replace_broker_roster=True,
            schedule_a_broker_rows=[ScheduleABrokerRow(name='Current Broker',commission_total='100',fee_total='0',purpose='COMMISSIONS')])
        self.assertEqual(ET.fromstring(xml).findtext('.//Broker/NameXX'),'CURRENT BROKER')
        self.assertEqual(ET.fromstring(xml).findtext('.//Broker/CommPdAmtXX'),'100')

    def test_same_name_in_service_disclosure_does_not_remove_schedule_a_recipient(self):
        result=NormalizedExtractionResult(provider='test',fields=[],schedule_a_broker_rows=[
            ScheduleABrokerRow(name='Example Advisory',source_page=None,commission_total='100',fee_total='0')])
        separate_service_provider_disclosures(result,[(5,'Schedule C Service Provider Information\nBroker Information: Example Advisory\n10 Main St\n$240,148.74')])
        self.assertEqual(len(result.schedule_a_broker_rows),1)
        self.assertEqual(result.schedule_a_broker_roster.state,'NEEDS_REVIEW')

    def test_legacy_matches_cannot_verify_incomplete_or_empty_source(self):
        self.assertEqual(source_roster_review([]).state,'NEEDS_REVIEW')
        self.assertEqual(source_roster_review([ScheduleABrokerRow(name='Example',commission_total='0',fee_total='0')]).state,'NEEDS_REVIEW')

    def test_unclassified_payment_is_not_assumed_to_be_a_fee(self):
        result = NormalizedExtractionResult(provider="source", fields=[], schedule_a_broker_rows=[
            ScheduleABrokerRow(name="Example Advisory", commission_total="100", commission_source_text="Direct compensation paid by plan $100")])
        apply_customer_defaults(result)
        row=result.schedule_a_broker_rows[0]
        self.assertEqual(row.decision, "REVIEW_REQUIRED")
        self.assertTrue(any(v.validator == "payment_classification_ambiguity" for v in row.validation_results))
        self.assertNotEqual(row.fee_total, "100")

    def test_negative_confirmed_bonus_is_fee_and_combined_net_zero_keeps_both_categories(self):
        result=NormalizedExtractionResult(provider="source",fields=[],schedule_a_broker_rows=[
            ScheduleABrokerRow(name="Example Advisory",commission_total="-557",fee_total="20",purpose="Broker Bonus")])
        apply_customer_defaults(result)
        row=result.schedule_a_broker_rows[0]
        self.assertEqual(row.commission_total,"0")
        self.assertEqual(row.fee_total,"-537")
        self.assertEqual(canonical_broker_payment_purpose("100", "-100"), "COMMISSIONS AND FEES")

    def test_verified_complete_list_replaces_without_matching_old_recipient_addresses(self):
        row = ScheduleABrokerRow(name="Example Advisory LLC", address_line_1="42 Main St", city="Boston",
            state="MA", zip_code="02110", organization_code="3", commission_total="100", fee_total="0",
            source_page=1, confidence=.99, evidence=[SourceEvidence(page=1, source_text="Example Advisory LLC 100 0")])
        result = NormalizedExtractionResult(provider="test", fields=[
            NormalizedExtractionField(field_name="3b. Commissions", value="100"),
            NormalizedExtractionField(field_name="3c. Fees", value="0")], schedule_a_broker_rows=[row],
            raw={"extraction_stages":[{"stage":"structured_provider_normalized","broker_rows":[row.model_dump(mode="json")]}]})
        attest_source_broker_roster(result, [row.model_copy(deep=True)], "a"*64)
        records=[{"ftw_seq_no":"1","query_results":{},"query_subparts":{"Broker":[{
            "NameXX":row.name,"AddressLine1XX":"99 OLD ST","CityXX":"Dallas","StateXX":"TX","ZipCodeXX":"75001"}]}}]
        matches, replacement = FTWilliamsReviewService()._resolve_schedule_a_brokers([row], records, "1", [], create_new=False)
        self.assertTrue(all(m.resolved for m in matches))
        self.assertTrue(all(m.ftw_index is None for m in matches))
        self.assertEqual([r.address_line_1 for r in replacement], ["42 Main St"])

    def test_blank_aetna_schedule_a_keeps_schedule_c_payment_as_separate_disclosure(self):
        result = NormalizedExtractionResult(provider="GroundX", fields=[], schedule_a_broker_rows=[
            ScheduleABrokerRow(name="Example Advisory LLC", address_line_1="42 Main St",
                commission_total="240148.74", fee_total="0", source_page=5)])
        pages = [(3, """AETNA LIFE INSURANCE COMPANY
Part I Information Concerning Insurance Contract Coverage, Fees, and Commissions
2. Insurance fees and commissions paid to agents and brokers
Amount of commissions paid Fees Paid
TOTAL
Part III Welfare Benefit Contract Information"""), (5, """The following information is intended for use in completing Schedule C of Form 5500
Part I - Service Provider Information
Enter direct compensation paid by the plan
Broker Information: Example Advisory LLC
42 Main St Boston, MA 02110
$240,148.74 Yes No""")]
        reconcile_aetna_part_i_compensation(result, pages)
        self.assertEqual(result.schedule_a_broker_rows, [])
        disclosure = result.schedule_a_broker_roster.other_disclosures[0]
        self.assertEqual(disclosure["amount"], "240148.74")
        self.assertEqual(disclosure["payment_category"], "UNCLASSIFIED_DIRECT_COMPENSATION")
        self.assertEqual(result.schedule_a_broker_roster.state, "NEEDS_REVIEW")


class BrokerEditInvalidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_excluding_last_preview_row_keeps_roster_review_and_disclosure_evidence(self):
        from app.models import ScheduleABrokerRosterReview, FTWilliamsScheduleABrokerRowsRequest
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        previous=ScheduleABrokerRosterReview(other_disclosures=[{'name':'Separate provider','amount':'240148.74'}])
        filing=SimpleNamespace(schedule_a_broker_rows=[ScheduleABrokerRow(name='Source broker')],schedule_a_broker_roster=previous)
        review=FTWilliamsReview(filing_id='qa')
        repo=SimpleNamespace(get_filing=AsyncMock(return_value=filing),update_filing=AsyncMock(),
            get_ftwilliams_review=AsyncMock(return_value=review),upsert_ftwilliams_review=AsyncMock(),add_audit=AsyncMock())
        service=FTWilliamsReviewService()
        with patch('app.services.ftwilliams_review.get_repository',return_value=repo), patch.object(service,'prepare_review',AsyncMock(return_value=review)):
            await service.update_schedule_a_broker_rows('qa',FTWilliamsScheduleABrokerRowsRequest(rows=[],action='excluded'))
        status=repo.update_filing.call_args.args[1]['schedule_a_broker_roster']
        self.assertIsNotNone(status,'An edited empty preview must not become an unreviewed legacy field-only extraction')
        status=ScheduleABrokerRosterReview.model_validate(status)
        self.assertEqual(status.mode,'PRESERVE')
        self.assertEqual(status.other_disclosures,previous.other_disclosures)


if __name__ == "__main__": unittest.main()
