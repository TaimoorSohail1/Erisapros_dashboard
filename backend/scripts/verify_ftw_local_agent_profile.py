"""Verify a client-local persistent FT Williams browser profile without mutations."""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

# Allow both `python -m scripts...` and direct `python scripts/...` use.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.ftwilliams_local_agent import (
    LocalFTWTarget,
    load_local_ftw_targets,
    verify_local_ftw_identity,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a persistent local FT Williams browser profile.")
    parser.add_argument("--profile-dir", required=True, help="Dedicated browser profile directory outside the repository.")
    parser.add_argument("--targets", required=True, help="JSON file containing the FT Williams targets to verify.")
    parser.add_argument("--expected-account", default="HighlandTech")
    parser.add_argument("--interactive-login", action="store_true", help="Pause once so the user can complete login and MFA.")
    parser.add_argument("--restart-check", action="store_true", help="Close and reopen the profile, then verify again without login.")
    parser.add_argument("--report", help="Optional sanitized JSON report path.")
    return parser.parse_args()


async def collect_page_text(page) -> tuple[str, bool]:
    texts: list[str] = []
    password_visible = False
    for frame in page.frames:
        try:
            password_visible = password_visible or bool(await frame.locator("input[type='password']").count())
            body = frame.locator("body")
            texts.append((await body.inner_text(timeout=5_000)) or "")
            values = await frame.locator("input:not([type='password']), textarea, select").evaluate_all(
                """
                elements => elements.flatMap(element => {
                  if (element.tagName === "SELECT") {
                    return [element.value, ...Array.from(element.selectedOptions).map(option => option.text)];
                  }
                  return [element.value];
                }).filter(value => typeof value === "string" && value.trim())
                """
            )
            texts.extend(str(value) for value in values if str(value).strip())
        except Exception:
            continue
    return "\n".join(texts), password_visible


async def verify_target(page, target: LocalFTWTarget, expected_account: str, timeout_ms: int = 30_000):
    await page.goto(target.url, wait_until="domcontentloaded", timeout=timeout_ms)
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1_000
    last_result = None
    while asyncio.get_running_loop().time() < deadline:
        page_text, password_visible = await collect_page_text(page)
        last_result = verify_local_ftw_identity(
            target,
            page_text,
            expected_account=expected_account,
            password_visible=password_visible,
        )
        if last_result.success or last_result.state in {"LOGIN_REQUIRED", "WRONG_ACCOUNT"}:
            return last_result
        await page.wait_for_timeout(500)
    return last_result


async def verification_pass(profile_dir: Path, targets: list[LocalFTWTarget], args, *, allow_login: bool):
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile_dir),
            # FT Williams binds the authenticated session to the browser
            # identity. Keep one stable headed identity; the packaged client
            # agent may minimize this window but must not switch to headless.
            headless=False,
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            if allow_login:
                await page.goto("https://www.ftwilliam.com/cgi-bin/index.cgi?#go=home", wait_until="commit", timeout=45_000)
                await asyncio.to_thread(input, "Log into HighlandTech, finish MFA, then press Enter here: ")
            results = []
            for target in targets:
                results.append(await verify_target(page, target, args.expected_account))
            return results
        finally:
            await context.close()


async def main() -> int:
    args = parse_args()
    if not str(args.expected_account or "").strip():
        raise SystemExit("The expected FT Williams account is required.")
    repository_root = Path(__file__).resolve().parents[2]
    profile_dir = Path(args.profile_dir).expanduser().resolve()
    if profile_dir == repository_root or repository_root in profile_dir.parents:
        raise SystemExit("The persistent FT Williams profile must be stored outside the repository.")
    profile_dir.mkdir(parents=True, exist_ok=True)
    targets = load_local_ftw_targets(args.targets)

    passes = []
    first = await verification_pass(profile_dir, targets, args, allow_login=args.interactive_login)
    passes.append({"name": "initial", "results": [asdict(result) for result in first]})
    if args.restart_check and all(result.success for result in first):
        restarted = await verification_pass(profile_dir, targets, args, allow_login=False)
        passes.append({"name": "after_restart", "results": [asdict(result) for result in restarted]})

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "expected_account": args.expected_account,
        "profile_state_uploaded": False,
        "mutation_attempted": False,
        "passes": passes,
    }
    rendered = json.dumps(report, indent=2)
    print(rendered, flush=True)
    if args.report:
        report_path = Path(args.report).expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered, encoding="utf-8")
    return 0 if all(result["success"] for item in passes for result in item["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
