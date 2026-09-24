"""Explicit per-user agent updates; never re-pair, decrypt secrets, or force-kill work."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.services.windows_agent_installation import InstalledAgent, installation_paths
from app.services.windows_agent_subprocess import windows_system_environment

STOP_REQUEST = 'update-stop.request'
UPDATE_LOCK = 'update.lock'
RUNTIME_LOCK = 'runtime.lock'
STAGED_PACKAGE = 'update-package.pending'


@dataclass(frozen=True)
class AgentUpdateResult:
    installed: InstalledAgent
    backup: Path


def file_hash(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


@contextmanager
def exclusive_file_lock(path: Path):
    """OS lock automatically releases on crashes; the lock file is not a PID claim."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('a+b')
    locked = False
    try:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('An agent or updater is already using this installation.') from exc
        locked = True
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()


def _powershell(script: str, **environment: str) -> str:
    if os.name != 'nt':
        raise RuntimeError('Agent package validation and process inspection require Windows.')
    powershell = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    # A PowerShell 7 host can pass its incompatible Security module to Windows
    # PowerShell 5.1. These helpers need only the trusted OS's built-in modules.
    with windows_system_environment({**os.environ, **environment,
                                     'PSModulePath': str(powershell.parent / 'Modules')}) as child:
        try:
            result = subprocess.run(
                [str(powershell), '-NoProfile', '-NonInteractive', '-Command', script],
                env=child, capture_output=True, text=True,
                timeout=30, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f'Windows validation failed: {(exc.stderr or "system helper failed").strip()[:1000]}') from exc
    return result.stdout.strip()


def package_signature(path: Path) -> str:
    return _powershell(
        "$ErrorActionPreference='Stop'; (Get-AuthenticodeSignature -LiteralPath $env:FTW_UPDATE_PACKAGE).Status.ToString()",
        FTW_UPDATE_PACKAGE=str(path),
    )


def installed_processes(installed: InstalledAgent) -> list[int]:
    # Never act on arbitrary matching names: inaccessible same-name paths fail closed.
    output = _powershell(
        "$ErrorActionPreference='Stop'; $hits=@(Get-CimInstance Win32_Process | "
        "Where-Object {$_.Name -eq $env:FTW_UPDATE_IMAGE_NAME}); "
        "foreach ($p in $hits) { if (-not $p.ExecutablePath) {throw 'Unable to verify agent process path'}; "
        "if ($p.ExecutablePath -eq $env:FTW_UPDATE_IMAGE_PATH) {$p.ProcessId} }",
        FTW_UPDATE_IMAGE_NAME=installed.executable.name,
        FTW_UPDATE_IMAGE_PATH=str(installed.executable),
    )
    return [int(line) for line in output.splitlines() if line.strip()]


def start_existing_agent(installed: InstalledAgent) -> None:
    # Launch the preserved startup script, without changing the registry or pairing.
    if not installed.launcher.is_file():
        raise ValueError('Existing startup launcher is missing; repair is required before update.')
    wscript = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'System32/wscript.exe'
    with windows_system_environment({**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'}) as child:
        subprocess.Popen(
            [str(wscript), '//B', str(installed.launcher)], close_fds=True, env=child,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'DETACHED_PROCESS', 0),
        )


def _snapshot(installed: InstalledAgent) -> dict[str, str]:
    return {path.name: file_hash(path) for path in (
        installed.credential, installed.login_credential, installed.launcher,
    ) if path.is_file()}


def _wait_until_stopped(installed: InstalledAgent, timeout: float) -> None:
    deadline = time.monotonic() + max(0, timeout)
    while installed_processes(installed):
        if time.monotonic() >= deadline:
            raise TimeoutError(
                'The installed agent is still running. No process was killed or package replaced. '
                'Older agents require a confirmed idle, controlled stop before update; '
                'newer agents finish active work and acknowledge its result before exiting.'
            )
        time.sleep(0.5)


def _backup(installed: InstalledAgent, candidate_hash: str) -> Path:
    backup = installed.root.parent / 'FTWLocalAgentBackups' / f'update-{uuid.uuid4().hex}'
    backup.mkdir(parents=True)
    files = [installed.executable, installed.credential, installed.login_credential, installed.launcher]
    hashes = {}
    for path in files:
        if path.is_file():
            shutil.copy2(path, backup / path.name)
            digest = file_hash(path)
            if file_hash(backup / path.name) != digest:
                raise OSError('Backup checksum verification failed; update was not installed.')
            hashes[path.name] = digest
    manifest = {
        'installation': str(installed.root.resolve()),
        'previous_sha256': hashes[installed.executable.name],
        'candidate_sha256': candidate_hash,
        'files': hashes,
    }
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return backup


def update_agent(
    *, source_executable: str | Path, expected_sha256: str,
    local_app_data: str | Path | None = None, allow_unsigned: bool = False,
    restart: bool = True, stop_timeout_seconds: float = 120,
) -> AgentUpdateResult:
    source = Path(source_executable).expanduser().resolve()
    installed = installation_paths(local_app_data)
    checksum = str(expected_sha256).strip().lower()
    if not re.fullmatch(r'[0-9a-f]{64}', checksum) or file_hash(source) != checksum:
        raise ValueError('The package checksum is missing, invalid, or does not match the approved release.')
    if source == installed.executable.resolve() or installed.root.resolve() in source.parents:
        raise ValueError('Run the updater from a separate downloaded package, not the installed directory.')
    if not installed.executable.is_file() or not installed.credential.is_file():
        raise ValueError('An existing connection and installed executable are required; use setup for a new computer.')
    if restart and not installed.launcher.is_file():
        raise ValueError('Existing startup launcher is missing; repair is required before update.')
    status = package_signature(source)
    if status != 'Valid' and not (allow_unsigned and status == 'NotSigned'):
        raise ValueError('The package must have a valid signature; unsigned restricted testing requires explicit opt-in.')
    request = installed.root / STOP_REQUEST
    with exclusive_file_lock(installed.root / UPDATE_LOCK):
        if request.exists():
            raise RuntimeError('An interrupted update requires explicit rollback/recovery before another update.')
        original = _snapshot(installed)
        # Read-only backup precedes the drain marker: every interrupted drain has
        # a complete recovery reference. Never copy the active browser profile.
        backup = _backup(installed, checksum)
        replaced = False
        try:
            request.write_text(json.dumps({'backup_directory': str(backup),
                                          'message': 'Finish active work and acknowledge its result; stop new claims.'}),
                               encoding='utf-8')
            _wait_until_stopped(installed, stop_timeout_seconds)
            with exclusive_file_lock(installed.root / RUNTIME_LOCK):
                if installed_processes(installed):
                    raise RuntimeError('An agent restarted during update; no executable was replaced.')
                staged = installed.root / STAGED_PACKAGE
                shutil.copy2(source, staged)
                if file_hash(staged) != checksum or _snapshot(installed) != original:
                    raise ValueError('Package or protected installation changed during staging; update aborted.')
                staged.replace(installed.executable)
                replaced = True
                request.unlink()
            if restart:
                start_existing_agent(installed)
            return AgentUpdateResult(installed, backup)
        except Exception:
            if replaced and backup is not None:
                # Fail closed if a process did launch: never overwrite a live executable.
                with exclusive_file_lock(installed.root / RUNTIME_LOCK):
                    if installed_processes(installed):
                        raise RuntimeError('Update startup needs inspection; agent is running. Verified backup retained.')
                    _restore_executable(installed, backup)
            request.unlink(missing_ok=True)
            raise
        finally:
            (installed.root / STAGED_PACKAGE).unlink(missing_ok=True)


def _validated_backup(installed: InstalledAgent, backup: Path) -> dict:
    directory = (installed.root.parent / 'FTWLocalAgentBackups').resolve()
    if backup.parent != directory or not backup.name.startswith('update-'):
        raise ValueError('Rollback must use a backup belonging to this installation.')
    manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('installation') != str(installed.root.resolve()):
        raise ValueError('Rollback backup belongs to a different installation.')
    if file_hash(backup / installed.executable.name) != manifest.get('previous_sha256'):
        raise ValueError('Rollback executable checksum does not match its backup manifest.')
    return manifest


def _restore_executable(installed: InstalledAgent, backup: Path) -> None:
    manifest = _validated_backup(installed, backup)
    staged = installed.root / 'rollback-package.pending'
    try:
        shutil.copy2(backup / installed.executable.name, staged)
        if file_hash(staged) != manifest['previous_sha256']:
            raise ValueError('Rollback staging checksum failed.')
        staged.replace(installed.executable)
    finally:
        staged.unlink(missing_ok=True)


def rollback_agent(
    backup_directory: str | Path, *, local_app_data: str | Path | None = None,
    restart: bool = True, stop_timeout_seconds: float = 120,
) -> InstalledAgent:
    installed = installation_paths(local_app_data)
    backup = Path(backup_directory).expanduser().resolve()
    _validated_backup(installed, backup)
    with exclusive_file_lock(installed.root / UPDATE_LOCK):
        request = installed.root / STOP_REQUEST
        request.write_text('Stop safely for explicit update recovery.', encoding='utf-8')
        # On a recovery failure keep the marker: do not silently start an ambiguous runtime.
        _wait_until_stopped(installed, stop_timeout_seconds)
        with exclusive_file_lock(installed.root / RUNTIME_LOCK):
            if installed_processes(installed):
                raise RuntimeError('An agent restarted during recovery; executable was not restored.')
            _restore_executable(installed, backup)
            request.unlink()
        if restart:
            start_existing_agent(installed)
    return installed
