"""Create a host-protected Playwright session file for FTW demo use.

Run this interactively on the dedicated automation worker. Never commit the
resulting JSON because it contains authenticated browser state. Use worker
disk encryption and restrict filesystem access to the service account.
"""

import argparse
import asyncio
from pathlib import Path
from urllib.parse import urlsplit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Save an FT Williams demo browser session.")
    parser.add_argument("--url", default="https://www.ftwilliam.com", help="FT Williams login URL.")
    parser.add_argument("--storage-state", required=True, help="Absolute output path outside the repository.")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    parsed = urlsplit(args.url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not (host == "ftwilliam.com" or host.endswith(".ftwilliam.com")):
        raise SystemExit("The login URL must be an HTTPS ftwilliam.com address.")

    output = Path(args.storage_state).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(args.url, wait_until="domcontentloaded")
        await asyncio.to_thread(
            input,
            "Log in to the dedicated FT Williams demo account, finish MFA, then press Enter here: ",
        )
        await context.storage_state(path=str(output))
        output.chmod(0o600)
        await context.close()
        await browser.close()
    print(f"Saved FT Williams browser session to {output}")


if __name__ == "__main__":
    asyncio.run(main())
