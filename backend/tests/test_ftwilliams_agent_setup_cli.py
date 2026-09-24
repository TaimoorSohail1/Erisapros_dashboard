import asyncio
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from app.services import windows_agent_installation
from app.services.windows_agent_installation import (
    ExistingConnectionState,
    InstalledAgent,
    existing_connection_state,
    install_agent,
    stop_disconnected_installation,
)
from app.services.windows_secret_store import load_secret_json
from scripts.run_ftw_local_agent import collect_ftw_login_credentials, parse_args


def test_double_clicking_the_agent_defaults_to_client_setup():
    args = parse_args([])

    assert args.command == "install"
    assert args.server_url == "https://d3axcdlq9aydpw.cloudfront.net"
    assert args.pairing_code == ""


def test_existing_pair_run_and_unpair_commands_remain_available():
    pair = parse_args(
        [
            "pair",
            "--server-url",
            "https://dashboard.example.com",
            "--pairing-code",
            "one-time",
            "--credential-file",
            "device.credential",
        ]
    )
    run = parse_args(["run", "--credential-file", "device.credential", "--profile-dir", "profile"])
    unpair = parse_args(["unpair", "--credential-file", "device.credential"])

    assert pair.command == "pair"
    assert run.command == "run"
    assert unpair.command == "unpair"


def test_startup_registration_uses_current_user_registry_without_admin(tmp_path, monkeypatch):
    installed = InstalledAgent(
        root=tmp_path,
        executable=tmp_path / "ERISAProsFTWAgent.exe",
        credential=tmp_path / "device.credential",
        login_credential=tmp_path / "ftw-login.credential",
        profile=tmp_path / "BrowserProfile",
        launcher=tmp_path / "start-agent.vbs",
    )
    installed.executable.write_bytes(b"agent")
    registry_values = {}

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(windows_agent_installation.winreg, "CreateKey", lambda *_args: FakeKey())
    monkeypatch.setattr(
        windows_agent_installation.winreg,
        "SetValueEx",
        lambda _key, name, _reserved, _kind, value: registry_values.__setitem__(name, value),
    )
    launched = []
    monkeypatch.setattr(
        windows_agent_installation.subprocess,
        "Popen",
        lambda command, **kwargs: launched.append((command, kwargs)),
    )
    monkeypatch.setattr(
        windows_agent_installation.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, "schtasks.exe", stderr="Access is denied.")
        ),
    )

    windows_agent_installation.register_startup_task(installed)

    assert installed.launcher.exists()
    launcher_text = installed.launcher.read_text(encoding="utf-8")
    assert "ERISAProsFTWAgent.exe" in launcher_text
    assert "device.credential" in launcher_text
    assert "ftw-login.credential" in launcher_text
    assert "BrowserProfile" in launcher_text
    assert "wscript.exe //B" in registry_values["ERISAPros FT Williams Agent"]
    assert "start-agent.vbs" in registry_values["ERISAPros FT Williams Agent"]
    assert launched[0][0][0].lower().endswith("wscript.exe")


class PairingHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        assert self.path == "/api/ftwilliams/local-agent/pair"
        assert payload["pairing_code"] == "fresh-code"
        body = json.dumps(
            {
                "device_id": "fresh-device",
                "device_token": "secret-device-token",
                "expected_account": "HighlandTech",
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def test_one_file_setup_pairs_and_installs_into_the_windows_user_profile(tmp_path, monkeypatch):
    source = tmp_path / "download" / "ERISAProsFTWAgentSetup.exe"
    source.parent.mkdir()
    source.write_bytes(b"agent executable")
    local_app_data = tmp_path / "LocalAppData"
    previous_agent = local_app_data / "ERISAPros" / "FTWLocalAgent" / "ERISAProsFTWAgent.exe"
    previous_agent.parent.mkdir(parents=True)
    previous_agent.write_bytes(b"previous agent")
    stopped_processes = []
    monkeypatch.setattr(
        windows_agent_installation.subprocess,
        "run",
        lambda command, **kwargs: stopped_processes.append((command, kwargs)),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), PairingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = asyncio.run(
            install_agent(
                server_url=f"http://127.0.0.1:{server.server_port}",
                pairing_code="fresh-code",
                device_name="Fresh client computer",
                source_executable=source,
                local_app_data=local_app_data,
                register_startup=False,
                ftw_login_credentials={
                    "company_code": "test-company",
                    "username": "test-user",
                    "password": "local-password",
                },
            )
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result.executable == local_app_data / "ERISAPros" / "FTWLocalAgent" / "ERISAProsFTWAgent.exe"
    assert result.executable.read_bytes() == b"agent executable"
    assert result.credential.exists()
    assert b"secret-device-token" not in result.credential.read_bytes()
    assert result.login_credential.exists()
    assert b"local-password" not in result.login_credential.read_bytes()
    assert load_secret_json(result.login_credential) == {
        "company_code": "test-company",
        "username": "test-user",
        "password": "local-password",
    }
    assert stopped_processes[0][0] == ["taskkill.exe", "/IM", "ERISAProsFTWAgent.exe", "/F"]


def test_client_can_choose_and_enter_an_encrypted_local_auto_login():
    answers = iter(["", "company-01", "client.user"])

    result = collect_ftw_login_credentials(
        input_func=lambda _prompt: next(answers),
        password_func=lambda _prompt: "client-password",
    )

    assert result == {
        "company_code": "company-01",
        "username": "client.user",
        "password": "client-password",
    }


def test_client_can_decline_local_auto_login():
    result = collect_ftw_login_credentials(
        input_func=lambda _prompt: "n",
        password_func=lambda _prompt: (_ for _ in ()).throw(AssertionError("password prompt was unexpected")),
    )

    assert result is None


def test_client_password_is_not_modified_before_windows_encrypts_it():
    answers = iter(["", "company-01", "client.user"])

    result = collect_ftw_login_credentials(
        input_func=lambda _prompt: next(answers),
        password_func=lambda _prompt: " password with spaces ",
    )

    assert result["password"] == " password with spaces "


def test_reinstall_can_remove_a_previously_saved_automatic_login(tmp_path, monkeypatch):
    source = tmp_path / "ERISAProsFTWAgentSetup.exe"
    source.write_bytes(b"agent executable")
    installed = windows_agent_installation.installation_paths(tmp_path / "LocalAppData")
    installed.root.mkdir(parents=True)
    installed.login_credential.write_bytes(b"previous encrypted login")

    async def fake_pair_device(*_args, **_kwargs):
        return {
            "device_id": "replacement-device",
            "device_token": "replacement-token",
            "expected_account": "HighlandTech",
        }

    monkeypatch.setattr(windows_agent_installation, "pair_device", fake_pair_device)
    monkeypatch.setattr(windows_agent_installation.subprocess, "run", lambda *_args, **_kwargs: None)

    asyncio.run(
        install_agent(
            server_url="https://dashboard.example.com",
            pairing_code="replacement-code",
            device_name="Client computer",
            source_executable=source,
            local_app_data=tmp_path / "LocalAppData",
            register_startup=False,
            clear_ftw_login_credentials=True,
        )
    )

    assert not installed.login_credential.exists()


def test_existing_connection_probe_distinguishes_active_revoked_and_network_failure(tmp_path, monkeypatch):
    installed = windows_agent_installation.installation_paths(tmp_path / "LocalAppData")
    installed.root.mkdir(parents=True)
    installed.credential.write_bytes(b"protected")
    monkeypatch.setattr(
        windows_agent_installation,
        "load_secret_json",
        lambda _path: {
            "server_url": "https://dashboard.example.com",
            "device_token": "saved-token",
        },
    )

    class FakeApi:
        outcome = None

        def __init__(self, server_url, token):
            assert server_url == "https://dashboard.example.com"
            assert token == "saved-token"

        async def control(self):
            if self.outcome is not None:
                raise self.outcome
            return {"status": "CONNECTED"}

        async def close(self):
            return None

    monkeypatch.setattr(windows_agent_installation, "LocalAgentApiClient", FakeApi)
    assert asyncio.run(existing_connection_state(installed)) is ExistingConnectionState.ACTIVE

    request = httpx.Request("GET", "https://dashboard.example.com/control")
    FakeApi.outcome = httpx.HTTPStatusError(
        "revoked", request=request, response=httpx.Response(401, request=request)
    )
    assert asyncio.run(existing_connection_state(installed)) is ExistingConnectionState.REVOKED

    FakeApi.outcome = httpx.ConnectError("offline", request=request)
    assert asyncio.run(existing_connection_state(installed)) is ExistingConnectionState.UNAVAILABLE


def test_disconnected_reconnect_stops_only_verified_installed_agent_and_preserves_profile(
    tmp_path, monkeypatch
):
    installed = windows_agent_installation.installation_paths(tmp_path / "LocalAppData")
    installed.root.mkdir(parents=True)
    installed.executable.write_bytes(b"old-agent")
    installed.credential.write_bytes(b"old-device")
    installed.login_credential.write_bytes(b"saved-login")
    installed.profile.mkdir()
    (installed.profile / "Cookies").write_bytes(b"saved-profile")
    stopped = []
    probes = iter([[4100], [4100], []])
    monkeypatch.setattr(
        windows_agent_installation,
        "installed_agent_processes",
        lambda _installed: next(probes),
    )
    monkeypatch.setattr(windows_agent_installation.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        windows_agent_installation.subprocess,
        "run",
        lambda command, **kwargs: stopped.append((command, kwargs)),
    )
    monkeypatch.setattr(windows_agent_installation, "unregister_startup_task", lambda: None)

    stop_disconnected_installation(installed, graceful_timeout_seconds=0)

    assert stopped[0][0] == ["taskkill.exe", "/PID", "4100", "/T", "/F"]
    assert installed.login_credential.read_bytes() == b"saved-login"
    assert (installed.profile / "Cookies").read_bytes() == b"saved-profile"
    assert not (installed.root / "update-stop.request").exists()


def test_reconnect_replaces_only_device_pairing_and_keeps_saved_ftw_state(tmp_path, monkeypatch):
    source = tmp_path / "download" / "ERISAProsFTWAgentSetup.exe"
    source.parent.mkdir()
    source.write_bytes(b"new-agent")
    local_app_data = tmp_path / "LocalAppData"
    installed = windows_agent_installation.installation_paths(local_app_data)
    installed.root.mkdir(parents=True)
    installed.executable.write_bytes(b"old-agent")
    installed.login_credential.write_bytes(b"saved-login")
    installed.profile.mkdir()
    (installed.profile / "Cookies").write_bytes(b"saved-profile")
    stopped = []

    async def fake_pair_device(*_args, **_kwargs):
        return {
            "device_id": "replacement-device",
            "device_token": "replacement-token",
            "expected_account": "HighlandTech",
        }

    monkeypatch.setattr(windows_agent_installation, "pair_device", fake_pair_device)
    monkeypatch.setattr(
        windows_agent_installation,
        "stop_disconnected_installation",
        lambda current: stopped.append(current),
    )

    asyncio.run(
        install_agent(
            server_url="https://dashboard.example.com",
            pairing_code="replacement-code",
            device_name="Client computer",
            source_executable=source,
            local_app_data=local_app_data,
            register_startup=False,
            reconnect_disconnected=True,
        )
    )

    assert stopped == [installed]
    assert installed.executable.read_bytes() == b"new-agent"
    assert installed.login_credential.read_bytes() == b"saved-login"
    assert (installed.profile / "Cookies").read_bytes() == b"saved-profile"
    assert load_secret_json(installed.credential)["device_token"] == "replacement-token"


def test_double_click_reconnect_prompts_only_for_new_code_and_preserves_ftw_login(tmp_path, monkeypatch):
    from scripts import run_ftw_local_agent as cli

    installed = windows_agent_installation.installation_paths(tmp_path / "LocalAppData")
    installed.root.mkdir(parents=True)
    installed.credential.write_bytes(b"revoked-device")
    installed.login_credential.write_bytes(b"saved-login")
    calls = []
    prompts = []

    async def revoked(_installed):
        return ExistingConnectionState.REVOKED

    async def fake_install_agent(**kwargs):
        calls.append(kwargs)
        return installed

    args = cli.argparse.Namespace(
        command="install",
        pairing_code="",
        server_url="https://dashboard.example.com",
        device_name="Client computer",
        no_startup=True,
    )
    monkeypatch.setattr(cli, "parse_args", lambda: args)
    monkeypatch.setattr(cli, "installation_paths", lambda: installed)
    monkeypatch.setattr(cli, "existing_connection_state", revoked)
    monkeypatch.setattr(cli, "install_agent", fake_install_agent)
    monkeypatch.setattr(cli.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: prompts.append(prompt) or "fresh-pairing-code",
    )

    assert asyncio.run(cli.main()) == 0
    assert prompts == ["Enter the one-time connection code from ERISAPros: "]
    assert calls[0]["reconnect_disconnected"] is True
    assert calls[0]["ftw_login_credentials"] is None
    assert calls[0]["clear_ftw_login_credentials"] is False


def test_failed_reconnect_startup_restores_revoked_credential_for_safe_retry(tmp_path, monkeypatch):
    from app.services.windows_secret_store import save_secret_json

    source = tmp_path / "download.exe"
    source.write_bytes(b"new-agent")
    local_app_data = tmp_path / "LocalAppData"
    installed = windows_agent_installation.installation_paths(local_app_data)
    installed.root.mkdir(parents=True)
    installed.executable.write_bytes(b"old-agent")
    old_device = {
        "server_url": "https://dashboard.example.com",
        "device_id": "revoked-device",
        "device_token": "revoked-token",
        "expected_account": "HighlandTech",
    }
    save_secret_json(installed.credential, old_device)
    revoked = []

    async def fake_pair_device(*_args, **_kwargs):
        return {
            "device_id": "replacement-device",
            "device_token": "replacement-token",
            "expected_account": "HighlandTech",
        }

    class FakeApi:
        def __init__(self, _server_url, token):
            self.token = token

        async def revoke_self(self):
            revoked.append(self.token)

        async def close(self):
            return None

    monkeypatch.setattr(windows_agent_installation, "pair_device", fake_pair_device)
    monkeypatch.setattr(windows_agent_installation, "LocalAgentApiClient", FakeApi)
    monkeypatch.setattr(windows_agent_installation, "stop_disconnected_installation", lambda _installed: None)
    monkeypatch.setattr(
        windows_agent_installation,
        "register_startup_task",
        lambda _installed: (_ for _ in ()).throw(OSError("registry unavailable")),
    )

    try:
        asyncio.run(
            install_agent(
                server_url="https://dashboard.example.com",
                pairing_code="replacement-code",
                device_name="Client computer",
                source_executable=source,
                local_app_data=local_app_data,
                reconnect_disconnected=True,
            )
        )
    except OSError as exc:
        assert "registry unavailable" in str(exc)
    else:
        raise AssertionError("Reconnect startup failure was not surfaced")

    assert load_secret_json(installed.credential) == old_device
    assert revoked == ["replacement-token"]
