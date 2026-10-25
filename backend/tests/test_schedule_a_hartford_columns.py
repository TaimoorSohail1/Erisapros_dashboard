from decimal import Decimal
import pytest

from app.services.extractor import extract_hartford_broker_rows


@pytest.mark.parametrize('first,second', [('11.25','19.50'), ('6.75','102.40')])
def test_flattened_interleaved_money_keeps_each_commission_with_its_policy(first,second):
    total=Decimal(first)+Decimal(second)
    source=f'''Producer and Address EXAMPLE RESEARCH AGENCY LLC 42 TEST RD BOSTON , MA 02110
Org Code 6 Policy Number 000123-GL 000123-LTD Total
Commissions Fees Paid Paid $ {first} $ 0.00 $ {second} $ 0.00 $ {total} $ 0.00
(1) Bonus Paid $ 0.00 $ 0.00 $ 0.00
(2) Additional Compensation Paid $ 0.00 $ 0.00 $ 0.00
'''
    rows=extract_hartford_broker_rows([(2,'Annual Statement of Premiums and Producer Compensation'),(3,source)])
    assert len(rows)==1
    assert rows[0].decision=='AUTOMATIC'
    assert rows[0].commission_total==format(total,'.2f')
    assert rows[0].fee_total=='0.00'
    assert [(r.coverage,Decimal(r.amount)) for r in rows[0].commission_rows]==[('000123-GL',Decimal(first)),('000123-LTD',Decimal(second))]


def test_combined_columns_do_not_guess_when_two_policy_allocations_reconcile():
    source='''Producer and Address EXAMPLE RESEARCH AGENCY LLC 42 TEST RD BOSTON , MA 02110
Org Code 6 Policy Number 000123-GL 000123-LTD Total
Commissions Fees Paid Paid $ 0.00 $ 2.00 $ 2.00 $ 5.00 $ 2.00 $ 7.00
(1) Bonus Paid $ 0.00 $ 0.00 $ 0.00
(2) Additional Compensation Paid $ 0.00 $ 0.00 $ 0.00
'''
    # Both readings total 2 commissions and 7 fees but assign the fees to
    # different policies. A coincidentally matching total is not sufficient.
    assert extract_hartford_broker_rows([(2,'Annual Statement of Premiums and Producer Compensation'),(3,source)])==[]


def test_adjacent_numbered_bonus_and_additional_headings_preserve_each_payment_category():
    source='''Producer and Address EXAMPLE RESEARCH AGENCY LLC 42 TEST RD BOSTON , MA 02110
Org Code 6 Policy Number 000123-GL 000123-LTD Total
Commissions Fees Paid Paid $ 0.00 $ 0.00 $ 0.00 $ 0.00 $ 0.00 $ 0.00
Paid (1) Bonus (2) Additional Compensation Paid $ 2.25 $ 1.10 $ 4.50 $ 3.20 $ 6.75 $ 4.30
'''
    rows=extract_hartford_broker_rows([(2,'Annual Statement of Premiums and Producer Compensation'),(3,source)])
    assert len(rows)==1
    assert rows[0].decision=='AUTOMATIC'
    assert rows[0].commission_total=='0.00' and rows[0].fee_total=='11.05'
    assert [(r.coverage,r.amount,r.purpose) for r in rows[0].fee_rows]==[
        ('000123-GL','2.25','BONUS PAID'),('000123-LTD','4.50','BONUS PAID'),
        ('000123-GL','1.10','ADDITIONAL COMPENSATION'),('000123-LTD','3.20','ADDITIONAL COMPENSATION')]


def test_interleaved_hartford_columns_preserve_recipients_and_negative_components():
    header = """Org Policy Commissions (1)Bonus
Producer and Address Fees Paid Compensation
Code Number Paid Paid
Paid
"""
    pages = [(3, "Annual Statement of Premiums and Producer Compensation\n" + header + """
EXAMPLE BENEFITS 3 000123-GL -$0.80 $0.00 $0.00 $0.00
AGENCY LLC
100 OTHER AVE STE
401 000123-LTD $2.74 $0.00 $0.00 $0.00
BOSTON, MA 02199
Total $1.94 $0.00 $0.00 $0.00
""" + header + """
EXAMPLE BENEFITS 3 000123-GL $0.00 $0.00 -$5.57 $0.00
AGENCY LLC 000123-LTD $0.00 $0.00 $10.35 $0.00
2300 DIFFERENT BLVD
KING OF PRUSSIA, PA 19406
Total $0.00 $0.00 $4.78 $0.00
""")]
    rows = extract_hartford_broker_rows(pages)
    assert len(rows) == 2
    assert rows[0].name == rows[1].name == "EXAMPLE BENEFITS AGENCY LLC"
    assert rows[0].address_line_1 == "100 OTHER AVE STE 401"
    assert rows[1].address_line_1 == "2300 DIFFERENT BLVD"
    assert Decimal(rows[0].commission_total.replace(",", "")) == Decimal("1.94")
    assert [Decimal(r.amount.replace(",", "")) for r in rows[0].commission_rows] == [Decimal("-.80"), Decimal("2.74")]
    assert Decimal(rows[1].fee_total.replace(",", "")) == Decimal("4.78")
    assert [Decimal(r.amount.replace(",", "")) for r in rows[1].fee_rows] == [Decimal("-5.57"), Decimal("10.35")]


def test_printed_bonus_purpose_is_preserved_on_the_recipient_not_only_its_components():
    source = """Annual Statement of Premiums and Producer Compensation
Producer and Address Org Policy Commissions Fees (1)Bonus Paid Additional Compensation
EXAMPLE AGENCY 3 000123-GL $0.00 $0.00 $15.50 $0.00
42 EXAMPLE RD
BOSTON, MA 02110
Total $0.00 $0.00 $15.50 $0.00
"""
    rows = extract_hartford_broker_rows([(3, source)])
    assert len(rows) == 1
    assert rows[0].purpose == "BONUS PAID"


@pytest.mark.parametrize('interleaved_headings', [False, True])
def test_flattened_producer_columns_on_a_continuation_page_reconcile_each_money_category(interleaved_headings):
    pages = [(2, 'Annual Statement of Premiums and Producer Compensation'), (3, '''Producer and Address EXAMPLE AGENCY LLC 42 RESEARCH RD STE 2 BOSTON , MA 02110 Org Code 6 Policy Number 000123-GL 000123 - LTD Total Commissions Paid - $ 0.80 $ 2.74 $ 1.94 Fees Paid $ 0.00 $ 0.00 $ 0.00 (1) Paid Bonus $ 10.00 $ 5.50 $ 15.50 (2) Additional Compensation Paid $ 0.00 $ 0.00 $ 0.00
''')]
    if interleaved_headings:
        pages[1] = (3, pages[1][1].replace('Commissions Paid - $ 0.80 $ 2.74 $ 1.94 Fees Paid', 'Commissions Fees Paid Paid - $ 0.80 $ 2.74 $ 1.94'))
    rows = extract_hartford_broker_rows(pages)
    assert len(rows) == 1
    row = rows[0]
    assert row.name == 'EXAMPLE AGENCY LLC'
    assert row.address_line_1 == '42 RESEARCH RD STE 2'
    assert row.city == 'BOSTON' and row.organization_code == '6'
    assert Decimal(row.commission_total) == Decimal('1.94')
    assert Decimal(row.fee_total) == Decimal('15.50')
    assert [item.amount for item in row.commission_rows] == ['-0.80', '2.74']
    assert [item.amount for item in row.fee_rows] == ['10.00', '5.50']
    from app.services.schedule_a_source_observations import collect_schedule_a_source_observations
    components = collect_schedule_a_source_observations(pages)['broker_components']
    assert len(components) == 8, 'Include the zero columns as well as nonzero components.'
    assert [value['amount'] for value in components if value['category'] == 'commission'] == ['-0.80', '2.74']


def test_spaced_policy_and_currency_tokens_keep_negative_and_suffix_dollar_components():
    pages = [(2, 'Annual Statement of Premiums and Producer Compensation'), (3, '''Org Policy Commissions (1) Bonus
Producer and Address Fees Paid Compensation
Code Number Paid Paid
EXAMPLE AGENCY 6 000123-GL $ 0.00 $ 0.00 - $ 5.57 $ 0.00
LLC
000123 - LTD $ 0.00 $ 0.00 $ 1.94 $ 0.00
42 OTHER BLVD
000123 - GRH $ 0.00 $ 0.00 10.35 $ $ 0.00
BOSTON , MA 02110
Total $ 0.00 $ 0.00 $ 6.72 $ 0.00
''')]
    rows = extract_hartford_broker_rows(pages)
    assert len(rows) == 1
    assert rows[0].address_line_1 == '42 OTHER BLVD'
    assert rows[0].fee_total == '6.72'
    assert [item.amount for item in rows[0].fee_rows] == ['-5.57', '1.94', '10.35']


def test_hartford_scalar_organization_code_uses_printed_value_not_a_sample_default():
    from app.services.extractor import extract_hartford_schedule_a_fields
    pages = [(2, '''The Hartford Annual Statement of Premiums and Producer Compensation
HARTFORD LIFE AND ACCIDENT
Premium was applied as follows during the Plan/Policy Year -
'''), (3, '''Producer and Address EXAMPLE AGENCY LLC 42 RESEARCH RD BOSTON , MA 02110 Org Code 6 Policy Number 000123-GL Total Commissions Paid $ 25.00 $ 25.00 Fees Paid $ 0.00 $ 0.00 (1) Paid Bonus $ 0.00 $ 0.00 (2) Additional Compensation Paid $ 0.00 $ 0.00
''')]
    fields = extract_hartford_schedule_a_fields(pages)
    assert next(field.value for field in fields if field.field_name.startswith('3e.')) == '6'


@pytest.mark.parametrize('lives', ['284','731'])
def test_hartford_count_evidence_uses_the_actual_premium_page_not_the_cover(lives):
    from app.services.extractor import extract_hartford_schedule_a_fields
    pages=[(1,'''The Hartford
Annual Statement of Premiums and Producer Compensation
HARTFORD LIFE AND ACCIDENT
Cover letter explaining the report; no insurance table here.
'''),(2,f'''Premium was applied as follows during the Plan/Policy Year -
Policy Number Type of Benefit Premium Applied Approximate Lives Covered
000123-GL Life $123.45 {lives}
000123-AD AD&D $20.00 12
Total $143.45
''')]
    field=next(f for f in extract_hartford_schedule_a_fields(pages) if f.field_name.startswith('1e.'))
    assert field.value==lives
    assert field.page==2
    assert lives in field.source_text and 'Lives Covered' in field.source_text
    assert field.evidence and all(e.page==2 and lives in e.source_text for e in field.evidence)


def test_reversed_policy_tokens_stay_in_the_payment_row_not_the_recipient_address():
    pages=[(2,'Annual Statement of Premiums and Producer Compensation'),(3,'''Org Policy Commissions (1) Bonus
Producer and Address Fees Paid Compensation
Code Number Paid Paid
EXAMPLE AGENCY 6 000123-GL $0.00 $0.00 -$2.11 $0.00
LLC
000123-AD $0.00 $0.00 $3.22 $0.00
42 OTHER BLVD
- GRH 000123 $0.00 $0.00 $4.44 $0.00
BOSTON, MA 02110
Total $0.00 $0.00 $5.55 $0.00
''')]
    rows=extract_hartford_broker_rows(pages)
    assert len(rows)==1
    assert rows[0].decision=='AUTOMATIC'
    assert rows[0].address_line_1=='42 OTHER BLVD'
    assert rows[0].fee_total=='5.55'
    assert [r.amount for r in rows[0].fee_rows]==['-2.11','3.22','4.44']
    assert all('000123' in r.coverage for r in rows[0].fee_rows)
