"""Source costs distinguish printed amounts from unreadable or blank cells."""
from app.services.schedule_a_source_observations import collect_schedule_a_source_observations
import pytest
import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace
from app.services.extractor import extract_image_only_pdf_ocr_pages
from app.models import NormalizedExtractionResult
from app.services.schedule_a_source_observations import attach_schedule_a_source_observations


def test_labelled_report_amount_keeps_a_terminal_decimal_without_a_currency_sign():
    source = '''(C) IF THE CARRIER, SERVICE OR OTHER ORGANIZATION
INCURRED SPECIFIC COSTS IN CONNECTION WITH
THE ACQUISITION OR RETENTION OF THE CONTRACT
OR POLICY, OTHER THAN REPORTED IN NO. 5 ABOVE,
REPORT AMOUNT... 2... . ee eee ee ee ee ee eee ee .00'''
    observations = collect_schedule_a_source_observations([(1, source)])
    cost = next(item for item in observations['reported_values'] if item['key'] == 'additional_costs')
    assert cost['literal'] == '.00'
    assert cost['value'] == '0.00' and cost['value_kind'] == 'PRINTED_ZERO'
    assert cost['page'] == 1 and cost['decision'] == 'SOURCE_ONLY'
    assert 'REPORT AMOUNT' in cost['source_text']


@pytest.mark.parametrize('ending', ['', 'REPORT AMOUNT... .o0', 'REPORT AMOUNT... 2...'])
def test_incomplete_or_unreadable_cost_text_is_not_a_proven_printed_blank(ending):
    source = 'INCURRED SPECIFIC COSTS IN CONNECTION WITH THE CONTRACT NO. 5 ABOVE\n' + ending
    result = collect_schedule_a_source_observations([(2, source)])
    cost = next(item for item in result['reported_values'] if item['key'] == 'additional_costs')
    assert cost['literal'] == '' and cost['value'] == ''
    assert cost['value_kind'] == 'UNREADABLE'
    assert cost['decision'] == 'REVIEW_REQUIRED'


def test_ocr_preserves_the_existing_primary_route_and_its_other_source_read(monkeypatch):
    import shutil

    def run(args, **kwargs):
        if args[0] == 'pdftoppm':
            Path(args[-1] + '-1.jpg').write_bytes(b'boundary image')
            return SimpleNamespace(stdout='')
        return SimpleNamespace(stdout='SCHEDULE A FORM 5500\nREPORT AMOUNT .00' if args[-1] == '6'
                               else 'PRIMARY COLUMN READ')

    monkeypatch.setattr(shutil, 'which', lambda name: name)
    monkeypatch.setattr(subprocess, 'run', run)
    source = b'one bound PDF'
    reads = []
    primary = extract_image_only_pdf_ocr_pages(source, source_reads=reads)
    assert primary == [(1, 'PRIMARY COLUMN READ')]
    assert [(read['page'], read['psm']) for read in reads] == [(1, 6), (1, 4)]
    assert all(read['source_sha256'] == hashlib.sha256(source).hexdigest() for read in reads)
    assert reads[0]['text'].endswith('REPORT AMOUNT .00')


def test_local_pdf_supplement_retains_a_bound_alternate_cost_read_without_changing_primary(monkeypatch):
    import io
    import shutil
    from pypdf import PdfWriter
    from app.services.extractor import local_schedule_a_pdf_result, supplement_schedule_a_result_with_local

    primary = '''SCHEDULE A (FORM 5500)
NAME OF CARRIER: EXAMPLE LIFE
TAX ID: 12-3456789 NAIC: 12345
CONTRACT NUMBER: 001234
DATE FOR PERIOD: FROM 01/01/2025 TO 12/31/2025
TOTAL PREMIUM PAID TO CARRIER $1,234.56
INCURRED SPECIFIC COSTS IN CONNECTION WITH THE CONTRACT
'''
    alternate = primary + 'REPORT AMOUNT........ .00'
    native_run = subprocess.run

    def run(args, **kwargs):
        if Path(args[0]).stem == 'pdftoppm':
            Path(args[-1] + '-1.jpg').write_bytes(b'boundary image')
            return SimpleNamespace(stdout='')
        if Path(args[0]).stem == 'tesseract':
            return SimpleNamespace(stdout=alternate if args[-1] == '6' else primary)
        return native_run(args, **kwargs)

    native_which = shutil.which
    monkeypatch.setattr(shutil, 'which', lambda name: name if name in {'pdftoppm', 'tesseract'} else native_which(name))
    monkeypatch.setattr(subprocess, 'run', run)
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    stream = io.BytesIO()
    writer.write(stream)
    source = stream.getvalue()
    local = local_schedule_a_pdf_result(source, 'unrelated-example.pdf', rules=[])
    direct_pages = [(item['page'], item['text']) for item in local.raw['ocr_pages']]
    attach_schedule_a_source_observations(local, direct_pages, source_sha256=hashlib.sha256(source).hexdigest())
    direct_cost = next(item for item in local.raw['source_review']['reported_values'] if item['key'] == 'additional_costs')
    assert direct_cost['value'] == '0.00' and direct_cost['value_kind'] == 'PRINTED_ZERO'
    result = supplement_schedule_a_result_with_local(NormalizedExtractionResult(provider='Provider', fields=[]), local)
    pages = [(item['page'], item['text']) for item in result.raw['local_ocr_pages']]
    assert pages == [(1, primary.strip())]
    attach_schedule_a_source_observations(result, pages, source_sha256=hashlib.sha256(source).hexdigest())
    cost = next(item for item in result.raw['source_review']['reported_values'] if item['key'] == 'additional_costs')
    assert cost['value'] == '0.00' and cost['value_kind'] == 'PRINTED_ZERO'
    assert cost['ocr_psm'] == 6 and cost['page'] == 1
    assert result.raw['source_review']['cost_source_reconciliations'][0]['primary']['value_kind'] == 'UNREADABLE'
    assert result.raw['local_ocr_pages'][0]['text'] == primary.strip()


@pytest.mark.parametrize('amount, kind, expected', [('.00', 'PRINTED_ZERO', '0.00'),
                                                 ('123.45', 'PRINTED_VALUE', '123.45'),
                                                 ('-25.01', 'PRINTED_VALUE', '-25.01'),
                                                 ('', 'PRINTED_BLANK', '')])
def test_complete_report_amount_distinguishes_zero_negative_nonzero_and_blank(amount, kind, expected):
    source = 'incurred any specific costs in connection with the policy\nreport amount .... $ ' + amount
    value = collect_schedule_a_source_observations([(1, source)])['reported_values'][0]
    assert value['value_kind'] == kind and value['value'] == expected


def test_direct_additional_cost_label_reads_its_own_currency_or_plain_decimal_cell():
    for amount in ['55.01', '$55.01']:
        source = 'b Additional costs incurred ' + amount + '\nc Other report values $999.00'
        value = collect_schedule_a_source_observations([(1, source)])['reported_values'][0]
        assert value['value'] == '55.01' and value['value_kind'] == 'PRINTED_VALUE'


def test_empty_direct_cost_cell_cannot_borrow_currency_from_a_following_line():
    source = 'b Additional costs incurred ....\nc Other report values $999.00'
    value = collect_schedule_a_source_observations([(1, source)])['reported_values'][0]
    assert value['value'] == '' and value['value_kind'] == 'PRINTED_BLANK'


@pytest.mark.parametrize('cell', ['$25.01 $99.02', '25.01 99.02'])
def test_two_different_amounts_in_one_cost_cell_are_not_silently_selected(cell):
    result = collect_schedule_a_source_observations([(1, 'Additional costs incurred ' + cell)])
    cost = result['reported_values'][0]
    assert cost['value'] == '' and cost['value_kind'] == 'UNREADABLE'
    assert cost['decision'] == 'REVIEW_REQUIRED'


@pytest.mark.parametrize('change', [{'source_sha256': 'other file'}, {'page': 2}, {'page': True},
                                   {'psm': 11}, {'text': None}])
def test_unbound_or_wrong_page_source_read_cannot_supply_a_cost(change):
    primary = [(1, 'incurred specific costs in connection with the policy')]
    read = {'page': 1, 'psm': 6, 'source_sha256': 'expected',
            'text': primary[0][1] + '\nREPORT AMOUNT .00', **change}
    result = NormalizedExtractionResult(provider='test', fields=[], raw={'local_ocr_source_reads': [read]})
    attach_schedule_a_source_observations(result, primary, source_sha256='expected')
    value = result.raw['source_review']['reported_values'][0]
    assert value['value_kind'] == 'UNREADABLE' and value['value'] == ''
    assert value['decision'] == 'REVIEW_REQUIRED'


@pytest.mark.parametrize('source_kind', ['provider', 'local_document_parser', None])
def test_unmarked_raw_ocr_reads_are_not_accepted_as_direct_local_pdf_evidence(source_kind):
    primary = [(1, 'incurred specific costs in connection with the policy')]
    result = NormalizedExtractionResult(provider='test', fields=[], raw={'source': source_kind, 'ocr_source_reads': [
        {'page': 1, 'psm': 6, 'source_sha256': 'expected', 'text': primary[0][1] + '\nREPORT AMOUNT .00'}]})
    attach_schedule_a_source_observations(result, primary, source_sha256='expected')
    assert result.raw['source_review']['reported_values'][0]['value_kind'] == 'UNREADABLE'


@pytest.mark.parametrize('primary_amount, alternate_amount', [('25.50', '.00'), ('', '.00'), ('.00', '25.50')])
def test_alternate_cost_read_cannot_overwrite_an_actual_amount_or_proven_blank(primary_amount, alternate_amount):
    label = 'incurred specific costs in connection with the policy\nREPORT AMOUNT ..... $ '
    result = NormalizedExtractionResult(provider='test', fields=[], raw={'local_ocr_source_reads': [
        {'page': 1, 'psm': 6, 'source_sha256': 'expected', 'text': label + alternate_amount}]})
    attach_schedule_a_source_observations(result, [(1, label + primary_amount)], source_sha256='expected')
    value = result.raw['source_review']['reported_values'][0]
    assert value['literal'].strip('$ ') == primary_amount
    assert value['decision'] == 'REVIEW_REQUIRED'
    assert result.raw['source_review']['cost_source_reconciliations'][0]['action'] == 'CONFLICT_REQUIRES_REVIEW'
