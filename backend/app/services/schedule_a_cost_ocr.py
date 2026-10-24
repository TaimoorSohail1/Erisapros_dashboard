"""Bounded lossless source read for otherwise unreadable cost cells only."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile


def read_lossless_cost_source_pages(file_bytes, pages):
    renderer, ocr = shutil.which('pdftoppm'), shutil.which('tesseract')
    if not renderer or not ocr or not file_bytes:
        return []
    selected = sorted({page for page in pages if type(page) is int and 1 <= page <= 20})[:3]
    if not selected:
        return []
    source_hash = hashlib.sha256(file_bytes).hexdigest()
    reads = []
    with tempfile.TemporaryDirectory(prefix='schedule-a-cost-source-') as temporary:
        source = Path(temporary) / 'source.pdf'
        source.write_bytes(file_bytes)
        for page in selected:
            prefix = Path(temporary) / f'cost-{page}'
            record = {'page': page, 'psm': 6, 'ocr_format': 'png', 'dpi': 400,
                      'source_sha256': source_hash, 'text': ''}
            try:
                subprocess.run([renderer, '-f', str(page), '-l', str(page), '-singlefile', '-png',
                                '-r', '400', str(source), str(prefix)], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
                response = subprocess.run([ocr, str(prefix.with_suffix('.png')), 'stdout', '--psm', '6'],
                    check=True, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
                record['text'] = (response.stdout or '').strip()
            except (OSError, subprocess.SubprocessError) as exc:
                record['error_type'] = type(exc).__name__
            reads.append(record)
    return reads
