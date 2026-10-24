"""Uncertain JPEG cost glyphs need source evidence, never guessed decimals."""
import asyncio
import hashlib
import io
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from pypdf import PdfWriter
from app.config import Settings
from app.services import extractor


def extract_unreadable_cost(monkeypatch, *, png_amount='.00', lossless_error=None):
    header = '''INSURANCE DATA FOR SCHEDULE A (FORM 5500)
1. NAME OF CARRIER, SERVICE OR OTHER ORGANIZATION:
First Unum Life Insurance Company
TAX ID: 123456789 NAIC: 12345
2. CONTRACT NUMBER: 009876
3. APPROXIMATE NUMBER OF PERSONS COVERED AT END OF POLICY YEAR: 57
4. DATE FOR PERIOD: FROM 01-01-2025 TO 01-01-2026
7. NON-PARTICIPATING CONTRACTS (PREMIUMS):
(A) TOTAL PREMIUM OR SUBSCRIPTION CHARGES PAID
TO CARRIER $1,234.56
(C) IF THE CARRIER, SERVICE OR OTHER ORGANIZATION
INCURRED SPECIFIC COSTS IN CONNECTION WITH THE CONTRACT
'''
    primary = header.strip()
    native_run = subprocess.run
    native_which = shutil.which
    native_temporary = tempfile.TemporaryDirectory

    def temporary(*args, **kwargs):
        if lossless_error == 'temp' and kwargs.get('prefix') == 'schedule-a-cost-source-':
            raise OSError('Temporary source reader unavailable')
        return native_temporary(*args, **kwargs)

    def run(args, **kwargs):
        if Path(args[0]).stem == 'pdftoppm':
            if '-png' in args and lossless_error == 'render_timeout':
                raise subprocess.TimeoutExpired(args, 60)
            suffix = '.png' if '-png' in args else '-1.jpg'
            Path(args[-1] + suffix).write_bytes(b'external renderer response')
            return SimpleNamespace(stdout='')
        if Path(args[0]).stem == 'tesseract':
            if Path(args[1]).suffix == '.png' and lossless_error == 'ocr_timeout':
                raise subprocess.TimeoutExpired(args, 60)
            text = header + 'REPORT AMOUNT ... ' + png_amount if Path(args[1]).suffix == '.png' else (
                header + 'REPORT AMOUNT ... -00' if args[-1] == '6' else primary)
            return SimpleNamespace(stdout=text)
        return native_run(args, **kwargs)

    monkeypatch.setattr(shutil, 'which', lambda name: name if name in {'pdftoppm', 'tesseract'} else native_which(name))
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(tempfile, 'TemporaryDirectory', temporary)
    settings = Settings(_env_file=None, groundx_api_key=None, groundx_bucket_id=None,
        eyelevel_api_key=None, eyelevel_extract_url=None, mongodb_uri=None, ftwlink_key_id=None,
        s3_bucket_name=None, schedule_a_canonical_validation_enabled=True,
        schedule_a_canonical_validation_shadow_enabled=False)
    monkeypatch.setattr(extractor, 'get_settings', lambda: settings)
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    stream = io.BytesIO()
    writer.write(stream)
    data = stream.getvalue()
    async def extract():
        # Windows asyncio first creates a loopback self-pipe. Block sockets
        # only during the application operation, after the loop exists.
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline only')):
            return await extractor.ExtractionService([]).extract_schedule_a(data, 'different-example.pdf')
    result = asyncio.run(extract())
    return result, data, primary


@pytest.mark.parametrize('png_amount, expected', [('.00', '0.00'), ('25.01', '25.01'), ('-15.02', '-15.02')])
def test_public_local_route_can_read_an_unreadable_cost_losslessly_without_replacing_primary_ocr(monkeypatch, png_amount, expected):
    result, data, primary = extract_unreadable_cost(monkeypatch, png_amount=png_amount)
    cost = next(value for value in result.raw['source_review']['reported_values'] if value['key'] == 'additional_costs')
    assert cost['value'] == expected and cost['value_kind'] == ('PRINTED_ZERO' if expected == '0.00' else 'PRINTED_VALUE')
    assert cost['source_sha256'] == hashlib.sha256(data).hexdigest()
    assert cost['ocr_format'] == 'png' and cost['decision'] == 'SOURCE_ONLY'
    assert result.raw['ocr_pages'] == [{'page': 1, 'text': primary}]
    assert not any(field.field_name.startswith('10b.') for field in result.fields)


def test_lossless_temporary_io_failure_keeps_review_instead_of_crashing_extraction(monkeypatch):
    result, _, primary = extract_unreadable_cost(monkeypatch, lossless_error='temp')
    cost = next(value for value in result.raw['source_review']['reported_values'] if value['key'] == 'additional_costs')
    assert cost['value'] == '' and cost['value_kind'] == 'UNREADABLE' and cost['decision'] == 'REVIEW_REQUIRED'
    assert result.raw['ocr_pages'] == [{'page': 1, 'text': primary}]
    assert result.raw['lossless_cost_ocr_errors'][0]['error_type'] == 'OSError'


@pytest.mark.parametrize('failure', ['render_timeout', 'ocr_timeout'])
def test_lossless_tool_timeout_does_not_invent_zero_or_remove_review(monkeypatch, failure):
    result, _, primary = extract_unreadable_cost(monkeypatch, lossless_error=failure)
    cost = next(value for value in result.raw['source_review']['reported_values'] if value['key'] == 'additional_costs')
    assert cost['value'] == '' and cost['value_kind'] == 'UNREADABLE' and cost['decision'] == 'REVIEW_REQUIRED'
    assert result.raw['lossless_cost_source_reads'][0]['error_type'] == 'TimeoutExpired'
    assert result.raw['ocr_pages'] == [{'page': 1, 'text': primary}]


def test_unreadable_lossless_text_is_not_replaced_with_an_assumed_zero(monkeypatch):
    result, _, _ = extract_unreadable_cost(monkeypatch, png_amount='-00')
    cost = next(value for value in result.raw['source_review']['reported_values'] if value['key'] == 'additional_costs')
    assert cost['value'] == '' and cost['value_kind'] == 'UNREADABLE' and cost['decision'] == 'REVIEW_REQUIRED'


def test_existing_cost_conflict_is_not_resolved_by_taking_another_ocr_vote(monkeypatch):
    from app.models import NormalizedExtractionResult
    from app.services.schedule_a_source_observations import attach_schedule_a_source_observations, supplement_unreadable_cost_source_observations

    source = b'bound PDF'
    primary = 'incurred specific costs in connection with the policy'
    result = NormalizedExtractionResult(provider='Source', fields=[], raw={
        'local_ocr_pages': [{'page': 1, 'text': primary}],
        'local_ocr_source_reads': [{'page': 1, 'psm': 6, 'source_sha256': hashlib.sha256(source).hexdigest(),
                                  'text': primary + '\nREPORT AMOUNT ' + value} for value in ['.00', '25.01']]})
    attach_schedule_a_source_observations(result, [(1, primary)], source_sha256=hashlib.sha256(source).hexdigest())
    assert result.raw['source_review']['cost_source_reconciliations'][0]['action'] == 'CONFLICT_REQUIRES_REVIEW'
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('No additional read allowed')))
    supplement_unreadable_cost_source_observations(result, source, 'example.pdf')
    assert not result.raw.get('lossless_cost_ocr_attempted_pages')
    assert result.raw['source_review']['reported_values'][0]['decision'] == 'REVIEW_REQUIRED'


def test_lossless_source_reader_is_page_bounded_and_missing_tools_supply_no_value(monkeypatch):
    from app.services.schedule_a_cost_ocr import read_lossless_cost_source_pages

    monkeypatch.setattr(shutil, 'which', lambda name: name)
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout='REPORT AMOUNT .00'))
    source = b'bound PDF'
    reads = read_lossless_cost_source_pages(source, [True, -1, 0, 1, 2, 3, 4, 20, 21, '2'])
    assert [read['page'] for read in reads] == [1, 2, 3]
    assert all(read['ocr_format'] == 'png' and read['psm'] == 6
               and read['source_sha256'] == hashlib.sha256(source).hexdigest() for read in reads)
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    assert read_lossless_cost_source_pages(source, [1]) == []
