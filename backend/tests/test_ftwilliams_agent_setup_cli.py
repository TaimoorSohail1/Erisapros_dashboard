import asyncio
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.services import windows_agent_installation
from app.services.windows_agent_installation import InstalledAgent, install_agent
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
