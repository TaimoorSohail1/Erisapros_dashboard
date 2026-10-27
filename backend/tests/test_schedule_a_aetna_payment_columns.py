import pytest
from app.services.extractor import extract_litera_aetna_schedule_a_broker_rows


@pytest.mark.parametrize('heading', ['Part III', 'Part Ill', 'Part 111'])
def test_blank_compensation_table_cannot_read_the_following_premium(heading):
    from app.services.extractor import extract_schedule_a_fields_from_tables
    fields = extract_schedule_a_fields_from_tables(f'''Aetna Life Insurance Co.
2. Insurance Fees and commissions paid to agents and brokers:
(b) Amount of commissions paid (c) & (d) Fees Paid
Amount Purpose
00987654-Medical
TOTAL
{heading} Welfare Benefit Contract Information
9. Non experience rated contracts:
(a) Total premiums or subscription charges paid to carrier $1250.50
''', 3)
    assert not any(field.field_name.startswith('3b.') for field in fields)


def test_blank_part_i_does_not_accept_separate_disclosure_as_commission_or_zero_fees():
    from app.models import NormalizedExtractionField, NormalizedExtractionResult, ScheduleABrokerRow
    from app.services.extractor import reconcile_aetna_part_i_compensation
    from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result
    result = NormalizedExtractionResult(provider='External', fields=[
        NormalizedExtractionField(field_name='3b. Amount of Commissions', value='240.12', confidence=.99),
        NormalizedExtractionField(field_name='3c. Amount of Fees', value='0', confidence=.99),
    ], schedule_a_broker_rows=[ScheduleABrokerRow(name='EXAMPLE AGENCY', commission_total='240.12', fee_total='0')],
       raw={'provider_snapshot': {'commission': '240.12'}})
    reconcile_aetna_part_i_compensation(result, [(3, '''Aetna Life Insurance Co.
2. Insurance Fees and commissions paid to agents and brokers:
(b) Amount of commissions paid (c) & (d) Fees Paid
Amount Purpose
00987654-Medical
TOTAL
Part Ill Welfare Benefit Contract Information
Total premiums or subscription charges paid to carrier $1250.50
'''), (5, 'Service provider disclosure Direct Compensation $240.12')])
    resolve_schedule_a_result(result)
    payments = [field for field in result.fields if field.field_name.startswith(('3b.', '3c.'))]
    assert len(payments) == 2
    assert all(field.value == '' and field.decision == 'REVIEW_REQUIRED' for field in payments)
    assert result.schedule_a_broker_rows[0].commission_total is None
    assert result.schedule_a_broker_rows[0].fee_total is None
    assert result.schedule_a_broker_rows[0].decision == 'REVIEW_REQUIRED'
    assert result.raw['provider_snapshot'] == {'commission': '240.12'}
    assert result.raw['aetna_unprovided_part_i_compensation']['prior_broker_rows'][0]['commission_total'] == '240.12'


@pytest.mark.parametrize("fee_text,expected_fee", [("", None), (" $0.00", "0.00"), (" $25.50 Service Fee", "25.50")])
def test_aetna_reads_payment_columns_without_turning_blank_fees_into_zero(fee_text, expected_fee):
    pages = [(3, f"""AETNA LIFE INSURANCE COMPANY AND AFFILIATES
For Fiscal Plan Year beginning 01/01/2025 and ending 12/31/2025
2. Insurance Fees and commissions paid to agents and brokers:
001234 DIFFERENT ADVISORY LLC 42 EXAMPLE RD $125.25{fee_text}
BOSTON MA 02110
TOTAL $125.25
Part III Welfare Benefit Contract Information
""")]
    rows = extract_litera_aetna_schedule_a_broker_rows(pages)
    assert len(rows) == 1
    assert rows[0].name == "DIFFERENT ADVISORY LLC"
    assert rows[0].address_line_1 == "42 EXAMPLE RD"
    assert rows[0].commission_total == "125.25"
    assert rows[0].fee_total == expected_fee
    if expected_fee == "25.50":
        assert rows[0].fee_rows[0].purpose == "Service Fee"


def test_aetna_abbreviated_carrier_and_policy_dates_still_preserve_blank_fee_cells():
    pages = [(2, """aetna INSURANCE INFORMATION
PART I Information Concerning Insurance Contract Coverage, Fees, and Commissions.
(a) Name of Insurance Carrier: Aetna Life Insurance Co.
(f) From: 01/01/2024 (g) To: 12/31/2024
2. Insurance Fees and commissions paid to agents and brokers:
Contract or Identification (a) Name and address of the agents or brokers
(b) Amount of commissions paid (c) & (d) Fees Paid
00987654 EXAMPLE BENEFITS AGENCY LLC $125.25
42 RESEARCH RD
BOSTON MA 02110
TOTAL $125.25
Part III Welfare Benefit Contract Information
""")]
    rows = extract_litera_aetna_schedule_a_broker_rows(pages)
    assert len(rows) == 1
    assert rows[0].name == "EXAMPLE BENEFITS AGENCY LLC"
    assert rows[0].commission_total == "125.25"
    assert rows[0].fee_total is None
    assert rows[0].source_page == 2


@pytest.mark.parametrize('purpose', ['Administration','Renewal | incentive'])
def test_aetna_ocr_column_border_is_not_part_of_the_printed_fee_purpose(purpose):
    rows=extract_litera_aetna_schedule_a_broker_rows([(3,f'''Aetna Life Insurance Co.
For Fiscal Plan Year beginning 01/01/2025 and ending 12/31/2025
2. Insurance Fees and commissions paid to agents and brokers:
00987654 EXAMPLE BENEFITS AGENCY LLC 42 RESEARCH RD $125.25 $15.50 | {purpose}
BOSTON MA 02110
TOTAL $125.25 $15.50
Part III Welfare Benefit Contract Information
''')])
    assert len(rows)==1
    assert rows[0].fee_rows[0].purpose==purpose
    assert rows[0].purpose==purpose
    assert '| '+purpose in rows[0].evidence[0].source_text


def test_aetna_keeps_each_paid_recipient_instead_of_only_the_first_row():
    pages = [(1, """AETNA LIFE INSURANCE COMPANY AND AFFILIATES
For Fiscal Plan Year beginning 01/01/2024 and ending 12/31/2024
2. Insurance Fees and commissions paid to agents and brokers:
00987654 FIRST AGENCY LLC 42 RESEARCH RD $125.25
BOSTON MA 02110
00987654 SECOND AGENCY INC 19 OAK RD $75.00 $15.50 Service Fee
DALLAS TX 75201
TOTAL $200.25 $15.50
Part III Welfare Benefit Contract Information
""")]
    rows = extract_litera_aetna_schedule_a_broker_rows(pages)
    assert [(row.name, row.commission_total, row.fee_total) for row in rows] == [
        ("FIRST AGENCY LLC", "125.25", None),
        ("SECOND AGENCY INC", "75.00", "15.50"),
    ]


@pytest.mark.parametrize("fee_text,expected", [("", None), (" $0.00", "0.00"), (" $15.50", "15.50")])
def test_aetna_source_table_reconciles_blank_and_explicit_fee_values_without_changing_provider_history(fee_text, expected):
    from app.models import NormalizedExtractionField, NormalizedExtractionResult, ScheduleABrokerRow
    from app.services.extractor import reconcile_aetna_part_i_compensation
    pages = [(1, f"""Aetna Life Insurance Co.
2. Insurance Fees and commissions paid to agents and brokers:
(a) Name and address of the agents or brokers
(b) Amount of commissions paid (c) & (d) Fees Paid
00987654 EXAMPLE BENEFITS AGENCY LLC $125.25{fee_text}
42 RESEARCH RD
BOSTON MA 02110
TOTAL $125.25{fee_text}
Part III Welfare Benefit Contract Information
""")]
    result = NormalizedExtractionResult(provider="Example", fields=[
        NormalizedExtractionField(field_name="3c. Amount of Fees", value="0", confidence=.97),
    ], schedule_a_broker_rows=[ScheduleABrokerRow(name="EXAMPLE BENEFITS AGENCY LLC", fee_total="0")],
       raw={"provider_snapshot": {"fee": "0"}})
    reconcile_aetna_part_i_compensation(result, pages)
    fees = [field.value for field in result.fields if field.field_name.startswith("3c.")]
    assert fees == ([] if expected is None else [expected])
    assert result.schedule_a_broker_rows[0].fee_total == expected
    assert result.raw["provider_snapshot"] == {"fee": "0"}
    assert result.raw["aetna_compensation_reconciliation"]["complete_paid_rows"] == 1


@pytest.mark.parametrize("extra_row,total", [
    ("", "$200.00"),
    ("00987654 UNREADABLE RECIPIENT $75.00", "$200.25"),
    ("", "$125.25 $10.00"),
])
def test_aetna_incomplete_or_unreconciled_table_cannot_erase_provider_values(extra_row, total):
    from app.models import NormalizedExtractionField, NormalizedExtractionResult, ScheduleABrokerRow
    from app.services.extractor import reconcile_aetna_part_i_compensation
    result = NormalizedExtractionResult(provider="Example", fields=[
        NormalizedExtractionField(field_name="3c. Amount of Fees", value="10.00", confidence=.97),
    ], schedule_a_broker_rows=[ScheduleABrokerRow(name="PROVIDER RECIPIENT", fee_total="10.00")])
    reconcile_aetna_part_i_compensation(result, [(1, f"""Aetna Life Insurance Co.
2. Insurance Fees and commissions paid to agents and brokers:
(a) Name and address of the agents or brokers
(b) Amount of commissions paid (c) & (d) Fees Paid
00987654 EXAMPLE BENEFITS AGENCY LLC $125.25
42 RESEARCH RD
BOSTON MA 02110
{extra_row}
TOTAL {total}
Part III Welfare Benefit Contract Information
""")])
    assert result.fields[0].value == "10.00"
    assert result.schedule_a_broker_rows[0].name == "PROVIDER RECIPIENT"
