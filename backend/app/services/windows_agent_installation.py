"""One-file per-user installation for the ERISAPros FT Williams agent."""

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import httpx

try:
    import winreg
except ImportError:  # pragma: no cover - the packaged agent runs on Windows
    winreg = None

from app.services.ftwilliams_local_agent_runtime import LocalAgentApiClient, pair_device
from app.services.windows_secret_store import load_secret_json, save_secret_json


TASK_NAME = "ERISAPros FT Williams Agent"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STOP_REQUEST = "update-stop.request"


class ExistingConnectionState(str, Enum):
    MISSING = "MISSING"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"


@dataclass(frozen=True)
class InstalledAgent:
    root: Path
    executable: Path
    credential: Path
    login_credential: Path
    profile: Path
    launcher: Path


def installation_paths(local_app_data: str | Path | None = None) -> InstalledAgent:
    base = Path(local_app_data or os.environ["LOCALAPPDATA"]).expanduser().resolve()
    root = base / "ERISAPros" / "FTWLocalAgent"
    return InstalledAgent(
        root=root,
        executable=root / "ERISAProsFTWAgent.exe",
        credential=root / "device.credential",
        login_credential=root / "ftw-login.credential",
        profile=root / "BrowserProfile",
        launcher=root / "start-agent.vbs",
    )


def register_startup_task(installed: InstalledAgent) -> None:
    if winreg is None:
        raise RuntimeError("Windows startup registration is only available on Windows.")
    agent_command = subprocess.list2cmdline(
        [
            str(installed.executable),
            "run",
            "--credential-file",
            str(installed.credential),
            "--login-credential-file",
            str(installed.login_credential),
            "--profile-dir",
            str(installed.profile),
        ]
    )
    escaped_command = agent_command.replace('"', '""')
    installed.launcher.write_text(
        f'CreateObject("WScript.Shell").Run "{escaped_command}", 0, False\n',
        encoding="utf-8",
    )
    windows_root = Path(os.environ.get("WINDIR", r"C:\Windows"))
    wscript = windows_root / "System32" / "wscript.exe"
    startup_command = subprocess.list2cmdline([str(wscript), "//B", str(installed.launcher)])
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, TASK_NAME, 0, winreg.REG_SZ, startup_command)
    subprocess.Popen(
        [str(wscript), "//B", str(installed.launcher)],
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        ),
        close_fds=True,
    )


def unregister_startup_task() -> None:
    """Prevent a disconnected legacy agent from relaunching during reconnect."""
    if winreg is None:
        raise RuntimeError("Windows startup registration is only available on Windows.")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, TASK_NAME)
    except FileNotFoundError:
        return


def installed_agent_processes(installed: InstalledAgent) -> list[int]:
    # Local import avoids a module cycle. The updater probe validates the exact
    # executable path and fails closed when Windows cannot expose it.
    from app.services.windows_agent_update import installed_processes

    return installed_processes(installed)


async def existing_connection_state(installed: InstalledAgent) -> ExistingConnectionState:
    """Classify a saved connection without mutating it or masking outages."""
    if not installed.credential.is_file():
        return ExistingConnectionState.MISSING
    try:
        credentials = load_secret_json(installed.credential)
        server_url = credentials["server_url"]
        device_token = credentials["device_token"]
    except (KeyError, TypeError, ValueError):
        return ExistingConnectionState.INVALID

    api = LocalAgentApiClient(server_url, device_token)
    try:
        await api.control()
        return ExistingConnectionState.ACTIVE
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            return ExistingConnectionState.REVOKED
        return ExistingConnectionState.UNAVAILABLE
    except httpx.RequestError:
        return ExistingConnectionState.UNAVAILABLE
    finally:
        await api.close()


def stop_disconnected_installation(
    installed: InstalledAgent, *, graceful_timeout_seconds: float = 5.0
) -> None:
    """Stop only the verified installed agent after its server token is revoked."""
    unregister_startup_task()
    request = installed.root / STOP_REQUEST
    request.write_text("Reconnect requested for a server-revoked device.", encoding="utf-8")
    deadline = time.monotonic() + max(0.0, graceful_timeout_seconds)
    processes = installed_agent_processes(installed)
    while processes and time.monotonic() < deadline:
        time.sleep(0.25)
        processes = installed_agent_processes(installed)
    for pid in processes:
        subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
    if processes:
        final_deadline = time.monotonic() + 5.0
        remaining = installed_agent_processes(installed)
        while remaining and time.monotonic() < final_deadline:
            time.sleep(0.25)
            remaining = installed_agent_processes(installed)
        if remaining:
            raise TimeoutError("The disconnected FT Williams Agent could not be stopped safely.")
    request.unlink(missing_ok=True)


async def install_agent(
    *,
    server_url: str,
    pairing_code: str,
    device_name: str,
    source_executable: str | Path,
    local_app_data: str | Path | None = None,
    register_startup: bool = True,
    ftw_login_credentials: dict[str, str] | None = None,
    clear_ftw_login_credentials: bool = False,
    reconnect_disconnected: bool = False,
) -> InstalledAgent:
    source = Path(source_executable).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("The FT Williams Agent setup file was not found.")
    code = pairing_code.strip()
    if not code:
        raise ValueError("A one-time ERISAPros connection code is required.")

    installed = installation_paths(local_app_data)
    paired = await pair_device(server_url, code, device_name)
    credential_committed = False
    previous_credential = (
        installed.credential.read_bytes()
        if reconnect_disconnected and installed.credential.is_file()
        else None
    )
    staged = installed.root / "install-package.pending"
    try:
        if reconnect_disconnected:
            stop_disconnected_installation(installed)
        installed.profile.mkdir(parents=True, exist_ok=True)
        if installed.executable.exists() and not reconnect_disconnected:
            subprocess.run(
                ["taskkill.exe", "/IM", installed.executable.name, "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        if source != installed.executable:
            shutil.copy2(source, staged)
            staged.replace(installed.executable)
        save_secret_json(
            installed.credential,
            {
                "server_url": server_url,
                "device_id": paired["device_id"],
                "device_token": paired["device_token"],
                "expected_account": paired["expected_account"],
            },
        )
        credential_committed = True
        if ftw_login_credentials is not None:
            save_secret_json(installed.login_credential, ftw_login_credentials)
        elif clear_ftw_login_credentials:
            installed.login_credential.unlink(missing_ok=True)
        if register_startup:
            register_startup_task(installed)
        return installed
    except Exception:
        if credential_committed and reconnect_disconnected and previous_credential is not None:
            rollback = installed.credential.with_suffix(installed.credential.suffix + ".reconnect-rollback")
            rollback.write_bytes(previous_credential)
            rollback.replace(installed.credential)
            credential_committed = False
        if not credential_committed:
            api = LocalAgentApiClient(server_url, paired["device_token"])
            try:
                await api.revoke_self()
            except Exception:
                pass
            finally:
                await api.close()
        raise
    finally:
        staged.unlink(missing_ok=True)
