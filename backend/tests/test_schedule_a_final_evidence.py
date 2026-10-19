"""Final carrier values must retain field-bound source evidence."""
import asyncio

from app.config import get_settings
from app.services.extractor import ExtractionService
from app.models import NormalizedExtractionResult, NormalizedExtractionField, ScheduleABrokerRow, ScheduleABrokerMoneyRow, SourceEvidence
from app.services.schedule_a_final_evidence import attach_final_field_evidence
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result


def test_authoritative_letter_parser_keeps_ein_source_after_semantic_enrichment(monkeypatch):
    for key in ('GROUNDX_API_KEY','EYELEVEL_API_KEY','EYELEVEL_EXTRACT_URL'):
        monkeypatch.setenv(key,'')
    monkeypatch.setenv('GROUNDX_BUCKET_ID','0')
    monkeypatch.setenv('SCHEDULE_A_CANONICAL_VALIDATION_ENABLED','true')
    monkeypatch.setenv('SCHEDULE_A_CANONICAL_VALIDATION_SHADOW_ENABLED','false')
    get_settings.cache_clear()
    source = '''INSURANCE DATA FOR SCHEDULE A (FORM 5500)
1. NAME OF CARRIER, SERVICE OR OTHER ORGANIZATION:
First Unum Life Insurance Company
TAX ID: 131898173 NAIC: 64297
2. CONTRACT NUMBER: 00987654
3. APPROXIMATE NUMBER OF PERSONS COVERED AT END OF POLICY YEAR: 27
4. DATE FOR PERIOD: FROM 01-01-2024 TO 01-01-2025
'''
    try:
        result=asyncio.run(ExtractionService().extract_schedule_a(source.encode(),'different.txt'))
        field=next(f for f in result.fields if f.field_name.startswith('1b.'))
        assert field.value=='13-1898173'
        assert any(v.validator=='source_evidence' and v.status=='PASS' for v in field.validation_results)
        assert any(e.page==1 and '131898173' in (e.source_text or '') for e in field.evidence)
    finally:
        get_settings.cache_clear()


def test_policy_period_evidence_does_not_use_a_correspondence_date():
    source='Letter date: 04/15/2024\nPolicy or Contract Year: 01/01/2024 - 12/31/2024'
    result=NormalizedExtractionResult(provider='External',fields=[
        NormalizedExtractionField(field_name='1f. Policy Year Beginning Date',value='01/01/2024',confidence=.99),
        NormalizedExtractionField(field_name='1g. Policy Year Ending Date',value='12/31/2024',confidence=.99)])
    attach_final_field_evidence(result,[(2,source)])
    resolve_schedule_a_result(result)
    assert all(f.decision=='AUTOMATIC' for f in result.fields)
    assert '04/15/2024' not in result.fields[0].source_text


def test_a_nearby_phone_number_cannot_prove_an_incorrect_ein():
    result=NormalizedExtractionResult(provider='External',fields=[
        NormalizedExtractionField(field_name='1b. Insurance Carrier EIN',value='13-1898173',confidence=.99)])
    attach_final_field_evidence(result,[(1,'TAX ID: 12-3456789\nBroker phone: 131898173')])
    resolve_schedule_a_result(result)
    assert result.fields[0].decision=='REVIEW_REQUIRED'
    assert any(v.validator=='source_evidence' and v.status=='ERROR' for v in result.fields[0].validation_results)


def test_identity_table_attaches_each_value_to_its_own_column():
    source='''(b) EIN (c) NAIC code (d) Contract or identification number
(e) Approximate number of persons covered at end of policy year
Policy or contract year (f) From (g) To
12-3456789 22222 XX00987 27 01/01/2024 12/31/2024
'''
    result=NormalizedExtractionResult(provider='External',fields=[
        NormalizedExtractionField(field_name=label,value=value,confidence=.99) for label,value in [
            ('1b. Insurance Carrier EIN','12-3456789'),('1c. NAIC Code','22222'),
            ('1d. Contract/Policy Number','XX00987'),('1e. Persons Covered (End of Policy Year)','27'),
            ('1f. Policy Year Beginning Date','01/01/2024'),('1g. Policy Year Ending Date','12/31/2024')]])
    attach_final_field_evidence(result,[(2,source)])
    resolve_schedule_a_result(result)
    assert all(f.decision=='AUTOMATIC' for f in result.fields)
    assert all(any(e.page==2 for e in f.evidence) for f in result.fields if f.field_name.startswith('1'))


def test_contract_digits_cannot_prove_the_naic_column():
    source='EIN NAIC Code Contract or identification number\n12-3456789 22222 11111 27 01/01/2024 12/31/2024'
    result=NormalizedExtractionResult(provider='External',fields=[
        NormalizedExtractionField(field_name='1c. NAIC Code',value='11111',confidence=.99)])
    attach_final_field_evidence(result,[(2,source)])
    resolve_schedule_a_result(result)
    assert result.fields[0].decision=='REVIEW_REQUIRED'


def test_printed_carrier_is_not_in_conflict_with_a_certification_sentence():
    result=NormalizedExtractionResult(provider='External',fields=[
        NormalizedExtractionField(field_name='1a. Name of Insurance Company',value='Example Life Insurance Company',confidence=.99,
            candidate_values=['Example Life Insurance Company','HEREBY CERTIFIES THAT THIS INFORMATION IS COMPLETE AND ACCURATE'])])
    attach_final_field_evidence(result,[(1,'Name of insurance carrier: Example Life Insurance Company')])
    resolve_schedule_a_result(result)
    assert result.fields[0].decision=='AUTOMATIC'
    assert len(result.raw['rejected_source_candidates'])==1


def test_two_actual_carrier_names_still_require_review():
    result=NormalizedExtractionResult(provider='External',fields=[
        NormalizedExtractionField(field_name='1a. Name of Insurance Company',value='Example Life Insurance Company',confidence=.99,
            candidate_values=['Example Life Insurance Company','Another Life Insurance Company'])])
    attach_final_field_evidence(result,[(1,'Name of insurance carrier: Example Life Insurance Company')])
    resolve_schedule_a_result(result)
    assert result.fields[0].decision=='REVIEW_REQUIRED'


def test_commission_sum_has_recipient_and_calculation_evidence_not_a_fabricated_printed_total():
    rows=[ScheduleABrokerRow(name='Example Agency',address_line_1=street,commission_total=amount,fee_total='0.00',
          source_page=2,decision='AUTOMATIC',confidence=.99,
          evidence=[SourceEvidence(provider='Source table',page=2,source_text=f'Example Agency {street}\nCommissions paid ${amount}; Fees paid $0.00')])
          for street,amount in [('42 EXAMPLE RD','10.25'),('PO BOX 27','20.50')]]
    result=NormalizedExtractionResult(provider='External',schedule_a_broker_rows=rows,fields=[
        NormalizedExtractionField(field_name='3b. Amount of Commissions',value='30.75',confidence=.99)])
    attach_final_field_evidence(result,[])
    resolve_schedule_a_result(result)
    field=next(f for f in result.fields if f.field_name.startswith('3b.'))
    assert any(v.validator=='source_evidence' and v.status=='PASS' for v in field.validation_results)
    assert any(e.provider=='Reconciled recipient calculation' and '10.25 + 20.50' in e.source_text for e in field.evidence)


def test_paid_premium_evidence_does_not_take_the_unpaid_balance():
    source='''7. NON-PARTICIPATING CONTRACTS (PREMIUMS)
(A) TOTAL PREMIUM OR SUBSCRIPTION CHARGES PAID
TO CARRIER.............................$ 1250.50
(B) PREMIUMS DUE AND UNPAID AT END OF YEAR $ 210.45
'''
    result=NormalizedExtractionResult(provider='External',fields=[
        NormalizedExtractionField(field_name='10a. Total premiums or subscription charges paid to carrier',value='1250.50',confidence=.99)])
    attach_final_field_evidence(result,[(1,source)])
    resolve_schedule_a_result(result)
    assert all(v.status=='PASS' for v in result.fields[0].validation_results)
    assert '210.45' not in result.fields[0].source_text


def test_reconciled_components_keep_evidence_when_table_header_is_on_a_previous_line():
    row=ScheduleABrokerRow(name='Example Agency',commission_total='0.00',fee_total='6.72',
        source_page=3,decision='AUTOMATIC',confidence=.99,
        fee_rows=[ScheduleABrokerMoneyRow(amount=amount,purpose='BONUS PAID') for amount in ['-5.57','1.94','10.35']],
        evidence=[SourceEvidence(provider='Source table',page=3,source_text='Name: Example Agency\n- $ 5.57\n$ 1.94\n10.35 $')])
    result=NormalizedExtractionResult(provider='External',schedule_a_broker_rows=[row],fields=[
        NormalizedExtractionField(field_name='3c. Amount of Fees',value='6.72',confidence=.99)])
    attach_final_field_evidence(result,[])
    resolve_schedule_a_result(result)
    assert any(e.provider=='Reconciled recipient calculation' for e in result.fields[0].evidence)


def test_unparseable_component_must_not_be_certified_or_crash():
    row=ScheduleABrokerRow(name='Example Agency',commission_total='10.00',fee_total='0.00',
        decision='AUTOMATIC',commission_rows=[ScheduleABrokerMoneyRow(amount='invalid')],
        evidence=[SourceEvidence(provider='Source table',page=1,source_text='Example Agency commission $10.00')])
    result=NormalizedExtractionResult(provider='External',schedule_a_broker_rows=[row],fields=[
        NormalizedExtractionField(field_name='3b. Amount of Commissions',value='10.00',confidence=.99)])
    attach_final_field_evidence(result,[])
    assert not result.fields[0].evidence


def test_unum_inclusive_date_keeps_literal_date_and_approved_calculation():
    from app.services.extractor import extract_first_unum_schedule_a_fields
    source='''INSURANCE DATA FOR SCHEDULE A
First Unum Life Insurance Company
DATE FOR PERIOD: FROM 01-01-2024 TO 01-01-2025'''
    result=NormalizedExtractionResult(provider='External',fields=extract_first_unum_schedule_a_fields([(2,source)]))
    attach_final_field_evidence(result,[(2,source)])
    resolve_schedule_a_result(result)
    end=next(f for f in result.fields if f.field_name.startswith('1g.'))
    assert end.value=='12/31/2024'
    assert any(e.page==2 and '01-01-2025' in e.source_text and 'inclusive' in e.source_text for e in end.evidence)
    assert any(v.validator=='source_evidence' and v.status=='PASS' for v in end.validation_results)


def test_org_code_binds_the_named_table_column_not_an_address_number():
    source='''Name: Example Agency
Address: 03 EXAMPLE RD
Commissions Paid Fees Paid Organization
Coverage Amount Purpose Coverage Amount Purpose code
LIFE 10.25 Base Commissions 03'''
    row=ScheduleABrokerRow(name='Example Agency',organization_code='03',commission_total='10.25',fee_total='0.00',
        evidence=[SourceEvidence(provider='Source table',page=2,source_text=source)])
    result=NormalizedExtractionResult(provider='External',schedule_a_broker_rows=[row],fields=[
        NormalizedExtractionField(field_name='3e. Organizational Code',value='03',confidence=.99)])
    attach_final_field_evidence(result,[(2,source)])
    assert any(e.provider=='Printed recipient organization code' for e in result.fields[0].evidence)
    bad=NormalizedExtractionResult(provider='External',schedule_a_broker_rows=[row.model_copy(update={'organization_code':'04'})],fields=[
        NormalizedExtractionField(field_name='3e. Organizational Code',value='04',confidence=.99)])
    attach_final_field_evidence(bad,[(2,source.replace('03 EXAMPLE','04 EXAMPLE'))])
    assert not bad.fields[0].evidence


def test_benefit_count_table_evidence_never_adds_or_takes_maximum_of_counts():
    source='''Number of Persons on
Benefits & the Last Day of the Reporting Period
Contract Type Reporting Period From To
(Part III, #8) (e) (f) (g)
AD & D 27 01/01/2024 12/31/2024
Life 27 01/01/2024 12/31/2024
2. Insurance fee and commission information'''
    for value,printed,has_evidence in [('27',source,True),('54',source,False),('27',source.replace('Life 27','Life 14'),False)]:
        result=NormalizedExtractionResult(provider='External',fields=[
            NormalizedExtractionField(field_name='1e. Persons Covered (End of Policy Year)',value=value,confidence=.99)])
        attach_final_field_evidence(result,[(1,printed)])
        assert bool(result.fields[0].evidence)==has_evidence


def test_premium_components_support_only_their_reconciled_total_not_classification():
    from app.services.schedule_a_source_observations import attach_schedule_a_source_observations
    source='''Premium was applied as follows
Policy Number Type of Benefit Premium Applied Approximate # of Lives Covered
TEST123 LIFE - BAS $50.20) 27
TEST123 ADD - BAS $12.30) 27
Total $62.50'''
    result=NormalizedExtractionResult(provider='External',fields=[
        NormalizedExtractionField(field_name='10a. Total premiums or subscription charges paid to carrier',value='62.50',confidence=.99)])
    attach_schedule_a_source_observations(result,[(2,source)])
    attach_final_field_evidence(result,[(2,source)])
    resolve_schedule_a_result(result)
    premium=next(f for f in result.fields if f.field_name.startswith('10a.'))
    assert any(v.validator=='source_evidence' and v.status=='PASS' for v in premium.validation_results)
    assert any(v.validator=='section_context' and v.status=='ERROR' for v in premium.validation_results)
