"""Bounded, lossless source re-reading for a detected recipient-name conflict.

This reader never applies a name/payment/code. The conflict guard independently
checks its parsed recipient evidence and keeps unresolved/ambiguous rows held.
"""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile


class RecipientIdentityReader:
    def __init__(self, file_bytes, parse_rows, diagnostics):
        self.file_bytes = file_bytes
        self.parse_rows = parse_rows
        self.diagnostics = diagnostics
        self.cache = {}

    def __call__(self, page):
        if page in self.cache:
            return self.cache[page]
        if not isinstance(page, int) or not 1 <= page <= 20 or len(self.cache) >= 2:
            return []
        self.cache[page] = []
        renderer, ocr = shutil.which('pdftoppm'), shutil.which('tesseract')
        if not renderer or not ocr or not self.file_bytes.lstrip().startswith(b'%PDF-'):
            return []
        record = {'page': page, 'dpi': 600, 'format': 'PNG', 'psm': 4,
                  'source_sha256': hashlib.sha256(self.file_bytes).hexdigest()}
        self.diagnostics.setdefault('recipient_identity_secondary_ocr', []).append(record)
        try:
            with tempfile.TemporaryDirectory(prefix='schedule-a-recipient-') as directory:
                source = Path(directory) / 'source.pdf'
                source.write_bytes(self.file_bytes)
                prefix = Path(directory) / f'page-{page}'
                subprocess.run([renderer, '-f', str(page), '-l', str(page), '-singlefile', '-r', '600', '-scale-to', '7000', '-png', str(source), str(prefix)],
                               check=True, capture_output=True, timeout=45)
                raster = prefix.with_suffix('.png')
                record['raster_sha256'] = hashlib.sha256(raster.read_bytes()).hexdigest()
                response = subprocess.run([ocr, str(raster), 'stdout', '--psm', '4'],
                    check=True, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
                record.update(status='COMPLETED', text=response.stdout)
                self.cache[page] = self.parse_rows([(page, response.stdout)])
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            record.update(status='UNAVAILABLE', reason=type(exc).__name__)
        return self.cache[page]
