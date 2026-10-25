import pytest

from app.services.schedule_a_source_observations import collect_schedule_a_source_observations
from app.models import NormalizedExtractionResult, ScheduleABrokerRow
from app.services.schedule_a_source_observations import attach_schedule_a_source_observations


def test_declared_life_coverage_does_not_select_additional_accidental_death_from_a_note():
    result = collect_schedule_a_source_observations([(1, """1) COVERAGE - LIFE INSURANCE
When Life is checked, AD&D should also be checked on Form 5500.
7) BENEFIT TYPE: LIFE INSURANCE
""")])
    assert result["benefit_types"] == ["life"]
    assert result["benefit_observations"][0]["page"] == 1
    assert all("should" not in o["source_text"] for o in result["benefit_observations"])


def test_unmarked_options_are_not_selected_benefits():
    result = collect_schedule_a_source_observations([(2, "8. Benefit and contract type\n[ ] Health [X] Dental [ ] Vision [X] Life Insurance")])
    assert result["benefit_types"] == ["dental", "life"]


def test_paid_unpaid_and_zero_costs_remain_distinct_review_values():
    result = collect_schedule_a_source_observations([(1, """6. COVERAGE/BENEFITS PROVIDED: DBL
(A) TOTAL PREMIUM OR SUBSCRIPTION CHARGES PAID
TO CARRIER....................................$ 637,857.78
(B) PREMIUMS DUE AND UNPAID AT END OF THE PLAN
YEAR..........................................$ 28,600.17
(C) IF THE CARRIER, SERVICE OR OTHER ORGANIZATION
INCURRED SPECIFIC COSTS IN CONNECTION WITH
THE ACQUISITION OR RETENTION OF THE CONTRACT
OR POLICY, OTHER THAN REPORTED IN NO. 5 ABOVE,
REPORT AMOUNT.................................$ .00
""")])
    values = {v["key"]: v for v in result["reported_values"]}
    assert values["paid_premium"]["value"] == "637857.78"
    assert values["unpaid_premium"]["value"] == "28600.17"
    assert values["additional_costs"]["value"] == "0.00"
    assert values["additional_costs"]["value_kind"] == "PRINTED_ZERO"
    assert result["benefit_observations"][0]["literal"] == "DBL"
    assert result["benefit_observations"][0]["decision"] == "SOURCE_ONLY"
    assert result["benefit_types"] == ["temporary_disability"]


def test_premium_applied_table_keeps_each_coverage_count_without_summing_people():
    result = collect_schedule_a_source_observations([(2, """Premium was applied as follows during the Plan/Policy Year -
Policy Number Type of Benefit Premium Applied Approximate # of Lives Covered
001234G ADD-BAS $50.20 10
001234G LIFE-BTRM $10.80 4
Total $61.00
""")])
    assert [(r["coverage"], r["amount"], r["persons_covered"]) for r in result["premium_components"]] == [
        ("ADD-BAS", "50.20", "10"), ("LIFE-BTRM", "10.80", "4"),
    ]
    assert result["reconciliations"][0]["result"] == "MATCH"
    assert result["benefit_types"] == ["life", "other"]


def test_premium_table_ocr_cell_border_does_not_drop_a_complete_amount_and_count():
    pages = [(2, '''Premium was applied as follows during the Plan/Policy Year -
Policy Number Type of Benefit Premium Applied Approximate # of Lives Covered
001234G ADD-BAS $50.20) 10
001234G ADD-SUPP $12.30) 4
001234G LIFE-BTRM $10.80 4
Total $73.30
''')]
    result = collect_schedule_a_source_observations(pages)
    assert [row['amount'] for row in result['premium_components']] == ['50.20','12.30','10.80']
    assert result['reconciliations'][0]['result'] == 'MATCH'
    assert result['reconciliations'][0]['component_sum'] == '73.30'


def test_repeated_nonexperience_coverage_table_does_not_double_premium_components():
    table = """10 Nonexperience - rated contracts Coverage Amount
LIFE 83,501
Dental 54,821
Long Term Disability 30,085
AD & D 4,014
a Total premiums or subscription charges paid to carrier . 172,421
"""
    result = collect_schedule_a_source_observations([(5, table + table)])
    assert [(r["coverage"], r["amount"]) for r in result["premium_components"]] == [
        ("LIFE", "83501"), ("Dental", "54821"), ("Long Term Disability", "30085"), ("AD & D", "4014"),
    ]
    assert len(result["reconciliations"]) == 1
    assert result["reconciliations"][0]["component_sum"] == "172421"


def test_benefit_level_person_counts_are_kept_separate_not_added():
    result = collect_schedule_a_source_observations([(2, """Benefits & Contract Type Reporting Period
AD&D 422 01/01/2025 12/31/2025
Life 422 01/01/2025 12/31/2025
""")])
    assert result["benefit_types"] == ["life", "other"]
    assert [v["persons_covered"] for v in result["coverage_counts"]] == ["422", "422"]


@pytest.mark.parametrize("label", ["a", "a_", "a."])
def test_nonexperience_premium_components_accept_ocr_subsection_punctuation(label):
    text = f"""10 Nonexperience-rated contracts Coverage Amount
LIFE 11,203.45
Dental 7,801.02
Long Term Disability 901.03
AD&D 45.67
{label} Total premiums or subscription charges paid to carrier. 19,951.17
b Additional costs incurred 55.00
"""
    result = collect_schedule_a_source_observations([(5, text)])
    assert [(row["coverage"], row["amount"]) for row in result["premium_components"]] == [
        ("LIFE", "11203.45"), ("Dental", "7801.02"),
        ("Long Term Disability", "901.03"), ("AD&D", "45.67"),
    ]
    assert result["reconciliations"][0]["result"] == "MATCH"
    assert result["reconciliations"][0]["component_sum"] == "19951.17"


def test_premium_component_ocr_label_retains_total_mismatch_for_review():
    from app.models import NormalizedExtractionField

    result = NormalizedExtractionResult(provider="test", fields=[NormalizedExtractionField(
        field_name="10a. Total Premiums Paid", value="32.01", confidence=.99,
    )])
    attach_schedule_a_source_observations(result, [(5, """10 Nonexperience-rated contracts Coverage Amount
LIFE 12.01
Dental 20.01
a_ Total premiums or subscription charges paid to carrier. 32.01
""")])
    assert result.raw["source_review"]["reconciliations"][0]["result"] == "MISMATCH"
    assert result.fields[0].value == "32.01"
    assert result.fields[0].decision == "REVIEW_REQUIRED"
    assert any(item.validator == "source_component_total" and item.status == "ERROR"
               for item in result.fields[0].validation_results)


@pytest.mark.parametrize("total_label", ["", "b Additional costs incurred 32.02",
                                            "Total premiums or subscription charges paid to carrier. 32.02"])
def test_premium_components_require_the_labelled_paid_premium_subsection(total_label):
    result = collect_schedule_a_source_observations([(5, f"""10 Nonexperience-rated contracts Coverage Amount
LIFE 12.01
Dental 20.01
{total_label}
""")])
    assert result["premium_components"] == []
    assert result["reconciliations"] == []


def test_broker_coverage_components_and_wrapped_label_reconcile_without_sample_values():
    result = collect_schedule_a_source_observations([(4, """Name: DIFFERENT AGENT
Commissions Paid Fees Paid Organization
Coverage Amount Purpose Coverage Amount Purpose code
LIFE 10.01 Base Commissions 06
Dental 20.02 Base Commissions Field 1e
Long Term 30.03 Base Commissions
Disability
AD & D 40.04 Base Commissions
100.10 Sub - total 0 Sub - total
""")])
    assert [(c["coverage"], c["amount"]) for c in result["broker_components"]] == [
        ("LIFE", "10.01"), ("Dental", "20.02"), ("Long Term Disability", "30.03"), ("AD & D", "40.04"),
    ]
    assert all(c["broker_name"] == "DIFFERENT AGENT" for c in result["broker_components"])
    assert result["reconciliations"][0]["result"] == "MATCH"


def test_source_only_components_enrich_the_matching_broker_without_adding_destination_fields():
    result = NormalizedExtractionResult(provider="Example", fields=[], schedule_a_broker_rows=[
        ScheduleABrokerRow(name="DIFFERENT AGENT", commission_total="30.03", fee_total="0"),
    ])
    attach_schedule_a_source_observations(result, [(4, """Name: DIFFERENT AGENT
Commissions Paid Fees Paid Organization
Coverage Amount Purpose Coverage Amount Purpose code
LIFE 10.01 Base Commissions 06
Dental 20.02 Base Commissions
30.03 Sub - total 0 Sub - total
""")])
    assert result.fields == []
    assert result.schedule_a_broker_rows[0].commission_total == "30.03"
    assert [c.amount for c in result.schedule_a_broker_rows[0].commission_rows] == ["10.01", "20.02"]
    assert result.raw["source_review"]["mapping_mode"] == "EXTRACTION_ONLY_REVIEW"


def test_wrapped_disability_qualifier_is_preserved_in_reconciled_broker_components():
    result = NormalizedExtractionResult(provider="Example", fields=[], schedule_a_broker_rows=[
        ScheduleABrokerRow(name="EXAMPLE AGENCY", commission_total="35.75", fee_total="0"),
    ])
    attach_schedule_a_source_observations(result, [(4, """Name: EXAMPLE AGENCY
Commissions Paid Fees Paid Organization
Coverage Amount Purpose Coverage Amount Purpose code
Life 10.25 Base Commissions
Long Term
Disability 25.50 Base Commissions
35.75 Sub-total 0 Sub-total
""")])
    row = result.schedule_a_broker_rows[0]
    assert [(part.coverage, part.amount) for part in row.commission_rows] == [
        ("Life", "10.25"), ("Long Term Disability", "25.50"),
    ]
    assert row.commission_total == "35.75"
    assert any("Long Term\nDisability 25.50" in evidence.source_text for evidence in row.evidence)


def test_disability_qualifier_cannot_cross_a_blank_line():
    result = collect_schedule_a_source_observations([(4, """Name: EXAMPLE AGENCY
Commissions Paid Fees Paid Organization
Coverage Amount Purpose Coverage Amount Purpose code
Long Term

Disability 25.50 Base Commissions
25.50 Sub-total 0 Sub-total
""")])
    assert result["broker_components"][0]["coverage"] == "Disability"


def component_qualifier_case():
    text='''Name: EXAMPLE AGENCY
Commissions Paid Fees Paid Organization
Coverage Amount Purpose Coverage Amount Purpose code
Life 10.25 Base Commissions
Long Term —_

Disability 25.50 Base Commissions
35.75 Sub-total 0 Sub-total
'''
    provider_text=text.replace('Long Term —_\n\n','Long Term\n')
    raw={'ingest':{'ingest':{'processId':'CURRENT'}},
        'process':{'ingest':{'processId':'CURRENT','status':'complete','progress':{'complete':{
            'documents':[{'fileName':'example.pdf','sourceUrl':'https://example.test/current'}]}}}},
        'outputs':[{'fileName':'example.pdf','sourceUrl':'https://example.test/current',
            'chunks':[{'pageNumbers':[4],'text':provider_text}]}]}
    row=ScheduleABrokerRow(name='EXAMPLE AGENCY',commission_total='35.75',fee_total='0')
    result=NormalizedExtractionResult(provider='Example',fields=[],schedule_a_broker_rows=[row],raw=raw)
    return text, result


def test_broker_component_qualifier_can_use_a_unique_matching_bound_provider_source_row():
    text,result=component_qualifier_case()
    attach_schedule_a_source_observations(result,[(4,text)],source_sha256='actual-source',source_file_name='example.pdf')
    assert [(part.coverage,part.amount) for part in result.schedule_a_broker_rows[0].commission_rows]==[
        ('Life','10.25'),('Long Term Disability','25.50')]
    assert result.schedule_a_broker_rows[0].commission_total=='35.75' and result.fields==[]
    trace=result.raw['source_review']['component_qualifier_reconciliations'][0]
    assert trace['primary']['coverage']=='Disability' and trace['provider_source']['coverage']=='Long Term Disability'
    assert trace['source_sha256']=='actual-source' and trace['provider_source']['page']==4


@pytest.mark.parametrize('defect', ['wrong_file','wrong_process','wrong_url','other_page',
    'two_outputs','not_complete','structured_only','no_hash','different_broker',
    'different_amount','different_purpose','duplicate_provider_amount','duplicate_primary_amount'])
def test_component_qualifier_requires_unique_current_document_page_and_recipient_component(defect):
    text,result=component_qualifier_case()
    raw=result.raw
    if defect=='wrong_process':raw['process']['ingest']['processId']='OTHER'
    elif defect=='wrong_url':raw['outputs'][0]['sourceUrl']='https://example.test/other'
    elif defect=='other_page':raw['outputs'][0]['chunks'][0]['pageNumbers']=[5]
    elif defect=='two_outputs':raw['outputs'].append(raw['outputs'][0].copy())
    elif defect=='not_complete':raw['process']['ingest']['status']='processing'
    elif defect=='structured_only':
        raw.pop('outputs')
        raw['structured_outputs']=[{'coverage':'Long Term Disability'}]
    elif defect in {'different_broker','different_amount','different_purpose'}:
        before,after={'different_broker':('EXAMPLE AGENCY','OTHER AGENCY'),
            'different_amount':('25.50','25.51'),'different_purpose':('Base Commissions','Other Commissions')}[defect]
        raw['outputs'][0]['chunks'][0]['text']=raw['outputs'][0]['chunks'][0]['text'].replace(before,after)
    elif defect=='duplicate_provider_amount':
        raw['outputs'][0]['chunks'][0]['text']+='\nShort Term\nDisability 25.50 Base Commissions\n'
    elif defect=='duplicate_primary_amount':
        text=text.replace('Life 10.25','Life 25.50')
    attach_schedule_a_source_observations(result,[(4,text)],
        source_sha256=None if defect=='no_hash' else 'actual-source',
        source_file_name='other.pdf' if defect=='wrong_file' else 'example.pdf')
    assert not result.raw['source_review'].get('component_qualifier_reconciliations')
    assert result.raw['source_review']['broker_components'][1]['coverage']=='Disability'
    assert result.schedule_a_broker_rows[0].commission_total=='35.75'


def test_qualified_primary_component_is_not_overwritten_by_a_different_provider_qualifier():
    text,result=component_qualifier_case()
    text=text.replace('Long Term —_\n\n','Short Term\n')
    attach_schedule_a_source_observations(result,[(4,text)],source_sha256='actual-source',source_file_name='example.pdf')
    assert result.schedule_a_broker_rows[0].commission_rows[1].coverage=='Short Term Disability'
    assert not result.raw['source_review'].get('component_qualifier_reconciliations')


@pytest.mark.parametrize("prefix, expected", [
    ("Short Term\n", "Short Term Disability"),
    ("  Long\tTerm\r\n  ", "Long\tTerm Disability"),
    ("", "Disability"),
    ("Long Term\nCoverage Amount Purpose\n", "Disability"),
    ("Long Term\nName: ANOTHER AGENCY\n", "Disability"),
    ("Long Term 10.25 Base Commissions\n", "Disability"),
])
def test_wrapped_disability_qualifier_is_local_and_does_not_invent_a_term(prefix, expected):
    source = ("Name: EXAMPLE AGENCY\nCommissions Paid\nCoverage Amount Purpose\n"
              + prefix + "Disability 25.50 Base Commissions\n25.50 Sub-total\n")
    result = collect_schedule_a_source_observations([(4, source)])
    assert result["broker_components"][-1]["coverage"] == expected
    assert result["broker_components"][-1]["amount"] == "25.50"


def test_wrapped_disability_qualifier_cannot_cross_pages():
    result = collect_schedule_a_source_observations([
        (3, "Name: EXAMPLE AGENCY\nCommissions Paid\nCoverage Amount Purpose\nLong Term"),
        (4, "Name: EXAMPLE AGENCY\nCommissions Paid\nCoverage Amount Purpose\nDisability 25.50 Base Commissions\n"),
    ])
    assert result["broker_components"][0]["coverage"] == "Disability"


def test_ocr_table_borders_cannot_drop_a_wrapped_disability_payment_component():
    result = NormalizedExtractionResult(provider="Example", fields=[], schedule_a_broker_rows=[
        ScheduleABrokerRow(name="EXAMPLE AGENCY", commission_total="60.06", fee_total="0"),
    ])
    attach_schedule_a_source_observations(result, [(4, """Name: EXAMPLE AGENCY
Commissions Paid Fees Paid Organization
Coverage Amount |Purpose Coverage Amount _ | Purpose code
LIFE 10.01 | Base Commissions 06
Dental 20.02 | Base Commissions
Long Term 30.03} Base Commissions
Disability
60.06 | Sub-total 0 Sub-total
""")])
    assert [(r.coverage, r.amount) for r in result.schedule_a_broker_rows[0].commission_rows] == [
        ("LIFE", "10.01"), ("Dental", "20.02"), ("Long Term Disability", "30.03"),
    ]
    assert result.schedule_a_broker_rows[0].commission_total == "60.06"


def test_compact_native_text_can_separate_a_benefit_value_from_the_next_label():
    result = collect_schedule_a_source_observations([(1,
        "Insurance Carrier FEIN: 123456789Benefit Type: Vision CarePolicy or Contract Year: 04/01/2025 - 03/31/2026")])
    assert result["benefit_types"] == ["vision"]
    assert result["benefit_observations"][0]["literal"] == "Vision Care"


def test_producer_compensation_review_keeps_zero_columns_and_negative_adjustments():
    source = """Annual Statement of Premiums and Producer Compensation
Org Policy Commissions (1)Bonus
Producer and Address Fees Paid Compensation
Code Number Paid Paid
Paid
EXAMPLE AGENCY 3 001234-GL -$0.80 $0.00 $0.00 $0.00
LLC
42 EXAMPLE RD
BOSTON, MA 02110
Total -$0.80 $0.00 $0.00 $0.00
"""
    result = collect_schedule_a_source_observations([(3, source)])
    assert [(c["category"], c["amount"]) for c in result["broker_components"]] == [
        ("commission", "-0.80"), ("fee", "0.00"), ("bonus", "0.00"), ("additional_compensation", "0.00"),
    ]
    assert all(c["address_line_1"] == "42 EXAMPLE RD" for c in result["broker_components"])


def test_three_column_compensation_statement_keeps_additional_compensation_separate():
    result = collect_schedule_a_source_observations([(1, """INSURANCE FEES AND COMMISSION INFORMATION:
NAME AND ADDRESS OF SALES ADDITIONAL
EACH SOLICITING AGENT COMMISSION FEES COMPENSATION
OR BROKER RECEIVING PAID PAID PAID
DIFFERENT ADVISORY LLC 125.25 .00 10.00
42 EXAMPLE RD
BOSTON MA 02110
""")])
    assert [(c["category"], c["amount"]) for c in result["broker_components"]] == [
        ("commission", "125.25"), ("fee", "0.00"), ("additional_compensation", "10.00"),
    ]


def test_name_only_components_cannot_be_assigned_to_two_distinct_recipients():
    result = NormalizedExtractionResult(provider="Example", fields=[], schedule_a_broker_rows=[
        ScheduleABrokerRow(name="SAME AGENT", address_line_1="1 FIRST RD", commission_total="30.03"),
        ScheduleABrokerRow(name="SAME AGENT", address_line_1="2 SECOND RD", commission_total="30.03"),
    ])
    attach_schedule_a_source_observations(result, [(4, """Name: SAME AGENT
Commissions Paid Fees Paid Organization
Coverage Amount Purpose Coverage Amount Purpose code
LIFE 10.01 Base Commissions
Dental 20.02 Base Commissions
30.03 Sub - total 0 Sub - total
""")])
    assert all(not row.commission_rows for row in result.schedule_a_broker_rows)
    assert all(row.decision == "REVIEW_REQUIRED" for row in result.schedule_a_broker_rows)
    assert len(result.raw["source_review"]["broker_components"]) == 2


def test_conditional_missing_information_instruction_is_not_an_outbound_answer():
    result = NormalizedExtractionResult(provider='Example',fields=[])
    attach_schedule_a_source_observations(result,[(2, '''PART IV Provision of Information
11. Did the insurance company fail to provide any information necessary to complete Schedule A? No
12. If the answer to line 11 is "Yes", specify the information not provided. > Answer "Not Applicable"
''')])
    instructions = result.raw['source_review']['source_instructions']
    assert len(instructions) == 1
    assert instructions[0]['field_reference'] == '12'
    assert instructions[0]['instruction_value'] == 'Not Applicable'
    assert instructions[0]['page'] == 2
    assert instructions[0]['decision'] == 'SOURCE_ONLY_NOT_AN_OUTBOUND_ANSWER'
    assert result.fields == [], 'An instruction about a conditional field is not itself a populated answer.'


def test_reported_incentive_and_dated_premium_due_are_separate_from_paid_premium():
    result=collect_schedule_a_source_observations([(2,'''Part I, line 3c: Incentive compensation payments based upon persons/members in your plan and/or lump sum amount: $0 (Broker and General Agents combined)
Premium Due as of 07/08/2026 $0
''')])
    values={value['key']:value for value in result['reported_values']}
    assert values['incentive_compensation']['value']=='0'
    assert values['premium_due']['value']=='0'
    assert values['premium_due']['as_of']=='07/08/2026'
    assert 'paid_premium' not in values


def test_combined_payment_is_exposed_without_inventing_a_commission_fee_split():
    result=collect_schedule_a_source_observations([(1,'''Total Payments Made to Carrier: $2468.10
Insurance Fees and Commissions Paid to Agents and Brokers:
Commissions/FeesPaid for PolicyAgent or Broker or Contract Year
EXAMPLE BENEFITS AGENCY LLC $125.2542 RESEARCH RD
BOSTON MA 02110
''')])
    payments=[value for value in result['reported_values'] if value['key']=='combined_broker_payment']
    assert len(payments)==1 and payments[0]['value']=='125.25'
    assert payments[0]['decision']=='PENDING_CLASSIFICATION'
    assert payments[0]['value_kind']=='PRINTED_COMBINED_PAYMENT'
    assert result['broker_components']==[]


def test_a_single_coverage_row_can_supply_its_explicit_lives_count_without_a_maximum_rule():
    result=NormalizedExtractionResult(provider='Example',fields=[])
    attach_schedule_a_source_observations(result,[(2,'''Premium was applied as follows during the Plan/Policy Year -
Policy Number Type of Benefit Premium Applied Approximate # of Lives Covered
00987654G LIFE-BTRM $2468.10 53
Total $2468.10
''')])
    persons=next(field for field in result.fields if field.field_name.startswith('1e.'))
    assert persons.value=='53' and persons.page==2
    assert 'Lives Covered' in persons.source_text
    assert '00987654G' in persons.evidence[0].source_text


def test_selected_ocr_cross_marks_in_round_boxes_are_not_lost_or_confused_with_empty_boxes():
    result=collect_schedule_a_source_observations([(1,'8. Benefit and contract type\n[ ] Health (✗) Dental ( ) Vision (✘) Life Insurance')])
    assert result['benefit_types']==['dental','life']


def test_dbl_in_identifiers_or_unlabelled_footnotes_does_not_select_disability():
    result=collect_schedule_a_source_observations([(1, '''Contract number: DBL-00981234
DBL appears in a supporting footnote, not the declared benefit.
6. COVERAGE/BENEFITS PROVIDED: DBL-00981234
''')])
    assert result['benefit_types']==[]


def test_declared_benefit_rows_do_not_depend_on_correct_ocr_of_adjacent_dates():
    result=collect_schedule_a_source_observations([(2, '''Number of Persons on
Benefits & the Last Day of the Reporting Period
Contract Type Reporting Period From To
(Part III,#8) (e) (f) (g)
AD&D 53 Of #2024 12 731/2024
Life 53 01/01/2024 12 c31/2024
2. Insurance fee and commission information.
Dental 125.25 appears in a later compensation note, not the coverage table.
''')])
    assert result['benefit_types']==['life','other']
    assert [row['persons_covered'] for row in result['coverage_counts']]==['53','53']
    assert all(row['from'] is None and row['to'] is None for row in result['coverage_counts'])
    assert all('53' in row['source_text'] for row in result['benefit_observations'])


def test_spaced_benefit_codes_preserve_every_premium_and_coverage_count():
    result = collect_schedule_a_source_observations([(2, '''Premium was applied as follows during the Plan/Policy Year -
Policy Number Type of Benefit Premium Applied Approximate # of Lives Covered
00987654G LIFE - BTRM $ 1250.50 53
00987654G LTD - ABIL $ 250.25 31
Total $ 1500.75
''')])
    assert [(row['coverage'], row['amount'], row['persons_covered']) for row in result['premium_components']] == [
        ('LIFE - BTRM', '1250.50', '53'), ('LTD - ABIL', '250.25', '31')]
    assert result['reconciliations'][0]['result'] == 'MATCH'
    assert result['benefit_types'] == ['life', 'long_term_disability']
