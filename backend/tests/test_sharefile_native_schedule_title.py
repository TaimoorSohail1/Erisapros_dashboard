"""Recognize an explicit report title without relying on a source filename."""
import asyncio
from unittest.mock import AsyncMock,patch

import pytest
from app.models import DocumentType
from app.services.sharefile import ShareFileService


@pytest.mark.parametrize('title',[
    'INSURANCE DATA FOR SCHEDULE A (FORM 5500)',
    '  Insurance Data for Schedule A (Form 5500)  ',
    'INSURANCE DATA FOR\nSCHEDULE A (FORM 5500)',
    'INSURANCE\tDATA FOR SCHEDULE A ( FORM 5500 )',
])
def test_explicit_tax_form_report_title_is_sufficient_for_native_pdf(title):
    text='ANNUAL REPORT\n'+title+'\nPREPARED FOR: EXAMPLE BENEFITS PLAN\nPOLICY: 123456\nPERIOD: 01/01/2025 - 12/31/2025'
    service=ShareFileService()
    service._download_item=AsyncMock(return_value=b'%PDF-native-report')
    with patch('app.services.sharefile.extract_pdf_text_pages',return_value=[(1,text)]), \
         patch('app.services.sharefile.extract_image_only_pdf_ocr_pages') as ocr:
        result=asyncio.run(service._classify_sharefile_document_by_content(None,None,'source','unknown.pdf'))
    assert result==DocumentType.SCHEDULE_A
    ocr.assert_not_called()


@pytest.mark.parametrize('text',[
    'An invoice mentions insurance data for Schedule A (Form 5500) in passing.',
    'INSURANCE DATA FOR SCHEDULE A\nPOLICY: 123456',
    'INSURANCE DATA FOR SCHEDULE A (FORM 5500-SF)',
    'The requested document is "INSURANCE DATA FOR SCHEDULE A (FORM 5500)".',
    'INVOICE\nSCHEDULE A\nPlease send insurance data separately.',
])
def test_incidental_incomplete_or_different_form_reference_is_not_a_report_title(text):
    assert ShareFileService()._classify_document_text(text) is None


def test_worksheet_with_an_inline_attachment_reference_keeps_its_type():
    text='PLAN SPONSOR NAME\nPLAN SPONSOR ADDRESS\nTOTAL NUMBER OF PARTICIPANTS\nAttachments include insurance data for Schedule A (Form 5500).'
    assert ShareFileService()._classify_document_text(text)==DocumentType.PLAN_WORKSHEET
