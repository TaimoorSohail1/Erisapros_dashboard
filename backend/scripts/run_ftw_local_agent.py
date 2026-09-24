"""Pair and run the ERISAPros client-local FT Williams agent on Windows."""

import argparse
import asyncio
import getpass
import socket
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.ftwilliams_local_agent_runtime import (
    AGENT_VERSION,
    FTWLocalAgentRunner,
    LocalAgentApiClient,
    PersistentFTWBrowser,
    pair_device,
)
from app.services.windows_agent_installation import (
    ExistingConnectionState,
    existing_connection_state,
    install_agent,
    installation_paths,
)
from app.services.windows_agent_update import (
    RUNTIME_LOCK, STOP_REQUEST, exclusive_file_lock, rollback_agent, update_agent,
)
from app.services.windows_secret_store import load_secret_json, save_secret_json


DEFAULT_SERVER_URL = "https://d3axcdlq9aydpw.cloudfront.net"


def collect_ftw_login_credentials(*, input_func=input, password_func=getpass.getpass) -> dict[str, str] | None:
    choice = input_func("Save FT Williams login on this computer for automatic sign-in? [Y/n]: ").strip().lower()
    if choice in {"n", "no"}:
        return None

    def required(prompt: str, reader, *, trim: bool = True) -> str:
        while True:
            original = reader(prompt)
            value = original.strip()
            if value:
                return value if trim else original
            print("This value is required.")

    return {
        "company_code": required("FT Williams company code: ", input_func),
        "username": required("FT Williams username: ", input_func),
        "password": required(
            "FT Williams password (stored only with Windows encryption): ",
            password_func,
            trim=False,
        ),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ERISAPros FT Williams local agent")
    parser.add_argument("--version", action="version", version=f"ERISAPros FT Williams Agent {AGENT_VERSION}")
    subcommands = parser.add_subparsers(dest="command")

    install = subcommands.add_parser("install", help="Install and connect this Windows computer")
    install.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    install.add_argument("--pairing-code", default="")
    install.add_argument("--device-name", default=socket.gethostname())
    install.add_argument("--no-startup", action="store_true", help=argparse.SUPPRESS)

    update = subcommands.add_parser("update", help="Update an existing agent without reconnecting")
    update.add_argument("--expected-sha256", required=True)
    update.add_argument("--allow-unsigned", action="store_true", help="Explicit consent for unsigned restricted testing")
    update.add_argument("--stop-timeout-seconds", type=float, default=120)
    update.add_argument("--no-startup", action="store_true", help=argparse.SUPPRESS)

    rollback = subcommands.add_parser("rollback", help="Recover the executable from a verified update backup")
    rollback.add_argument("--backup-directory", required=True)
    rollback.add_argument("--stop-timeout-seconds", type=float, default=120)
    rollback.add_argument("--no-startup", action="store_true", help=argparse.SUPPRESS)

    pair = subcommands.add_parser("pair", help="Pair this Windows account with ERISAPros")
    pair.add_argument("--server-url", required=True)
    pair.add_argument("--pairing-code", required=True)
    pair.add_argument("--device-name", default=socket.gethostname())
    pair.add_argument("--credential-file", required=True)

    run = subcommands.add_parser("run", help="Run the persistent local agent")
    run.add_argument("--credential-file", required=True)
    run.add_argument("--login-credential-file", default="")
    run.add_argument("--profile-dir", required=True)
    run.add_argument("--poll-seconds", type=float, default=10.0)
    run.add_argument("--once", action="store_true", help="Run one heartbeat/job-poll cycle")

    unpair = subcommands.add_parser("unpair", help="Revoke this computer and remove its local pairing")
    unpair.add_argument("--credential-file", required=True)
    effective_argv = ["install"] if argv == [] else argv
    if argv is None and len(sys.argv) == 1:
        effective_argv = ["install"]
    return parser.parse_args(effective_argv)


async def main() -> int:
    args = parse_args()
    if args.command in {"update", "rollback"}:
        if not getattr(sys, "frozen", False):
            print("Client updates must be run from the packaged installer.", file=sys.stderr)
            return 2
        try:
            if args.command == "update":
                if args.allow_unsigned:
                    print("Unsigned restricted test approved for this invocation; Windows may warn about the publisher.")
                result = update_agent(
                    source_executable=sys.executable, expected_sha256=args.expected_sha256,
                    allow_unsigned=args.allow_unsigned, restart=not args.no_startup,
                    stop_timeout_seconds=args.stop_timeout_seconds,
                )
                print(f"Update installed. Verified rollback backup: {result.backup}")
            else:
                rollback_agent(args.backup_directory, restart=not args.no_startup,
                               stop_timeout_seconds=args.stop_timeout_seconds)
                print("Previous executable restored; saved connection and login were not replaced.")
        except Exception as exc:
            print(f"Agent maintenance could not finish: {exc}", file=sys.stderr)
            return 1
        print("Confirm the agent version and connection in ERISAPros; startup alone does not prove readiness.")
        return 0
    if args.command == "install":
        print("ERISAPros FT Williams Agent setup")
        installed = installation_paths()
        reconnect_disconnected = False
        if installed.credential.exists():
            state = await existing_connection_state(installed)
            if state is ExistingConnectionState.ACTIVE:
                print("An existing connection was found and is still active. Use the update command with the "
                      "approved release checksum; do not reconnect or re-enter credentials to update.", file=sys.stderr)
                return 2
            if state is ExistingConnectionState.REVOKED:
                reconnect_disconnected = True
                print("The previous ERISAPros connection is disconnected. Setup will reconnect this computer "
                      "and keep the saved FT Williams login and browser profile.")
            elif state is ExistingConnectionState.UNAVAILABLE:
                print("An existing connection was found, but ERISAPros could not verify whether it is disconnected. "
                      "Check the internet connection and run setup again; nothing was changed.", file=sys.stderr)
                return 2
            elif state is ExistingConnectionState.INVALID:
                print("An existing connection was found, but its protected credential cannot be verified. "
                      "Setup stopped without deleting it; contact support for safe recovery.", file=sys.stderr)
                return 2
        pairing_code = str(args.pairing_code or "").strip()
        if not pairing_code:
            pairing_code = input("Enter the one-time connection code from ERISAPros: ").strip()
        if not pairing_code:
            print("A one-time connection code is required.", file=sys.stderr)
            return 2
        if not getattr(sys, "frozen", False):
            print("Client setup must be run from the packaged ERISAPros installer.", file=sys.stderr)
            return 2
        ftw_login_credentials = None
        preserved_login = reconnect_disconnected and installed.login_credential.is_file()
        if not args.pairing_code and not reconnect_disconnected:
            ftw_login_credentials = collect_ftw_login_credentials()
        try:
            await install_agent(
                server_url=args.server_url,
                pairing_code=pairing_code,
                device_name=args.device_name,
                source_executable=sys.executable,
                register_startup=not args.no_startup,
                ftw_login_credentials=ftw_login_credentials,
                clear_ftw_login_credentials=(
                    not args.pairing_code
                    and not reconnect_disconnected
                    and ftw_login_credentials is None
                ),
                reconnect_disconnected=reconnect_disconnected,
            )
        except Exception as exc:
            print(f"Setup could not finish: {exc}", file=sys.stderr)
            if not args.pairing_code and sys.stdin.isatty():
                input("Press Enter to close setup.")
            return 1
        if ftw_login_credentials or preserved_login:
            print("Connected successfully. FT Williams will open and sign in automatically. Complete MFA if requested, then click Test connection in ERISAPros.")
        else:
            print("Connected successfully. Sign in in the FT Williams window, then return to ERISAPros and click Test connection.")
        if not args.pairing_code and sys.stdin.isatty():
            input("Press Enter to close setup.")
        return 0

    if args.command == "pair":
        paired = await pair_device(args.server_url, args.pairing_code, args.device_name)
        save_secret_json(
            args.credential_file,
            {
                "server_url": args.server_url,
                "device_id": paired["device_id"],
                "device_token": paired["device_token"],
                "expected_account": paired["expected_account"],
            },
        )
        print("This computer is paired. The device token was protected with Windows DPAPI.")
        return 0

    if args.command == "run":
        root = Path(args.credential_file).expanduser().resolve().parent
        try:
            with exclusive_file_lock(root / RUNTIME_LOCK):
                if (root / STOP_REQUEST).exists():
                    return 0
                return await run_connected_agent(args, root)
        except RuntimeError as exc:
            print(f"Agent startup refused: {exc}", file=sys.stderr)
            return 1
    credentials = load_secret_json(args.credential_file)
    api = LocalAgentApiClient(credentials["server_url"], credentials["device_token"])
    if args.command == "unpair":
        try:
            await api.revoke_self()
        finally:
            await api.close()
        Path(args.credential_file).unlink(missing_ok=True)
        print("This computer was disconnected from ERISAPros.")
        return 0


async def run_connected_agent(args: argparse.Namespace, root: Path) -> int:
    credentials = load_secret_json(args.credential_file)
    api = LocalAgentApiClient(credentials["server_url"], credentials["device_token"])
    browser = PersistentFTWBrowser(
        args.profile_dir,
        expected_account=credentials["expected_account"],
        login_credentials=(
            load_secret_json(args.login_credential_file)
            if args.login_credential_file and Path(args.login_credential_file).is_file()
            else None
        ),
    )
    runner = FTWLocalAgentRunner(api, browser, stop_requested=lambda: (root / STOP_REQUEST).exists())
    try:
        if args.once:
            await runner.run_once()
        else:
            await runner.run_forever(poll_seconds=args.poll_seconds)
    finally:
        await runner.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
