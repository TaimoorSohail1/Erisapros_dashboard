"""Run Schedule A extraction in a local, write-isolated shadow harness.

The harness reads downloaded ShareFile PDFs and calls only the configured
extraction provider. It never creates filings, updates the dashboard database,
or invokes the FT Williams automation service.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any

from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import DocumentType, FormType
from app.services.extractor import ExtractionService
from app.services.mapping import map_extraction_to_rules
from app.services.schedule_a_classification import classify_schedule_a_fields


def _pdf_profile(path: Path) -> dict[str, Any]:
    try:
        reader = PdfReader(path)
        text_chars = 0
        for page in reader.pages:
            try:
                text_chars += len(page.extract_text() or "")
            except Exception:
                continue
        return {
            "page_count": len(reader.pages),
            "text_chars": text_chars,
            "image_only_or_scanned": text_chars < 50,
        }
    except Exception as exc:
        return {
            "page_count": None,
            "text_chars": 0,
            "image_only_or_scanned": None,
            "pdf_profile_error": f"{type(exc).__name__}: {exc}",
        }


def _field_payload(field) -> dict[str, Any]:
    payload = field.model_dump(mode="json")
    payload["evidence_count"] = len(field.evidence)
    payload["evidence_pages"] = sorted(
        {item.page for item in field.evidence if item.page is not None}
    )
    return payload


async def _extract_one(
    record: dict[str, Any],
    output_dir: Path,
    semaphore: asyncio.Semaphore,
    force: bool,
) -> dict[str, Any]:
    item_id = str(record.get("item_id") or "unknown")
    result_path = output_dir / f"{item_id}.json"
    if result_path.exists() and not force:
        try:
            return json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    source_path = Path(str(record.get("local_path") or ""))
    started = time.perf_counter()
    base = {
        "item_id": item_id,
        "client_name": record.get("client_name"),
        "file_name": record.get("file_name"),
        "folder_path": record.get("folder_path") or record.get("path"),
        "local_path": str(source_path),
        "file_size": record.get("bytes") or record.get("file_size"),
        "mode": "local shadow extraction; no dashboard, database, or FT Williams writes",
        "dashboard_writes": 0,
        "database_writes": 0,
        "ft_williams_writes": 0,
    }
    if not source_path.is_file():
        output = {**base, "status": "ERROR", "error": "Downloaded PDF is missing."}
        result_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        return output

    data = source_path.read_bytes()
    base.update(_pdf_profile(source_path))
    base["source_sha256"] = hashlib.sha256(data).hexdigest()

    try:
        async with semaphore:
            result = await ExtractionService().extract_schedule_a(data, source_path.name)
        mapped_result = map_extraction_to_rules(
            f"shadow-{item_id}",
            result.fields,
            form_type=FormType.SCHEDULE_A,
            source_document_type=DocumentType.SCHEDULE_A,
        )
        classification = classify_schedule_a_fields(
            mapped_result["fields"],
            result.classification_signals,
        )
        raw = result.raw if isinstance(result.raw, dict) else {}
        quality = raw.get("extraction_quality") if isinstance(raw, dict) else None
        adapter = raw.get("xray_adapter") if isinstance(raw, dict) else None
        output = {
            **base,
            "status": "SUCCESS",
            "elapsed_seconds": round(time.perf_counter() - started, 2),
            "provider": result.provider,
            "normalized_fields": [_field_payload(field) for field in result.fields],
            "mapped_fields": [field.model_dump(mode="json") for field in mapped_result["fields"]],
            "broker_rows": [row.model_dump(mode="json") for row in result.schedule_a_broker_rows],
            "worksheet_summaries": [
                summary.model_dump(mode="json")
                for summary in result.schedule_a_worksheet_summaries
            ],
            "classification": {
                "contract_type": classification.contract_type.value,
                "reason": classification.reason,
                "confidence": classification.confidence,
                "evidence": list(classification.evidence),
                "signals": result.classification_signals,
            },
            "xray_adapter": adapter or {},
            "extraction_quality": quality or {},
            "mapping_summary": {
                "status": mapped_result["status"].value,
                "overall_confidence": mapped_result["overall_confidence"],
                "low_confidence_count": mapped_result["low_confidence_count"],
                "missing_high_priority_count": mapped_result["missing_high_priority_count"],
                "missing_medium_priority_count": mapped_result["missing_medium_priority_count"],
                "missing_low_priority_count": mapped_result["missing_low_priority_count"],
                "unmapped_count": mapped_result["unmapped_count"],
            },
        }
    except Exception as exc:
        output = {
            **base,
            "status": "ERROR",
            "elapsed_seconds": round(time.perf_counter() - started, 2),
            "error": f"{type(exc).__name__}: {exc}",
        }

    result_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return output


async def _run(args: argparse.Namespace) -> None:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    records = [record for record in manifest if record.get("result") in {"downloaded", "cached"}]
    if args.client:
        records = [record for record in records if record.get("client_name") == args.client]
    if args.file_name:
        records = [record for record in records if record.get("file_name") == args.file_name]
    if args.item_id:
        selected_ids = {str(item) for item in args.item_id}
        records = [record for record in records if str(record.get("item_id")) in selected_ids]
    if args.multiple_record_candidates:
        selected: list[dict[str, Any]] = []
        for record in records:
            cached_path = args.output_dir / f"{record.get('item_id')}.json"
            if not cached_path.exists():
                continue
            try:
                cached = json.loads(cached_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            adapter_count = int((cached.get("xray_adapter") or {}).get("schedule_a_count") or 0)
            cross_field_errors = (cached.get("extraction_quality") or {}).get("cross_field_errors") or []
            if adapter_count > 1 or "eyemed_contract_grouping_required" in cross_field_errors:
                selected.append(record)
        records = selected
    args.output_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    tasks = [
        asyncio.create_task(_extract_one(record, args.output_dir, semaphore, args.force))
        for record in records
    ]
    results: list[dict[str, Any]] = []
    for index, task in enumerate(asyncio.as_completed(tasks), start=1):
        result = await task
        results.append(result)
        print(
            json.dumps(
                {
                    "progress": f"{index}/{len(tasks)}",
                    "status": result.get("status"),
                    "client": result.get("client_name"),
                    "file": result.get("file_name"),
                    "elapsed_seconds": result.get("elapsed_seconds"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    by_id = {str(item.get("item_id")): item for item in results}
    ordered = [by_id[str(record.get("item_id"))] for record in records]
    combined = {
        "mode": "local shadow extraction; no dashboard, database, or FT Williams writes",
        "clients": sorted({str(item.get("client_name") or "") for item in ordered}),
        "client_count": len({str(item.get("client_name") or "") for item in ordered}),
        "file_count": len(ordered),
        "success_count": sum(item.get("status") == "SUCCESS" for item in ordered),
        "error_count": sum(item.get("status") == "ERROR" for item in ordered),
        "results": ordered,
    }
    args.output.write_text(
        json.dumps(combined, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "clients": combined["client_count"],
                "files": combined["file_count"],
                "success": combined["success_count"],
                "errors": combined["error_count"],
                "dashboard_writes": 0,
                "database_writes": 0,
                "ft_williams_writes": 0,
            }
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--client")
    parser.add_argument("--file-name")
    parser.add_argument("--item-id", action="append", help="Limit the run to one item ID; repeat for multiple files.")
    parser.add_argument("--multiple-record-candidates", action="store_true")
    parser.add_argument("--force", action="store_true")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
