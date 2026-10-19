import pytest
from app.models import NormalizedExtractionField
from app.services.extractor import prefer_authoritative_carrier_statement_fields


@pytest.mark.parametrize('label,value',[
    ('3b. Amount of Commissions','125.25'),
    ('1b. Insurance Carrier EIN','13-1898173'),
])
def test_recognized_but_incomplete_parser_does_not_erase_a_provider_field_it_did_not_read(label,value):
    source='''FIRST UNUM LIFE INSURANCE COMPANY
INSURANCE DATA FOR SCHEDULE A
TAX ID: 13-1898173 NAIC: 64297
CONTRACT NUMBER: 00987654
SALES COMMISSION PAID: $125.25
'''
    provider=NormalizedExtractionField(field_name=label,value=value,confidence=.97,page=1,source_text=source)
    result=prefer_authoritative_carrier_statement_fields([provider],[(1,source)])
    retained=next(field for field in result if field.field_name==label)
    assert retained.value==value
    assert provider.value==value
