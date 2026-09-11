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
from app.services.windows_secret_store import load_secret_json, save_secret_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ERISAPros FT Williams local agent")
    subcommands = parser.add_subparsers(dest="command", required=True)

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
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
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
