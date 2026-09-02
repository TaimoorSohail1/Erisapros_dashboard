"""Benchmark compact FT Williams failure APIs without printing sensitive data."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.api.ftwilliams import _build_failure_queue  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.models import Filing, FTWilliamsOperationDiagnostic, FTWilliamsReview, FTWilliamsReviewStatus  # noqa: E402
from app.repositories import MemoryRepository, MongoRepository  # noqa: E402


def timed_result(awaitable):
    async def run():
        started = time.perf_counter()
        response = await awaitable
        elapsed_ms = (time.perf_counter() - started) * 1_000
        body = response.model_dump_json()
        return response, round(elapsed_ms, 2), len(body.encode("utf-8"))

    return run()


async def seed_memory_repository(count: int) -> MemoryRepository:
    repository = MemoryRepository()
    large_excerpt = "x" * 20_000
    for index in range(count):
        filing = await repository.create_filing(
            Filing(
                file_name=f"Failure {index:04d}.pdf",
                content_type="application/pdf",
                file_size=1,
                s3_key=f"benchmark/{index}.pdf",
            )
        )
        await repository.upsert_ftwilliams_review(
            FTWilliamsReview(
                filing_id=filing.id or "",
                status=FTWilliamsReviewStatus.UPDATE_FAILED,
                active_failure=True,
                active_failure_reason="FT Williams update failed because the selected plan could not be matched.",
                update_attempted_count=41,
                update_diagnostics=[
                    FTWilliamsOperationDiagnostic(
                        operation="update_schedule_a",
                        outcome_code="HTTP_ERROR",
                        response_excerpt=large_excerpt,
                    )
                ],
            )
        )
    return repository


async def benchmark_synthetic() -> dict:
    results = []
    for count in (0, 10, 100, 1_000):
        repository = await seed_memory_repository(count)
        page, page_ms, page_bytes = await timed_result(
            _build_failure_queue(repository, page=1, page_size=10)
        )
        notifications, notification_ms, notification_bytes = await timed_result(
            _build_failure_queue(repository, page=1, page_size=3)
        )
        results.append(
            {
                "failures": count,
                "page_items": len(page.items),
                "page_ms": page_ms,
                "page_bytes": page_bytes,
                "notification_items": len(notifications.items),
                "notification_ms": notification_ms,
                "notification_bytes": notification_bytes,
                "page_target_met": page_ms < 1_500,
                "notification_target_met": notification_ms < 1_000,
            }
        )
    return {"synthetic": results}


async def benchmark_mongo() -> dict:
    uri = (os.environ.get("MONGODB_URI") or get_settings().mongodb_uri or "").strip()
    if not uri:
        raise SystemExit("MONGODB_URI is required with --mongo")
    repository = MongoRepository(uri)
    samples = []
    for _ in range(5):
        response, elapsed_ms, response_bytes = await timed_result(
            _build_failure_queue(repository, page=1, page_size=10)
        )
        samples.append({"items": len(response.items), "total": response.total, "elapsed_ms": elapsed_ms, "response_bytes": response_bytes})
    return {
        "mongo": {
            "samples": samples,
            "median_ms": round(statistics.median(item["elapsed_ms"] for item in samples), 2),
            "median_bytes": int(statistics.median(item["response_bytes"] for item in samples)),
        }
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mongo", action="store_true", help="also benchmark the configured MongoDB database")
    args = parser.parse_args()
    report = await benchmark_synthetic()
    if args.mongo:
        report.update(await benchmark_mongo())
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
