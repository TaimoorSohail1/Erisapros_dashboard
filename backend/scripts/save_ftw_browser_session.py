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
    parser.add_argument(
        "--url",
        default="https://www.ftwilliam.com/cgi-bin/index.cgi?#go=home",
        help="FT Williams application URL.",
    )
    parser.add_argument(
        "--expected-account",
        default="HighlandTech",
        help="Account label that must be visible before the session is saved.",
    )
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
        # FT Williams keeps legacy frames/connections active long enough that
        # Playwright's DOMContentLoaded wait can time out even when the secure
        # application page is already usable.  Commit verifies that navigation
        # reached the requested HTTPS origin without waiting on those frames.
        await page.goto(args.url, wait_until="commit", timeout=45_000)
        await asyncio.to_thread(
            input,
            "Log in to the dedicated FT Williams demo account, finish MFA, then press Enter here: ",
        )
        verified = False
        for _ in range(30):
            if "status=invalid" in page.url.casefold():
                break
            page_text: list[str] = []
            password_visible = False
            for frame in page.frames:
                try:
                    password_visible = password_visible or bool(
                        await frame.locator("input[type='password']").count()
                    )
                    body = frame.locator("body")
                    page_text.append((await body.text_content(timeout=2_000)) or "")
                except Exception:
                    continue
            if not password_visible and args.expected_account.casefold() in "\n".join(page_text).casefold():
                verified = True
                break
            await page.wait_for_timeout(1_000)
        if not verified:
            raise SystemExit(
                f"FT Williams session was not saved because account {args.expected_account!r} "
                "was not verified in this browser window."
            )
        await context.storage_state(path=str(output))
        output.chmod(0o600)
        await context.close()
        await browser.close()
    print(f"Saved FT Williams browser session to {output}")


if __name__ == "__main__":
    asyncio.run(main())
