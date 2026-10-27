"""Scanned reports must be recognized without filename-dependent routing."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.models import DocumentType
from app.services.sharefile import ShareFileService


@pytest.mark.parametrize('ocr_text, expected', [
    ('SCHEDULE A INSURANCE INFORMATION Name of Insurance Carrier', DocumentType.SCHEDULE_A),
    ('An unrelated scanned invoice with no filing content', None),
])
def test_image_only_pdf_content_classification_uses_bounded_local_ocr(ocr_text, expected):
    service = ShareFileService()
    service._download_item = AsyncMock(return_value=b'%PDF-scanned-test')
    with patch('app.services.sharefile.extract_pdf_text_pages', return_value=[]), \
         patch('app.services.sharefile.extract_image_only_pdf_ocr_pages', return_value=[(1, ocr_text)], create=True) as ocr:
        result = asyncio.run(service._classify_sharefile_document_by_content(
            None, None, 'test-source', '1. Litera Aetna - Medical.pdf'))
    assert result == expected
    ocr.assert_called_once_with(b'%PDF-scanned-test', classification_only=True)


def test_native_text_classification_does_not_run_ocr():
    service = ShareFileService()
    service._download_item = AsyncMock(return_value=b'%PDF-text-test')
    with patch('app.services.sharefile.extract_pdf_text_pages', return_value=[(1, 'SCHEDULE A INSURANCE INFORMATION')]), \
         patch('app.services.sharefile.extract_image_only_pdf_ocr_pages', create=True) as ocr:
        result = asyncio.run(service._classify_sharefile_document_by_content(None, None, 'test-source', 'unknown.pdf'))
    assert result == DocumentType.SCHEDULE_A
    ocr.assert_not_called()
