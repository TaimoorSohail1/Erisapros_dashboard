import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.services.windows_agent_installation import install_agent
from scripts.run_ftw_local_agent import parse_args


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


def test_one_file_setup_pairs_and_installs_into_the_windows_user_profile(tmp_path):
    source = tmp_path / "download" / "ERISAProsFTWAgentSetup.exe"
    source.parent.mkdir()
    source.write_bytes(b"agent executable")
    local_app_data = tmp_path / "LocalAppData"
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
            )
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result.executable == local_app_data / "ERISAPros" / "FTWLocalAgent" / "ERISAProsFTWAgent.exe"
    assert result.executable.read_bytes() == b"agent executable"
    assert result.credential.exists()
    assert b"secret-device-token" not in result.credential.read_bytes()
