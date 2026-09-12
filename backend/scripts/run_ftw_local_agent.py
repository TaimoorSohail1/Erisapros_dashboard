"""Pair and run the ERISAPros client-local FT Williams agent on Windows."""

import argparse
import asyncio
import socket
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.ftwilliams_local_agent_runtime import (
    FTWLocalAgentRunner,
    LocalAgentApiClient,
    PersistentFTWBrowser,
    pair_device,
)
from app.services.windows_agent_installation import install_agent
from app.services.windows_secret_store import load_secret_json, save_secret_json


DEFAULT_SERVER_URL = "https://d3axcdlq9aydpw.cloudfront.net"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ERISAPros FT Williams local agent")
    subcommands = parser.add_subparsers(dest="command")

    install = subcommands.add_parser("install", help="Install and connect this Windows computer")
    install.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    install.add_argument("--pairing-code", default="")
    install.add_argument("--device-name", default=socket.gethostname())
    install.add_argument("--no-startup", action="store_true", help=argparse.SUPPRESS)

    pair = subcommands.add_parser("pair", help="Pair this Windows account with ERISAPros")
    pair.add_argument("--server-url", required=True)
    pair.add_argument("--pairing-code", required=True)
    pair.add_argument("--device-name", default=socket.gethostname())
    pair.add_argument("--credential-file", required=True)

    run = subcommands.add_parser("run", help="Run the persistent local agent")
    run.add_argument("--credential-file", required=True)
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
    if args.command == "install":
        print("ERISAPros FT Williams Agent setup")
        pairing_code = str(args.pairing_code or "").strip()
        if not pairing_code:
            pairing_code = input("Enter the one-time connection code from ERISAPros: ").strip()
        if not pairing_code:
            print("A one-time connection code is required.", file=sys.stderr)
            return 2
        if not getattr(sys, "frozen", False):
            print("Client setup must be run from the packaged ERISAPros installer.", file=sys.stderr)
            return 2
        await install_agent(
            server_url=args.server_url,
            pairing_code=pairing_code,
            device_name=args.device_name,
            source_executable=sys.executable,
            register_startup=not args.no_startup,
        )
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
    browser = PersistentFTWBrowser(
        args.profile_dir,
        expected_account=credentials["expected_account"],
    )
    runner = FTWLocalAgentRunner(api, browser)
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
