from app.services.extractor import extract_metlife_standard_schedule_a_fields, extract_metlife_standard_broker_rows


def test_metlife_money_fields_preserve_reported_cents_without_rounding_or_truncation():
    text = """METROPOLITAN LIFE INSURANCE COMPANY
SCHEDULE A Insurance Information
Total amount of commissions paid Total Fees Paid / amount
125.25 15.50
10 Nonexperience-rated contracts
Total premiums or subscription charges paid to carrier: $2,468.10
"""
    fields = {field.field_name.split('.')[0]: field.value
              for field in extract_metlife_standard_schedule_a_fields([(2, text)])}
    assert fields['10a'] == '2,468.10'
    assert fields['3b'] == '125.25'
    assert fields['3c'] == '15.50'


def test_metlife_broker_keeps_decimal_components_and_the_printed_recipient_code():
    text = """METROPOLITAN LIFE INSURANCE COMPANY
SCHEDULE A Insurance Information
Name: ANOTHER BENEFITS AGENCY
Address: 42 RESEARCH RD City:
BOSTON ST: MA ZIP: 02110
Commissions Paid Fees Paid Organization
Coverage Amount Purpose Coverage Amount Purpose code
LIFE 100.00 Base Commissions 06
Dental 25.25 Base Commissions
125.25 Sub-total 0 Sub-total
"""
    rows = extract_metlife_standard_broker_rows([(2, text)])
    assert len(rows) == 1
    assert rows[0].name == 'ANOTHER BENEFITS AGENCY'
    assert rows[0].commission_total == '125.25'
    assert [row.amount for row in rows[0].commission_rows] == ['100.00', '25.25']
    assert rows[0].organization_code == '06'
    assert rows[0].source_page == 2


def test_metlife_continuation_keeps_each_recipient_and_its_own_subtotal():
    header = 'METROPOLITAN LIFE INSURANCE COMPANY\nSCHEDULE A Insurance Information\n'
    first = '''Name: FIRST AGENCY
Address: 42 RESEARCH RD City: BOSTON ST: MA ZIP: 02110
Commissions Paid Fees Paid Organization code
LIFE 25.25 Base Commissions 03
25.25 Sub-total 0 Sub-total
'''
    second = '''Name: SECOND AGENCY
Address: 45 DIFFERENT RD City: BOSTON ST: MA ZIP: 02110
Commissions Paid Fees Paid Organization code
Dental 50.50 Base Commissions 06
50.50 Sub-total 0 Sub-total
'''
    rows = extract_metlife_standard_broker_rows([(1, header), (2, first), (3, second)])
    assert [(row.name, row.commission_total, row.organization_code, row.source_page) for row in rows] == [
        ('FIRST AGENCY', '25.25', '03', 2), ('SECOND AGENCY', '50.50', '06', 3),
    ]


def test_metlife_custom_table_reader_does_not_replace_a_labelled_irs_broker_form():
    source = '''SCHEDULE A Insurance Information
METROPOLITAN LIFE INSURANCE COMPANY
3 Persons receiving commissions and fees
Name: EXAMPLE AGENCY Address Line
1: 42 RESEARCH RD
City: BOSTON State: MA ZIP Code: 02110
(b) Amount of sales and base commissions paid
125.25 Sub-total
(c) Amount of fees paid: 15.50
'''
    assert extract_metlife_standard_broker_rows([(2, source)]) == []


def test_metlife_scalar_code_cannot_disagree_with_the_printed_recipient_code():
    text='''METROPOLITAN LIFE INSURANCE COMPANY
SCHEDULE A Insurance Information
Commissions Paid Fees Paid Organization code
LIFE 125.25 Base Commissions 06
125.25 Sub-total 0 Sub-total
'''
    fields={field.field_name.split('.')[0]:field.value
            for field in extract_metlife_standard_schedule_a_fields([(2,text)])}
    assert fields['3e']=='06'
