"""Label/value geometry can recover a native overlay without OCR binaries."""
from io import BytesIO
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from app.services.schedule_a_visual_benefits import read_visual_benefit_evidence


def native_statement(value, *, value_x=200, white=False):
    pdf=PdfWriter()
    page=pdf.add_blank_page(width=612,height=792)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):pdf._add_object(font)})})
    content=DecodedStreamObject()
    # The value is appended after the template stream but shares its visual row.
    color='0 0 0 0 k' if white=='cmyk' else '1 1 1 rg' if white else '0 0 0 rg'
    content.set_data((f'BT /F1 10 Tf 30 700 Td (1. COVERAGE -) Tj ET\n'
                      'BT /F1 10 Tf 30 300 Td (Life Insurance mentioned in a footnote only.) Tj ET\n'
                      f'{color}\n'
                      f'BT /F1 10 Tf {value_x} 700 Td ({value}) Tj ET').encode('ascii'))
    page[NameObject('/Contents')]=pdf._add_object(content)
    stream=BytesIO();pdf.write(stream)
    return stream.getvalue()


@pytest.mark.parametrize('literal,kind',[('DENTAL','dental'),('VISION CARE','vision'),('LIFE INSURANCE','life')])
def test_native_labelled_overlay_is_read_when_external_ocr_is_unavailable(monkeypatch,literal,kind):
    monkeypatch.setenv('PATH','')
    result=read_visual_benefit_evidence(native_statement(literal),(1,))
    observations=[row for page in result['pages'] for row in page.get('observations',[])]
    assert [row['benefit_type'] for row in observations]==[kind]
    assert observations[0]['page']==1
    assert observations[0]['provider']=='Native visible labelled benefit'
    assert observations[0]['bounding_box']
    assert result['status']=='COMPLETED'


@pytest.mark.parametrize('settings',[{'value_x':800},{'white':True},{'white':'cmyk'}])
def test_off_page_or_white_text_and_an_unlabelled_footnote_do_not_create_a_benefit(monkeypatch,settings):
    monkeypatch.setenv('PATH','')
    result=read_visual_benefit_evidence(native_statement('DENTAL',**settings),(1,))
    assert [row for page in result['pages'] for row in page.get('observations',[])]==[]
