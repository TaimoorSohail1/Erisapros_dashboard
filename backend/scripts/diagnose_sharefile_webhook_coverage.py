"""Reconcile ShareFile upload subscriptions and print safe coverage diagnostics.

Run with the same ShareFile and MongoDB environment as the production worker.
The script deliberately omits webhook URLs, OAuth tokens, and API response bodies.
"""

import asyncio
import os

from app.services.sharefile import ShareFileService


async def main() -> None:
    result = await ShareFileService().auto_register_relevant_webhooks()
    print(
        "ShareFile webhook coverage: "
        f"roots={result.get('webhook_roots', 0)} "
        f"registered={result.get('registered', 0)} "
        f"existing={result.get('skipped', 0)} "
        f"failed={result.get('failed', 0)}"
    )
    covered_ids = {
        item.get("folder_id")
        for key in ("registered_roots", "skipped_roots")
        for item in result.get(key, [])
    }
    failed_ids = {item.get("folder_id") for item in result.get("failed_roots", [])}
    target_ids = {value.strip() for value in os.getenv("TARGET_SHAREFILE_FOLDER_IDS", "").split(",") if value.strip()}
    for folder_id in sorted(target_ids):
        status = "covered" if folder_id in covered_ids else "failed" if folder_id in failed_ids else "not a subscription root"
        print(f"Target folder {folder_id}: {status}")
    for item in result.get("failed_roots", []):
        status_code = item.get("status_code")
        failure = f"HTTP {status_code}" if status_code else "request exception"
        print(f"Failed folder {item.get('folder_id')}: {failure}")


if __name__ == "__main__":
    asyncio.run(main())
