from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.repositories import get_repository
from app.services.schedule_a_classification_migration import reclassify_active_filings


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply automatic Schedule A rating rules to existing active filings."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist classifications, derived values, field counts, and audit events. Without this flag the command is read-only.",
    )
    args = parser.parse_args()

    repository = get_repository()
    report = await reclassify_active_filings(repository, apply_changes=args.apply)
    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry-run",
                "filing_count": len(report),
                "filings": report,
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
