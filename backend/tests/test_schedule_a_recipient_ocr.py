"""Secondary OCR is bounded, cached, optional source evidence only."""
from pathlib import Path
import subprocess
from types import SimpleNamespace

from app.services.schedule_a_recipient_ocr import RecipientIdentityReader


def test_missing_tools_and_nonpdf_do_not_call_parser(monkeypatch):
    from app.services import schedule_a_recipient_ocr as m
    monkeypatch.setattr(m.shutil, 'which', lambda name: None)
    def forbidden(*args):
        raise AssertionError('Parser must not be invoked without pixel evidence')
    assert RecipientIdentityReader(b'%PDF-1.7', forbidden, {})(2) == []


def test_reader_uses_requested_page_lossless_image_and_caches_without_replacing_values(monkeypatch):
    from app.services import schedule_a_recipient_ocr as m
    monkeypatch.setattr(m.shutil, 'which', lambda name: name)
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        if command[0] == 'pdftoppm':
            assert command[command.index('-f') + 1] == command[command.index('-l') + 1] == '2'
            assert '-singlefile' in command and '-png' in command
            assert command[command.index('-r') + 1] == '600'
            Path(command[-1]).with_suffix('.png').write_bytes(b'raster fixture')
            return SimpleNamespace(stdout='')
        assert command[-2:] == ['--psm', '4']
        return SimpleNamespace(stdout='Actual source reader text')
    monkeypatch.setattr(m.subprocess, 'run', run)
    calls, diagnostics = [], {}
    def parse(pages):
        calls.append(pages)
        return []
    reader = RecipientIdentityReader(b'%PDF-1.7 example', parse, diagnostics)
    assert reader(2) == reader(2) == []
    assert len(commands) == 2 and calls == [[(2, 'Actual source reader text')]]
    assert diagnostics['recipient_identity_secondary_ocr'][0]['status'] == 'COMPLETED'


def test_source_failure_keeps_conflict_unresolved_and_attempts_are_bounded(monkeypatch):
    from app.services import schedule_a_recipient_ocr as m
    monkeypatch.setattr(m.shutil, 'which', lambda name: name)
    calls = []
    def fail(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, 45)
    monkeypatch.setattr(m.subprocess, 'run', fail)
    diagnostics = {}
    reader = RecipientIdentityReader(b'%PDF-1.7', lambda pages: [], diagnostics)
    for page in [0, 21, 2, 2, 3, 4]:
        assert reader(page) == []
    assert len(calls) == 2
    assert all(r['status'] == 'UNAVAILABLE' for r in diagnostics['recipient_identity_secondary_ocr'])
