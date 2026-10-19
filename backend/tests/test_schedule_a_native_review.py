"""Visible source review must survive an incomplete PDF layout-text decode."""
import asyncio
from io import BytesIO

import httpx
import pytest
from pypdf import PdfWriter
from pypdf._page import PageObject
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from app.config import get_settings
from app.models import FormType
from app.services.extractor import ExtractionService
from app.services.mapping import map_extraction_to_rules
from app.services.schedule_a_pdf_source import visible_native_text_pages


def native_statement(naic='000-12345', *, value_x=240, color='0 0 0 rg'):
    pdf=PdfWriter()
    page=pdf.add_blank_page(width=612,height=792)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):pdf._add_object(font)})})
    rows=[(720,'INSURANCE DATA FOR SCHEDULE A'),
          (700,'Name of insurance carrier: Synthetic Life Insurance Company'),
          (680,'NAIC CODE:'),(660,'Contract number: 00981234'),
          (640,'NUMBER OF PERSONS COVERED: 53'),
          (620,'FROM: 01/01/2024 TO: 12/31/2024'),
          (600,'Total premiums paid to carrier: $1234.56')]
    commands=[f'BT /F1 10 Tf 30 {y} Td ({text}) Tj ET' for y,text in rows]
    commands.extend([color,f'BT /F1 10 Tf {value_x} 680 Td ({naic}) Tj ET'])
    stream=DecodedStreamObject();stream.set_data('\n'.join(commands).encode('ascii'))
    page[NameObject('/Contents')]=pdf._add_object(stream)
    output=BytesIO();pdf.write(output)
    return output.getvalue()


def test_visible_overlength_naic_is_held_through_the_full_extraction_and_mapping_path(monkeypatch):
    monkeypatch.setenv('GROUNDX_API_KEY','')
    monkeypatch.setenv('EYELEVEL_API_KEY','test-no-network')
    monkeypatch.setenv('EYELEVEL_EXTRACT_URL','https://provider.invalid/extract')
    monkeypatch.setenv('SCHEDULE_A_CANONICAL_VALIDATION_ENABLED','true')
    monkeypatch.setenv('SCHEDULE_A_CANONICAL_VALIDATION_SHADOW_ENABLED','false')
    get_settings.cache_clear()
    # Reproduce a decoder losing an appended overlay in layout mode only.
    # This is the external PDF-library boundary, not an application mock.
    original_text=PageObject.extract_text
    def decode(page,*args,**kwargs):
        value=original_text(page,*args,**kwargs)
        return value.replace('000-12345','') if kwargs.get('extraction_mode')=='layout' else value
    monkeypatch.setattr(PageObject,'extract_text',decode)
    original_client=httpx.AsyncClient
    def client(*args,**kwargs):
        kwargs['transport']=httpx.MockTransport(lambda request:httpx.Response(200,json={
            'fields':[{'fieldName':'1c. NAIC Code','value':'12345','confidence':.99,'page':1}]}))
        return original_client(*args,**kwargs)
    monkeypatch.setattr(httpx,'AsyncClient',client)
    try:
        result=asyncio.run(ExtractionService().extract_schedule_a(native_statement(),'changed-values.pdf'))
        naic=next(field for field in result.fields if field.field_name.startswith('1c.'))
        assert naic.decision=='REVIEW_REQUIRED', 'Do not automatically approve dropped visible identifier digits.'
        assert any(v.validator=='business_rule_confirmation' for v in naic.validation_results)
        assert any('000-12345' in (e.source_text or '') for e in naic.evidence)
        mapped=map_extraction_to_rules('offline',result.fields,form_type=FormType.SCHEDULE_A)
        target=next(field for field in mapped['fields'] if field.source_field_name==naic.field_name)
        assert target.proposed_value==''
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize('settings', [
    {'value_x':800}, {'color':'1 1 1 rg'}, {'color':'0 0 0 0 k'},
    {'color':'0.98 0.74 0.02 rg', 'naic':'Field 1c NAIC CODE: 000-12345'},
])
def test_invisible_values_and_mapping_instructions_cannot_become_review_source(settings):
    pages=visible_native_text_pages(native_statement(**settings))
    assert pages and pages[0][0]==1
    assert 'NAIC CODE:' in pages[0][1]
    assert '000-12345' not in pages[0][1]


def test_a_legitimate_leading_zero_naic_remains_five_digits_in_visible_source():
    pages=visible_native_text_pages(native_statement('01234'))
    assert 'NAIC CODE: 01234' in pages[0][1]
