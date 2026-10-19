import pytest
from app.services.extractor import extract_vsp_broker_rows, extract_vsp_schedule_a_fields


def test_vsp_keeps_all_brokers_after_one_shared_table_heading():
    source='''SCHEDULE A Insurance Information
Insurance Carrier: Vision Service Plan
Insurance Fees and Commissions Paid to Agents and Brokers:
Commissions/Fees Paid for Policy
Agent or Broker or Contract Year
FIRST BENEFITS AGENCY LLC $125.25
42 RESEARCH RD
BOSTON MA 02110
SECOND BENEFITS AGENCY LLC $100.00
45 OTHER RD
BOSTON MA 02110
'''
    rows=extract_vsp_broker_rows([(1,source)])
    assert [(row.name,row.commission_total) for row in rows]==[
        ('FIRST BENEFITS AGENCY LLC','125.25'),('SECOND BENEFITS AGENCY LLC','100.00'),
    ]
    assert all(row.decision=='REVIEW_REQUIRED' for row in rows)
    fields={field.field_name.split('.')[0]:field for field in extract_vsp_schedule_a_fields([(1,source)])}
    assert fields['3b'].value=='225.25'
    assert fields['3b'].decision=='REVIEW_REQUIRED'


@pytest.mark.parametrize('compact', [False, True])
def test_vsp_explicit_naic_na_uses_approved_default_instead_of_a_nearby_policy_year(compact):
    from app.models import NormalizedExtractionField, NormalizedExtractionResult
    from app.services.extractor import prefer_authoritative_carrier_statement_fields
    from app.services.schedule_a_extraction_pipeline import apply_schedule_a_pipeline
    source = '''Schedule A Form (5500) Insurance Information
Group ID: 00987654
Insurance Carrier: Vision Service Plan
Insurance Carrier NAIC Code: N/A
Insurance Carrier FEIN: 941632821
Policy or Contract Year: 01/01/2024 - 12/31/2024
'''
    if compact:
        source = source.replace('N/A\nInsurance', 'N/AInsurance')
    fields = prefer_authoritative_carrier_statement_fields([
        NormalizedExtractionField(field_name='1c. NAIC Code', value='2024', confidence=.99),
    ], [(1, source)])
    result = apply_schedule_a_pipeline(NormalizedExtractionResult(provider='Example',fields=fields),
                                       authoritative=False,shadow=False)
    naic = next(field for field in result.fields if field.field_name.startswith('1c.'))
    assert naic.value == '00000'
    assert any(e.provider == 'Customer configured default' for e in naic.evidence)


def test_vsp_explicit_claims_and_retention_summary_does_not_keep_a_second_nonexperience_premium():
    from app.models import NormalizedExtractionField
    from app.services.extractor import prefer_authoritative_carrier_statement_fields
    source = '''Schedule A Form (5500) Insurance Information
Insurance Carrier: Vision Service Plan
Total Administrative Fees Paid to Carrier: $15.50
Total Payments Made to Carrier: $125.25
Total Claims Paid by Carrier: $100.00
'''
    fields = prefer_authoritative_carrier_statement_fields([
        NormalizedExtractionField(field_name='10a. Total premiums or subscription charges paid to carrier',value='125.25'),
    ], [(1,source)])
    assert not any(field.field_name.startswith('10a.') for field in fields)
    assert any(field.field_name.startswith('9a.') and field.value=='125.25' for field in fields)
