"""One-file per-user installation for the ERISAPros FT Williams agent."""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover - the packaged agent runs on Windows
    winreg = None

from app.services.ftwilliams_local_agent_runtime import pair_device
from app.services.windows_secret_store import save_secret_json


TASK_NAME = "ERISAPros FT Williams Agent"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


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
) -> InstalledAgent:
    source = Path(source_executable).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("The FT Williams Agent setup file was not found.")
    code = pairing_code.strip()
    if not code:
        raise ValueError("A one-time ERISAPros connection code is required.")

    paired = await pair_device(server_url, code, device_name)
    installed = installation_paths(local_app_data)
    installed.profile.mkdir(parents=True, exist_ok=True)
    if installed.executable.exists():
        subprocess.run(
            ["taskkill.exe", "/IM", installed.executable.name, "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
    if source != installed.executable:
        shutil.copy2(source, installed.executable)
    save_secret_json(
        installed.credential,
        {
            "server_url": server_url,
            "device_id": paired["device_id"],
            "device_token": paired["device_token"],
            "expected_account": paired["expected_account"],
        },
    )
    if ftw_login_credentials is not None:
        save_secret_json(installed.login_credential, ftw_login_credentials)
    elif clear_ftw_login_credentials:
        installed.login_credential.unlink(missing_ok=True)
    if register_startup:
        register_startup_task(installed)
    return installed
