import pytest
from app.services.extractor import extract_cigna_schedule_a_fields


def test_cigna_clean_repeated_header_recovers_annotation_corrupted_primary_row():
    main = """Cigna Health and Life Insurance Company
Summary of All Insurance Contracts Included in Part III
Insurance fees and commissions information
Part III Welfare Benefit Contract Information
59-1031071 67369 Field 1d 004567890 Field 1137 Employees 04/01/2024 03/31/2025
Total Amount of commissions paid $12,345.67
Total Amount of fees paid
3. Persons receiving commissions and fees
Schedule A Insurance Information - Footnotes
"""
    support = """Schedule A Insurance Information - Footnotes
Cigna Health and Life Insurance Company
59-1031071 67369
004567890 137 Employees 04/01/2024 03/31/2025
Part I, line 1a: Name of Insurance Carrier
Commissions: $12,345.67
General Agent Fees: $0
"""
    fields = {f.field_name: f for f in extract_cigna_schedule_a_fields([(1, main), (2, support)])}
    assert fields["1e. Persons Covered (End of Policy Year)"].value == "137"
    assert fields["1e. Persons Covered (End of Policy Year)"].page == 2
    assert fields["1d. Contract/Policy Number"].value == "004567890"
    assert fields["3c. Amount of Fees"].value == "0"


def test_other_contract_support_cannot_replace_the_primary_record():
    main = """Cigna Health and Life Insurance Company
Summary of All Insurance Contracts Included in Part III
Insurance fees and commissions information
Part III Welfare Benefit Contract Information
59-1031071 67369 004567890 137 Employees 04/01/2024 03/31/2025
"""
    support = """Schedule A Insurance Information - Footnotes
59-1031071 67369 009999999 999 Employees 04/01/2024 03/31/2025
Part I, line 1a: Name of Insurance Carrier
General Agent Fees: $125
"""
    fields = {f.field_name: f for f in extract_cigna_schedule_a_fields([(1, main), (2, support)])}
    assert fields["1e. Persons Covered (End of Policy Year)"].value == "137"
    assert fields["1d. Contract/Policy Number"].value == "004567890"
    assert "3c. Amount of Fees" not in fields


def test_carrier_identity_preserves_the_printed_alias_instead_of_a_constant():
    carrier = 'Cigna Health and Life Insurance Company and affiliates ("Cigna")'
    main = f"""Cigna Health and Life Insurance Company
Summary of All Insurance Contracts Included in Part III
Name of Insurance Carrier: {carrier}
NAIC Code (d) Contract or Identification Number
59-1031071 67369 1234567 137 Employees 04/01/2024 03/31/2025
Insurance fees and commissions information
Part III Welfare Benefit Contract Information
"""
    fields = {f.field_name: f for f in extract_cigna_schedule_a_fields([(1, main)])}
    assert fields["1a. Name of Insurance Company"].value == carrier


def test_cigna_blank_purpose_is_not_filled_with_an_invented_general_agent_label():
    main = """Cigna Health and Life Insurance Company
Summary of All Insurance Contracts Included in Part III
59-1031071 67369 004567890 137 Employees 04/01/2024 03/31/2025
Insurance fees and commissions information
Total Amount of commissions paid $125.25
Total Amount of fees paid $0
Part III Welfare Benefit Contract Information
"""
    fields = {f.field_name: f for f in extract_cigna_schedule_a_fields([(1, main)])}
    assert "3d. Purpose" not in fields, "Only a printed purpose belongs in raw extracted source fields."


def test_cigna_summary_retains_changed_printed_recipient_code_not_a_constant_three():
    from app.services.extractor import extract_cigna_schedule_a_broker_rows
    main = """Cigna Health and Life Insurance Company
Summary of All Insurance Contracts Included in Part III
59-1031071 67369 004567890 137 Employees 04/01/2024 03/31/2025
Insurance fees and commissions information
Total Amount of commissions paid $125.25 Total Amount of fees paid $0
3. Persons receiving commissions and fees
DIFFERENT BENEFITS INC, 42 RESEARCH RD, BOSTON, MA, 02110 $125.25 $0
6-Other Organization
Part III Welfare Benefit Contract Information
"""
    fields = {f.field_name: f for f in extract_cigna_schedule_a_fields([(1, main)])}
    rows = extract_cigna_schedule_a_broker_rows([(1, main)])
    assert fields['3e. Organizational Code'].value == '6'
    assert len(rows) == 1 and rows[0].organization_code == '6'


def test_cigna_multiple_recipient_codes_are_read_from_each_source_row():
    from app.services.extractor import extract_cigna_schedule_a_broker_rows
    main = """Cigna Health and Life Insurance Company
Summary of All Insurance Contracts Included in Part III
Insurance fees and commissions information
3. Persons receiving commissions and fees
FIRST BENEFITS INC, 42 RESEARCH RD, BOSTON, MA, 02110 $125.25 $0
6-Other Organization
SECOND BENEFITS INC, 19 OAK RD, DALLAS, TX, 75201 $75.00 $0
03-Insurance Agent or Broker
Part III Welfare Benefit Contract Information
"""
    rows=extract_cigna_schedule_a_broker_rows([(1,main)])
    assert [(row.name,row.organization_code) for row in rows]==[
        ('FIRST BENEFITS INC','6'),('SECOND BENEFITS INC','03'),
    ]


def test_cigna_derived_purpose_uses_reconciled_payment_columns_not_an_earlier_wrong_provider_split():
    from app.models import NormalizedExtractionField
    from app.services.extractor import prefer_authoritative_cigna_summary_fields
    main = """Cigna Health and Life Insurance Company
Summary of All Insurance Contracts Included in Part III
Insurance fees and commissions information
Total Amount of commissions paid $125.25 Total Amount of fees paid $0
Part III Welfare Benefit Contract Information
"""
    fields = prefer_authoritative_cigna_summary_fields([
        NormalizedExtractionField(field_name='3d. Purpose',value='COMMISSIONS & FEES',confidence=.98),
    ],[(1,main)])
    purpose=next(field for field in fields if field.field_name.startswith('3d.'))
    assert purpose.value=='COMMISSIONS'
    assert 'derived' in purpose.source_text.lower()
    assert purpose.evidence and purpose.evidence[0].page==1


def test_cigna_printed_fee_purpose_survives_space_after_organization_code_hyphen():
    main = """Cigna Health and Life Insurance Company
Summary of All Insurance Contracts Included in Part III
Insurance fees and commissions information
Total Amount of commissions paid $125.25 Total Amount of fees paid $15.50
3. Persons receiving commissions and fees
DIFFERENT BENEFITS INC, 42 RESEARCH RD, BOSTON, MA, 02110 $125.25 $15.50 Consulting Services
3- Insurance Agent or Broker
Part III Welfare Benefit Contract Information
"""
    fields={field.field_name:field for field in extract_cigna_schedule_a_fields([(1,main)])}
    assert fields['3d. Purpose'].value=='Consulting Services'


def test_cigna_preserves_blank_fee_and_each_recipients_printed_fee_purpose():
    from app.services.extractor import extract_cigna_schedule_a_broker_rows
    main = """Cigna Health and Life Insurance Company
Summary of All Insurance Contracts Included in Part III
Insurance fees and commissions information
3. Persons receiving commissions and fees
FIRST BENEFITS INC, 42 RESEARCH RD, BOSTON, MA, 02110 $125.25
6-Other Organization
SECOND BENEFITS INC, 19 OAK RD, DALLAS, TX, 75201 $0 $15.50 Consulting Services
03- Insurance Agent or Broker
Part III Welfare Benefit Contract Information
"""
    rows=extract_cigna_schedule_a_broker_rows([(1,main)])
    assert rows[0].fee_total is None
    assert rows[1].fee_total=='15.50'
    assert rows[1].fee_rows[0].purpose=='Consulting Services'


def test_cigna_next_section_number_is_not_a_fee_when_the_total_cell_is_unread():
    main = """Cigna Health and Life Insurance Company
Summary of All Insurance Contracts Included in Part III
Insurance fees and commissions information
Total Amount of commissions paid $125.25 Total Amount of fees paid
3
Persons receiving commissions and fees
Part III Welfare Benefit Contract Information
"""
    fields={field.field_name:field.value for field in extract_cigna_schedule_a_fields([(1,main)])}
    assert '3c. Amount of Fees' not in fields


@pytest.mark.parametrize('fee_cell', [' $15.50', ' 15.50', '\n$15.50'])
def test_cigna_positive_fee_cells_remain_supported_with_or_without_a_currency_symbol(fee_cell):
    main=f'''Cigna Health and Life Insurance Company
Summary of All Insurance Contracts Included in Part III
Insurance fees and commissions information
Total Amount of commissions paid $125.25 Total Amount of fees paid{fee_cell}
3. Persons receiving commissions and fees
Part III Welfare Benefit Contract Information
'''
    fields={field.field_name:field.value for field in extract_cigna_schedule_a_fields([(1,main)])}
    assert fields['3c. Amount of Fees']=='15.50'
