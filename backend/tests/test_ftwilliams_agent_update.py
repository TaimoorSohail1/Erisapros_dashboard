import hashlib
import json
import os
from pathlib import Path

import pytest

from app.services import windows_agent_update as updater
from app.services.windows_agent_installation import installation_paths

REAL_PROCESS_PROBE = updater.installed_processes
REAL_SIGNATURE_PROBE = updater.package_signature


@pytest.fixture
def installation(tmp_path, monkeypatch):
    local = tmp_path / 'LocalAppData'
    installed = installation_paths(local)
    installed.root.mkdir(parents=True)
    installed.executable.write_bytes(b'MZold-agent')
    installed.credential.write_bytes(b'opaque-protected-device')
    installed.login_credential.write_bytes(b'opaque-protected-login')
    installed.launcher.write_bytes(b'existing-startup-launcher')
    installed.profile.mkdir()
    (installed.profile / 'Cookies').write_bytes(b'unchanged-profile')
    source = tmp_path / 'download.exe'
    source.write_bytes(b'MZnew-agent')
    monkeypatch.setattr(updater, 'package_signature', lambda _path: 'NotSigned')
    monkeypatch.setattr(updater, 'installed_processes', lambda _installed: [])
    return local, installed, source


def update(installation, **kwargs):
    local, _, source = installation
    return updater.update_agent(
        source_executable=source,
        expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        local_app_data=local,
        allow_unsigned=True,
        restart=False,
        **kwargs,
    )


def test_update_preserves_connection_login_profile_and_startup(installation, monkeypatch):
    _, installed, source = installation
    protected = {p: p.read_bytes() for p in (
        installed.credential, installed.login_credential, installed.launcher,
        installed.profile / 'Cookies',
    )}
    result = update(installation)
    assert installed.executable.read_bytes() == source.read_bytes()
    assert all(p.read_bytes() == content for p, content in protected.items())
    assert (result.backup / installed.executable.name).read_bytes() == b'MZold-agent'
    assert (result.backup / installed.credential.name).read_bytes() == protected[installed.credential]
    assert not (result.backup / 'BrowserProfile').exists()
    manifest = json.loads((result.backup / 'manifest.json').read_text())
    assert manifest['previous_sha256'] == hashlib.sha256(b'MZold-agent').hexdigest()
    assert not (installed.root / updater.STOP_REQUEST).exists()


def test_unsigned_requires_separate_explicit_opt_in(installation):
    local, installed, source = installation
    with pytest.raises(ValueError, match='unsigned'):
        updater.update_agent(source_executable=source, expected_sha256=updater.file_hash(source),
                             local_app_data=local, restart=False)
    assert installed.executable.read_bytes() == b'MZold-agent'
    assert not (installed.root / updater.STOP_REQUEST).exists()


@pytest.mark.parametrize('digest', ['', 'bad', 'a' * 64])
def test_bad_or_missing_checksum_changes_nothing(installation, digest):
    local, installed, source = installation
    with pytest.raises(ValueError, match='checksum'):
        updater.update_agent(source_executable=source, expected_sha256=digest,
                             local_app_data=local, allow_unsigned=True, restart=False)
    assert installed.executable.read_bytes() == b'MZold-agent'


def test_running_legacy_agent_is_not_killed_or_overwritten(installation, monkeypatch):
    _, installed, _ = installation
    monkeypatch.setattr(updater, 'installed_processes', lambda _installed: [123])
    with pytest.raises(TimeoutError, match='still running'):
        update(installation, stop_timeout_seconds=0)
    assert installed.executable.read_bytes() == b'MZold-agent'
    assert not (installed.root / updater.STOP_REQUEST).exists()


def test_active_new_agent_can_drain_before_replacement(installation, monkeypatch):
    _, installed, _ = installation
    checks = []
    def processes(_installed):
        checks.append((installed.root / updater.STOP_REQUEST).exists())
        return [123] if len(checks) == 1 else []
    monkeypatch.setattr(updater, 'installed_processes', processes)
    monkeypatch.setattr(updater.time, 'sleep', lambda _seconds: None)
    update(installation)
    assert checks[0] is True
    assert len(checks) >= 2


def test_missing_connection_does_not_pair_or_install(installation):
    _, installed, _ = installation
    installed.credential.unlink()
    with pytest.raises(ValueError, match='existing connection'):
        update(installation)
    assert installed.executable.read_bytes() == b'MZold-agent'


def test_login_optional_and_backup_retains_original_bytes(installation):
    _, installed, _ = installation
    installed.login_credential.unlink()
    result = update(installation)
    assert not installed.login_credential.exists()
    assert not (result.backup / installed.login_credential.name).exists()


def test_failed_replacement_leaves_original_and_cleans_request(installation, monkeypatch):
    _, installed, _ = installation
    original = Path.replace
    def fail_stage(path, target):
        if path.name == updater.STAGED_PACKAGE:
            raise PermissionError('locked')
        return original(path, target)
    monkeypatch.setattr(Path, 'replace', fail_stage)
    with pytest.raises(PermissionError):
        update(installation)
    assert installed.executable.read_bytes() == b'MZold-agent'
    assert not (installed.root / updater.STOP_REQUEST).exists()


def test_start_failure_rolls_back_executable(installation, monkeypatch):
    local, installed, source = installation
    monkeypatch.setattr(updater, 'start_existing_agent',
                        lambda _installed: (_ for _ in ()).throw(OSError('startup failed')))
    with pytest.raises(OSError, match='startup failed'):
        updater.update_agent(source_executable=source, expected_sha256=updater.file_hash(source),
                             local_app_data=local, allow_unsigned=True)
    assert installed.executable.read_bytes() == b'MZold-agent'
    assert installed.credential.read_bytes() == b'opaque-protected-device'


def test_interrupted_update_requires_explicit_recovery(installation):
    _, installed, _ = installation
    (installed.root / updater.STOP_REQUEST).write_text('interrupted')
    with pytest.raises(RuntimeError, match='recovery'):
        update(installation)
    assert (installed.root / updater.STOP_REQUEST).exists()
    assert installed.executable.read_bytes() == b'MZold-agent'


def test_rollback_uses_verified_backup_without_touching_credentials(installation):
    local, installed, _ = installation
    result = update(installation)
    updater.rollback_agent(result.backup, local_app_data=local, restart=False)
    assert installed.executable.read_bytes() == b'MZold-agent'
    assert installed.credential.read_bytes() == b'opaque-protected-device'


def test_rollback_rejects_modified_backup(installation):
    local, installed, _ = installation
    result = update(installation)
    (result.backup / installed.executable.name).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='checksum'):
        updater.rollback_agent(result.backup, local_app_data=local, restart=False)
    assert installed.executable.read_bytes() == b'MZnew-agent'


def test_two_updaters_cannot_replace_same_installation(installation):
    _, installed, _ = installation
    with updater.exclusive_file_lock(installed.root / updater.UPDATE_LOCK):
        with pytest.raises(RuntimeError, match='already'):
            update(installation)
    assert installed.executable.read_bytes() == b'MZold-agent'


def test_cli_update_does_not_require_pairing_code_or_login():
    from scripts.run_ftw_local_agent import parse_args
    args = parse_args(['update', '--expected-sha256', 'a' * 64, '--allow-unsigned'])
    assert args.command == 'update'
    assert args.allow_unsigned is True
    assert not hasattr(args, 'pairing_code')


def test_cli_rollback_requires_named_backup():
    from scripts.run_ftw_local_agent import parse_args
    args = parse_args(['rollback', '--backup-directory', 'backup'])
    assert args.backup_directory == 'backup'


def test_rollback_does_not_accept_other_installation_directory(installation, tmp_path):
    local, installed, _ = installation
    with pytest.raises(ValueError, match='belonging'):
        updater.rollback_agent(tmp_path / 'other', local_app_data=local, restart=False)
    assert installed.executable.read_bytes() == b'MZold-agent'


def test_windows_process_probe_filters_exact_path_without_killing(installation, monkeypatch):
    _, installed, _ = installation
    calls = []
    def powershell(script, **environment):
        calls.append((script, environment))
        return '123\n456'
    monkeypatch.setattr(updater, '_powershell', powershell)
    assert REAL_PROCESS_PROBE(installed) == [123, 456]
    assert calls[0][1]['FTW_UPDATE_IMAGE_PATH'] == str(installed.executable)
    assert 'taskkill' not in calls[0][0].lower()
    assert 'Unable to verify' in calls[0][0]


def test_update_refuses_tampered_signature_even_with_unsigned_opt_in(installation, monkeypatch):
    monkeypatch.setattr(updater, 'package_signature', lambda _path: 'HashMismatch')
    with pytest.raises(ValueError, match='signature'):
        update(installation)


def test_runtime_lock_blocks_replacement_after_process_probe(installation):
    _, installed, _ = installation
    with updater.exclusive_file_lock(installed.root / updater.RUNTIME_LOCK):
        with pytest.raises(RuntimeError, match='already'):
            update(installation)
    assert installed.executable.read_bytes() == b'MZold-agent'


def test_actual_windows_probe_leaves_unrelated_browser_processes_alone(installation):
    # Fixture executable name is installed under a temporary path, never the user's live agent.
    _, installed, _ = installation
    assert REAL_PROCESS_PROBE(installed) == []


def test_actual_windows_signature_probe_sees_unsigned_fixture(installation):
    _, _, source = installation
    # Invalid PE bytes must fail closed, not be accepted as an unsigned valid package.
    assert REAL_SIGNATURE_PROBE(source) != 'Valid'


def test_windows_signature_helper_can_load_its_own_system_security_module():
    system_file = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'System32/wscript.exe'
    assert REAL_SIGNATURE_PROBE(system_file) in {'Valid', 'NotTrusted', 'UnknownError', 'NotSigned'}


def test_update_keeps_dpapi_credentials_decryptable_for_same_windows_user(installation):
    from app.services.windows_secret_store import load_secret_json, save_secret_json
    _, installed, _ = installation
    device = {'device_id': 'synthetic-device', 'device_token': 'synthetic-token',
              'server_url': 'https://synthetic.example', 'expected_account': 'Synthetic'}
    login = {'company_code': 'synthetic', 'username': 'fixture', 'password': 'synthetic-password'}
    save_secret_json(installed.credential, device)
    save_secret_json(installed.login_credential, login)
    original = installed.credential.read_bytes(), installed.login_credential.read_bytes()
    update(installation)
    assert (installed.credential.read_bytes(), installed.login_credential.read_bytes()) == original
    assert load_secret_json(installed.credential) == device
    assert load_secret_json(installed.login_credential) == login


def test_source_change_during_staging_cannot_install_unverified_package(installation, monkeypatch):
    _, installed, source = installation
    copy = updater.shutil.copy2
    def corrupt(src, dst):
        result = copy(src, dst)
        if Path(dst).name == updater.STAGED_PACKAGE:
            Path(dst).write_bytes(b'changed-package')
        return result
    monkeypatch.setattr(updater.shutil, 'copy2', corrupt)
    with pytest.raises(ValueError, match='changed'):
        update(installation)
    assert installed.executable.read_bytes() == b'MZold-agent'


def test_protected_file_change_aborts_instead_of_restoring_stale_credentials(installation, monkeypatch):
    _, installed, _ = installation
    copy = updater.shutil.copy2
    def concurrent_change(src, dst):
        result = copy(src, dst)
        if Path(dst).name == updater.STAGED_PACKAGE:
            installed.credential.write_bytes(b'concurrent-protected-change')
        return result
    monkeypatch.setattr(updater.shutil, 'copy2', concurrent_change)
    with pytest.raises(ValueError, match='changed'):
        update(installation)
    assert installed.executable.read_bytes() == b'MZold-agent'
    assert installed.credential.read_bytes() == b'concurrent-protected-change'


def test_explicit_rollback_recovers_after_interruption(installation):
    local, installed, _ = installation
    result = update(installation)
    (installed.root / updater.STOP_REQUEST).write_text('interrupted')
    updater.rollback_agent(result.backup, local_app_data=local, restart=False)
    assert installed.executable.read_bytes() == b'MZold-agent'
    assert not (installed.root / updater.STOP_REQUEST).exists()


def test_rollback_timeout_keeps_fail_closed_recovery_marker(installation, monkeypatch):
    local, installed, _ = installation
    result = update(installation)
    monkeypatch.setattr(updater, 'installed_processes', lambda _installed: [123])
    with pytest.raises(TimeoutError):
        updater.rollback_agent(result.backup, local_app_data=local, restart=False,
                               stop_timeout_seconds=0)
    assert installed.executable.read_bytes() == b'MZnew-agent'
    assert (installed.root / updater.STOP_REQUEST).exists()


def test_existing_setup_does_not_prompt_or_repair_connection(installation, monkeypatch, capsys):
    import asyncio
    from scripts import run_ftw_local_agent as cli
    local, installed, _ = installation
    monkeypatch.setenv('LOCALAPPDATA', str(local))
    monkeypatch.setattr(cli, 'parse_args', lambda: cli.argparse.Namespace(command='install', pairing_code=''))
    monkeypatch.setattr('builtins.input', lambda _prompt: (_ for _ in ()).throw(AssertionError('unexpected prompt')))
    assert asyncio.run(cli.main()) == 2
    assert 'existing connection' in capsys.readouterr().err.lower()
    assert installed.credential.read_bytes() == b'opaque-protected-device'


def test_cli_update_routes_to_updater_without_loading_or_repairing_secrets(installation, monkeypatch):
    import asyncio
    from scripts import run_ftw_local_agent as cli
    local, _, source = installation
    args = cli.parse_args(['update', '--expected-sha256', updater.file_hash(source),
                           '--allow-unsigned', '--no-startup'])
    monkeypatch.setenv('LOCALAPPDATA', str(local))
    monkeypatch.setattr(cli, 'parse_args', lambda: args)
    monkeypatch.setattr(cli.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(cli.sys, 'executable', str(source))
    monkeypatch.setattr(cli, 'load_secret_json', lambda _path: (_ for _ in ()).throw(AssertionError('credential read')))
    async def no_pair(*_args, **_kwargs):
        raise AssertionError('unexpected pairing')
    monkeypatch.setattr(cli, 'pair_device', no_pair)
    assert asyncio.run(cli.main()) == 0


def test_runtime_startup_respects_interrupted_maintenance_without_secret_reads(installation, monkeypatch):
    import asyncio
    from scripts import run_ftw_local_agent as cli
    _, installed, _ = installation
    args = cli.parse_args(['run', '--credential-file', str(installed.credential),
                           '--profile-dir', str(installed.profile)])
    monkeypatch.setattr(cli, 'parse_args', lambda: args)
    monkeypatch.setattr(cli, 'load_secret_json', lambda _path: (_ for _ in ()).throw(AssertionError('secret read')))
    (installed.root / updater.STOP_REQUEST).write_text('interrupted')
    assert asyncio.run(cli.main()) == 0


def test_runtime_startup_rejects_duplicate_before_secret_reads(installation, monkeypatch):
    import asyncio
    from scripts import run_ftw_local_agent as cli
    _, installed, _ = installation
    args = cli.parse_args(['run', '--credential-file', str(installed.credential),
                           '--profile-dir', str(installed.profile)])
    monkeypatch.setattr(cli, 'parse_args', lambda: args)
    monkeypatch.setattr(cli, 'load_secret_json', lambda _path: (_ for _ in ()).throw(AssertionError('secret read')))
    with updater.exclusive_file_lock(installed.root / updater.RUNTIME_LOCK):
        assert asyncio.run(cli.main()) == 1
