"""Exercise a packaged update in an isolated synthetic Windows installation only."""

import argparse
import json
import marshal
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.windows_agent_installation import installation_paths
from app.services.windows_agent_update import file_hash
from app.services.windows_secret_store import load_secret_json, save_secret_json
from app.services.ftwilliams_local_agent_runtime import AGENT_VERSION


def verify_embedded_sources(package: Path) -> None:
    """Catch edits made during a long build: compare actual packaged code, not timestamps."""
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(package))
    pyz = archive.open_embedded_archive('PYZ.pyz')
    def normalized(code):
        return code.replace(co_filename='<verified>', co_consts=tuple(
            normalized(value) if isinstance(value, types.CodeType) else value
            for value in code.co_consts
        ))
    files = {
        'app.services.windows_agent_update': BACKEND_ROOT / 'app/services/windows_agent_update.py',
        'app.services.windows_agent_subprocess': BACKEND_ROOT / 'app/services/windows_agent_subprocess.py',
        'app.services.windows_agent_installation': BACKEND_ROOT / 'app/services/windows_agent_installation.py',
        'app.services.ftwilliams_local_agent': BACKEND_ROOT / 'app/services/ftwilliams_local_agent.py',
        'app.services.ftwilliams_local_agent_runtime': BACKEND_ROOT / 'app/services/ftwilliams_local_agent_runtime.py',
    }
    for module, source in files.items():
        expected = compile(source.read_bytes(), str(source), 'exec', optimize=0)
        assert normalized(pyz.extract(module)) == normalized(expected), f'Stale bundled module: {module}'
    entry = BACKEND_ROOT / 'scripts/run_ftw_local_agent.py'
    assert normalized(marshal.loads(archive.extract('run_ftw_local_agent'))) == normalized(
        compile(entry.read_bytes(), str(entry), 'exec', optimize=0)
    ), 'Stale bundled entry point'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    args = parser.parse_args()
    package = args.package.resolve()
    verify_embedded_sources(package)
    with tempfile.TemporaryDirectory(prefix='erisapros-synthetic-update-') as directory:
        local = Path(directory) / 'LocalAppData'
        installed = installation_paths(local)
        installed.root.mkdir(parents=True)
        installed.executable.write_bytes(b'MZsynthetic-previous-executable')
        device = {'device_id': 'synthetic-device', 'device_token': 'synthetic-token',
                  'server_url': 'https://synthetic.invalid', 'expected_account': 'Synthetic'}
        login = {'company_code': 'synthetic', 'username': 'fixture', 'password': 'synthetic-only'}
        save_secret_json(installed.credential, device)
        save_secret_json(installed.login_credential, login)
        installed.launcher.write_text('synthetic launcher -- never executed', encoding='utf-8')
        installed.profile.mkdir()
        cookie = installed.profile / 'Cookies'
        cookie.write_bytes(b'synthetic cookie fixture')
        preserved = {p: file_hash(p) for p in (
            installed.credential, installed.login_credential, installed.launcher, cookie,
        )}
        environment = {**os.environ, 'LOCALAPPDATA': str(local)}
        def run(*arguments):
            result = subprocess.run([str(package), *arguments], env=environment,
                                    capture_output=True, text=True, timeout=180,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise RuntimeError(f'Synthetic packaged {arguments[0]} failed: {result.stdout}\n{result.stderr}')
            return result.stdout
        version = run('--version').strip()
        assert version.endswith(AGENT_VERSION), version
        help_text = run('update', '--help')
        assert '--expected-sha256' in help_text and '--allow-unsigned' in help_text
        run('update', '--expected-sha256', file_hash(package), '--allow-unsigned', '--no-startup')
        assert file_hash(installed.executable) == file_hash(package)
        assert all(file_hash(p) == digest for p, digest in preserved.items())
        assert load_secret_json(installed.credential) == device
        assert load_secret_json(installed.login_credential) == login
        backups = list((installed.root.parent / 'FTWLocalAgentBackups').glob('update-*'))
        assert len(backups) == 1
        run('rollback', '--backup-directory', str(backups[0]), '--no-startup')
        assert installed.executable.read_bytes() == b'MZsynthetic-previous-executable'
        assert all(file_hash(p) == digest for p, digest in preserved.items())
        print(json.dumps({'version': version, 'embedded_sources': 'match', 'isolated_update': 'passed',
                          'dpapi_preservation': 'passed', 'profile_preservation': 'passed',
                          'packaged_rollback': 'passed', 'startup_executed': False,
                          'real_credentials_or_vendor_requests': False}))


if __name__ == '__main__':
    main()
