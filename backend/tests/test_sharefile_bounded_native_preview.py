"""Content recognition must not parse unrelated continuation-page geometry."""
import asyncio
from io import BytesIO
from unittest.mock import AsyncMock, patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.models import DocumentType
from app.services.sharefile import ShareFileService
from app.services import schedule_a_pdf_source as source
from app.services.extractor import extract_pdf_text_pages


def native_packet(lines):
    writer = PdfWriter()
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    font_ref = writer._add_object(font)
    for text in lines:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
            DictionaryObject({NameObject('/F1'): font_ref})})
        content = DecodedStreamObject()
        commands = []
        for index, (value, color) in enumerate(text if isinstance(text, list) else [(text, '0 0 0 rg')]):
            value = value.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            commands.append(f'{color} BT /F1 10 Tf 25 {700-index*30} Td ({value}) Tj ET')
        content.set_data('\n'.join(commands).encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(content)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_content_recognition_does_not_visit_geometry_beyond_consumed_two_pages():
    data = native_packet(['SCHEDULE A INSURANCE INFORMATION', 'Continuation', 'OUTSIDE PREVIEW'])
    source.mapping_note_text_overrides.cache_clear()
    visited = []
    original = source.source_page
    def observed(page):
        visited.append(page.page_number)
        return original(page)
    service = ShareFileService()
    service._download_item = AsyncMock(return_value=data)
    with patch.object(source, 'source_page', side_effect=observed):
        result = asyncio.run(service._classify_sharefile_document_by_content(None, None, 'source', 'unknown.pdf'))
    assert result == DocumentType.SCHEDULE_A
    assert visited == [1, 2]


def test_preview_cache_does_not_truncate_full_extraction_afterward():
    data = native_packet(['First page', 'Second page', 'Policy number 00123'])
    source.mapping_note_text_overrides.cache_clear()
    preview = extract_pdf_text_pages(data, max_pages=2)
    full = extract_pdf_text_pages(data)
    assert preview == full[:2]
    assert len(full) == 3
    assert '00123' in full[2][1]


def test_mapping_notes_are_excluded_in_both_preview_and_full_readers():
    data = native_packet([[
        ('SCHEDULE A INSURANCE INFORMATION', '0 0 0 rg'),
        ('Policy number 00123', '0 0 0 rg'),
        ('Field 1d Policy Number: 99999', '0.98 0.74 0.02 rg'),
    ], 'Continuation', 'Third page policy 00456'])
    source.mapping_note_text_overrides.cache_clear()
    preview = extract_pdf_text_pages(data, max_pages=2)
    full = extract_pdf_text_pages(data)
    assert preview == full[:2]
    assert '00123' in preview[0][1]
    assert '99999' not in preview[0][1]
    assert '00456' in full[2][1]


def test_full_reader_cache_does_not_expand_later_preview():
    data = native_packet(['First', 'Second', 'Third'])
    source.mapping_note_text_overrides.cache_clear()
    assert len(extract_pdf_text_pages(data)) == 3
    assert len(extract_pdf_text_pages(data, max_pages=2)) == 2


def test_title_beyond_native_preview_does_not_change_existing_classification():
    data = native_packet(['Unrelated invoice', 'Invoice detail', 'SCHEDULE A INSURANCE INFORMATION'])
    service = ShareFileService()
    service._download_item = AsyncMock(return_value=data)
    assert asyncio.run(service._classify_sharefile_document_by_content(None, None, 'source', 'unknown.pdf')) is None
