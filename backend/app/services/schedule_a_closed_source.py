"""Two source readers for a closed, explicitly totalled single-recipient grid.

Used for legacy filings whose original provider trace lacks a content hash.
The current bytes must match the stored source hash. Pypdf layout and a
separate pdfplumber word-coordinate read must agree on every recipient cell.
"""
from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
import re
import threading

from app.services.schedule_a_broker_roster import _fingerprint, _key, _money
from app.services.schedule_a_native_brokers import _row, labelled_single_recipient, header_named_fee_rows

_PDFIUM_LOCK = threading.Lock()  # PDFium's C API is not safe across threads.


@dataclass(frozen=True)
class ClosedSourceProof:
    source_sha256: str
    roster_sha256: str
    coverage_sha256: str


def _lines(words):
    lines = []
    for word in sorted(words, key=lambda w: (w['top'], w['x0'])):
        line = next((line for line in reversed(lines[-3:]) if abs(line[0]['top'] - word['top']) <= 2), None)
        if line is None:
            line = []; lines.append(line)
        line.append(word)
    return [sorted(line, key=lambda w: w['x0']) for line in lines]


def _label_cells(words, pattern):
    offsets, text = [], ''
    for word in words:
        offsets.append((len(text), word))
        text += word['text'] + ' '
    match = re.search(pattern, text, re.I)
    if match is None:
        return None
    label_word = next((word for offset, word in offsets if offset == match.start()), None)
    if label_word is None:
        return None
    left = ' '.join(w['text'] for w in words if w['x1'] < label_word['x0'])
    return left.strip(), text[match.end():].strip(), text.strip()


def closed_native_roster(content, pages, coverage):
    """Return a proof only for one complete closed grid, not open-ended tables."""
    if not pages or re.search(r'Schedule\s+C\b', '\n'.join(text for _,text in pages), re.I):
        return [], None
    if not coverage.get('pages') or not all(p.get('readable') for p in coverage['pages']):
        return [], None
    if all(re.search(r'Address\s+of\s+the\s+Agents', text, re.I)
           and 'Agent Number' in text for _, text in pages):
        return _closed_ruled_roster(content, coverage)
    if sum(len(re.findall(r'Agent\s+Fees\s+Paid\s+to', text, re.I)) for _,text in pages) == 1:
        rows = header_named_fee_rows(pages)
        if len(rows) != 1:
            return [], None
        row = rows[0]
        import pdfplumber
        from app.services.schedule_a_pdf_source import source_page
        with pdfplumber.open(BytesIO(content)) as document:
            physical = source_page(document.pages[row.source_page-1])
            words = physical.extract_words(x_tolerance=1,y_tolerance=2)
            lines = _lines(words)
            heading = next((line for line in lines if re.search(r'Agent\s+Fees\s+Paid\s+to', ' '.join(w['text'] for w in line), re.I)), None)
            candidates = [(line,[w for w in line if re.fullmatch(r'\$[\d,]+\.\d{2}',w['text'])])
                          for line in lines if len([w for w in line if re.fullmatch(r'\$[\d,]+\.\d{2}',w['text'])]) == 2]
            if heading is None or len(candidates) != 1 or len(document.pages) != coverage.get('page_count'):
                return [], None
            data, amounts = candidates[0]
            agent = next(w for w in heading if w['text'].casefold() == 'agent')
            premium = next((w for line in lines for w in line if w['text'].casefold() == 'premium' and w['top'] > agent['top'] and w['bottom'] < data[0]['top']),None)
            if premium is None:
                return [], None
            boundary = (premium['x1']+agent['x0'])/2
            name_words = [w for line in lines for w in line if w['top'] > agent['bottom']
                          and w['bottom'] < data[0]['top'] and w['x0'] > boundary]
            name = ' '.join(w['text'] for w in sorted(name_words,key=lambda w:(w['top'],w['x0'])))
            if amounts[-1]['x0'] <= boundary:
                return [], None
            geometric = _row(name, [], '0', _money(amounts[-1]['text']), row.source_page,
                'Independent fee-column word geometry: '+name+'; '+amounts[-1]['text'], provider='Independent PDF word-coordinate agent fee column')
            if geometric is None or _key(geometric) != _key(row):
                return [], None
            row.evidence.extend(geometric.evidence)
        digest = hashlib.sha256(json.dumps(coverage, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return rows, ClosedSourceProof(hashlib.sha256(content).hexdigest(),_fingerprint(rows),digest)
    heading = r'Name\s+and\s+Address\s+of\s+Recipient\s+of\s+Fees\s+or\s+Commissions'
    if sum(len(re.findall(heading, text, re.I)) for _, text in pages) != 1:
        return [], None
    contracts = set(re.findall(r'Contract\s+or\s+ID\s+Number:\s*([A-Z0-9/-]+)', '\n'.join(text for _,text in pages), re.I))
    if len(contracts) != 1:
        return [], None
    rows = labelled_single_recipient(pages)
    if (len(rows) != 1 or any(v.status == 'ERROR' for r in rows for v in r.validation_results)
            or not coverage.get('pages') or not all(p.get('readable') for p in coverage['pages'])):
        return [], None
    import pdfplumber
    from app.services.schedule_a_pdf_source import source_page
    reference = rows[0]
    with pdfplumber.open(BytesIO(content)) as pdf:
        if len(pdf.pages) != coverage.get('page_count'):
            return [], None
        physical = source_page(pdf.pages[reference.source_page-1])
        lines = _lines(physical.extract_words(x_tolerance=1, y_tolerance=2))
        start = next((i for i,line in enumerate(lines) if re.search(heading, ' '.join(w['text'] for w in line), re.I)), None)
        if start is None:
            return [], None
        body = lines[start+1:start+6]
        name = next((cell for line in body if (cell := _label_cells(line, r'Organization\s+Code\b'))), None)
        street = next((cell for line in body if (cell := _label_cells(line, r'Amount\s+of\s+Commissions\s+Paid\s*:'))), None)
        locality = next((cell for line in body if (cell := _label_cells(line, r'Fees\s+Paid\s*/\s*Amount\s*:?'))), None)
        if not all((name, street, locality)) or not re.fullmatch(r'[0-9]', name[1]):
            return [], None
        if not all(re.fullmatch(r'\$[\d,]+\.\d{2}', cell[1]) for cell in (street, locality)):
            return [], None
        geometric = _row(name[0], [street[0], locality[0]], _money(street[1]), _money(locality[1]), reference.source_page,
                         '\n'.join(cell[2] for cell in (name,street,locality)), provider='Independent PDF word-coordinate recipient grid')
        if geometric is None:
            return [], None
        geometric.organization_code = name[1]
        if _key(geometric) != _key(reference) or geometric.organization_code != reference.organization_code:
            return [], None
        reference.evidence.extend(geometric.evidence)
    digest = hashlib.sha256(json.dumps(coverage, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return rows, ClosedSourceProof(hashlib.sha256(content).hexdigest(), _fingerprint(rows), digest)


def _closed_ruled_roster(content, coverage):
    """Confirm every ruled cell with a separate PDFium text engine.

    PDFplumber locates the cells and decodes/total-checks their labelled roles.
    PDFium must independently return identical text for every physical cell,
    including headers and subtotals, on every page. No model answer is reused.
    """
    import pdfplumber
    import pypdfium2 as pdfium
    from app.services.schedule_a_pdf_source import source_page
    from app.services.schedule_a_native_brokers import bordered_recipient_tables
    rows = bordered_recipient_tables(content)
    if not rows:
        return [], None
    normalize = lambda text: re.sub(r'\s+', '', str(text or '')).casefold()
    try:
        with _PDFIUM_LOCK, pdfplumber.open(BytesIO(content)) as geometry:
            document = pdfium.PdfDocument(content)
            try:
                if len(document) != coverage.get('page_count'):
                    return [], None
                for index, physical in enumerate(geometry.pages):
                    tables = [table for table in source_page(physical).find_tables()
                              if any('nameandaddressoftheagents' in normalize(cell)
                                  for row in table.extract() for cell in row)]
                    if len(tables) != 1:
                        return [], None
                    page = document[index]
                    textpage = page.get_textpage()
                    try:
                        height = page.get_height()
                        table = tables[0]
                        for boxes, values in zip(table.rows, table.extract()):
                            for bounds, value in zip(boxes.cells, values):
                                if bounds is None:
                                    if value:
                                        return [], None
                                    continue
                                x0, top, x1, bottom = bounds
                                independent = textpage.get_text_bounded(x0, height-bottom, x1, height-top)
                                if normalize(independent) != normalize(value):
                                    return [], None
                    finally:
                        textpage.close()
                        page.close()
            finally:
                document.close()
    except Exception:
        return [], None
    digest = hashlib.sha256(json.dumps(coverage, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return rows, ClosedSourceProof(hashlib.sha256(content).hexdigest(), _fingerprint(rows), digest)


def proof_matches(proof, rows, coverage, source_sha256):
    return (isinstance(proof, ClosedSourceProof) and proof.source_sha256 == source_sha256
        and proof.roster_sha256 == _fingerprint(rows)
        and proof.coverage_sha256 == hashlib.sha256(json.dumps(coverage, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
