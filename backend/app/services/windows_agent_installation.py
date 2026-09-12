"""One-file per-user installation for the ERISAPros FT Williams agent."""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.services.ftwilliams_local_agent_runtime import pair_device
from app.services.windows_secret_store import save_secret_json


TASK_NAME = "ERISAPros FT Williams Agent"


@dataclass(frozen=True)
class InstalledAgent:
    root: Path
    executable: Path
    credential: Path
    profile: Path


def installation_paths(local_app_data: str | Path | None = None) -> InstalledAgent:
    base = Path(local_app_data or os.environ["LOCALAPPDATA"]).expanduser().resolve()
    root = base / "ERISAPros" / "FTWLocalAgent"
    return InstalledAgent(
        root=root,
        executable=root / "ERISAProsFTWAgent.exe",
        credential=root / "device.credential",
        profile=root / "BrowserProfile",
    )


def register_startup_task(installed: InstalledAgent) -> None:
    task_command = subprocess.list2cmdline(
        [
            str(installed.executable),
            "run",
            "--credential-file",
            str(installed.credential),
            "--profile-dir",
            str(installed.profile),
        ]
    )
    subprocess.run(
        [
            "schtasks.exe",
            "/Create",
            "/TN",
            TASK_NAME,
            "/TR",
            task_command,
            "/SC",
            "ONLOGON",
            "/F",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["schtasks.exe", "/Run", "/TN", TASK_NAME],
        check=True,
        capture_output=True,
        text=True,
    )


async def install_agent(
    *,
    server_url: str,
    pairing_code: str,
    device_name: str,
    source_executable: str | Path,
    local_app_data: str | Path | None = None,
    register_startup: bool = True,
) -> InstalledAgent:
    source = Path(source_executable).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("The FT Williams Agent setup file was not found.")
    code = pairing_code.strip()
    if not code:
        raise ValueError("A one-time ERISAPros connection code is required.")

    paired = await pair_device(server_url, code, device_name)
    installed = installation_paths(local_app_data)
    if register_startup:
        subprocess.run(
            ["schtasks.exe", "/End", "/TN", TASK_NAME],
            check=False,
            capture_output=True,
            text=True,
        )
    installed.profile.mkdir(parents=True, exist_ok=True)
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
    if register_startup:
        register_startup_task(installed)
    return installed
