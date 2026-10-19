from app.models import NormalizedExtractionField, NormalizedExtractionResult
from app.services.schedule_a_source_review import apply_source_business_review


def test_letter_premium_balances_are_source_only_not_experience_section_answers():
    from app.services.schedule_a_source_observations import attach_schedule_a_source_observations
    source = [(1, '''INSURANCE DATA FOR SCHEDULE A (FORM 5500)
7. NON-PARTICIPATING CONTRACTS (PREMIUMS):
(A) TOTAL PREMIUM OR SUBSCRIPTION CHARGES PAID
TO CARRIER.................................$ 1234.56
(B) PREMIUMS DUE AND UNPAID AT END OF THE PLAN
YEAR.......................................$ 210.45
(C) IF THE CARRIER, SERVICE OR OTHER ORGANIZATION
INCURRED SPECIFIC COSTS IN CONNECTION WITH
THE ACQUISITION OR RETENTION OF THE CONTRACT
OR POLICY, OTHER THAN REPORTED IN NO. 5 ABOVE,
REPORT AMOUNT..............................$ .00
''')]
    result = NormalizedExtractionResult(provider='External', fields=[
        NormalizedExtractionField(field_name=label,value=value) for label,value in [
            ('10a. Total premiums or subscription charges paid to carrier','1234.56'),
            ('9a(2). Increase (decrease) in amount due but unpaid','210.45'),
            ('9c(1)(C). Other Specific acquisition costs','.00')]])
    attach_schedule_a_source_observations(result, source)
    apply_source_business_review(result, source)
    assert [f.value for f in result.fields] == ['1234.56']
    assert len(result.raw['source_only_field_candidates']) == 2
    assert {r['key']:r['value'] for r in result.raw['source_review']['reported_values']} == {
        'paid_premium':'1234.56', 'unpaid_premium':'210.45', 'additional_costs':'0.00'}


def test_letter_review_does_not_remove_an_explicit_experience_answer():
    result = NormalizedExtractionResult(provider='External',fields=[
        NormalizedExtractionField(field_name='9a(2). Increase (decrease) in amount due but unpaid',value='25.00')],
        raw={'source_review':{'reported_values':[{'key':'unpaid_premium','value':'25.00'}]}})
    apply_source_business_review(result, [(1,'NON-PARTICIPATING CONTRACTS (PREMIUMS)\n9 Experience-rated contracts\n9a(2). Increase (decrease) in amount due but unpaid: $25.00')])
    assert len(result.fields) == 1
    assert not result.raw.get('source_only_field_candidates')


def test_overlength_naic_preserves_the_source_and_does_not_approve_a_trimming_note():
    result = NormalizedExtractionResult(provider="Example", fields=[
        NormalizedExtractionField(field_name="1c. NAIC Code", value="61069", confidence=.99),
    ])
    apply_source_business_review(result, [(1, "NAIC CODE: 000-61069")])
    field = result.fields[0]
    assert field.value == "000-61069"
    assert field.decision == "REVIEW_REQUIRED"
    assert any(v.validator == "business_rule_confirmation" for v in field.validation_results)


def test_zero_count_is_retained_without_inventing_a_form_5500_replacement():
    result = NormalizedExtractionResult(provider="Example", fields=[
        NormalizedExtractionField(field_name="1e. Persons Covered (End of Policy Year)", value="0", confidence=.99),
        NormalizedExtractionField(field_name="10a. Total premiums or subscription charges paid to carrier", value="33126.32", confidence=.99),
    ])
    apply_source_business_review(result, [(1, "NUMBER OF PERSONS COVERED: 0\nTotal premiums $33,126.32")])
    assert result.fields[0].value == "0"
    assert result.fields[0].decision == "REVIEW_REQUIRED"


def test_explicit_unprovided_person_count_cannot_be_filled_from_a_street_number():
    result = NormalizedExtractionResult(provider="Example", fields=[
        NormalizedExtractionField(field_name="1e. Persons Covered (End of Policy Year)",value="165",confidence=.99),
    ])
    apply_source_business_review(result, [(1, """Approximate Number of Persons Insured: To be Provided by the Plan Administrator
AON CONSULTING INC
ONE LIBERTY PLAZA, 165 BROADWAY SUITE 3201
""")])
    assert not any(field.field_name.startswith('1e.') for field in result.fields)
    observation=result.raw['source_observations'][0]
    assert observation['literal']=='To be Provided by the Plan Administrator'
    assert observation['rejected_values']==['165']
