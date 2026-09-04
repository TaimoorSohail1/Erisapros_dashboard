from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.extractor import local_schedule_a_pdf_result  # noqa: E402
from app.services.field_rules import DEFAULT_FIELD_RULES  # noqa: E402
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result  # noqa: E402
from app.services.schedule_a_layout_engine import (  # noqa: E402
    extract_layout_aware_schedule_a_fields,
    is_layout_label_text,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay real Schedule A PDFs through the geometry-aware extractor.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "tmp" / "schedule_a_qa_25_20260829" / "corpus_manifest.json",
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "tmp" / "schedule_a_geometry_qa_20260905" / "results.json",
    )
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))[: args.limit]
    rows: list[dict] = []
    for item in manifest:
        started = time.perf_counter()
        path = Path(item["local_path"])
        error = None
        try:
            file_bytes = path.read_bytes()
            geometry_fields = extract_layout_aware_schedule_a_fields(file_bytes)
            raw = local_schedule_a_pdf_result(file_bytes, path.name, rules=list(DEFAULT_FIELD_RULES))
            resolved = resolve_schedule_a_result(raw, rules=list(DEFAULT_FIELD_RULES))
            fields = [field for field in resolved.fields if str(field.value or "").strip()]
            label_values = [
                {"field": field.field_name, "value": field.value}
                for field in fields
                if is_layout_label_text(field.value)
            ]
            unsafe_automatic = [
                {
                    "field": field.field_name,
                    "value": field.value,
                    "errors": [check.reason for check in field.validation_results if check.status == "ERROR"],
                }
                for field in fields
                if field.decision == "AUTOMATIC"
                and any(check.status == "ERROR" for check in field.validation_results)
            ]
            incomplete_geometry_evidence = [
                field.field_name
                for field in geometry_fields
                if not field.evidence
                or field.evidence[0].page is None
                or field.evidence[0].bounding_box is None
                or field.evidence[0].table_cell is None
            ]
            by_label = {field.field_name: field for field in fields}
            result = {
                "slice": item["slice"],
                "client": item["client"],
                "source_file": item["source_file"],
                "sha256": _sha256(path),
                "field_count": len(fields),
                "broker_count": len(resolved.schedule_a_broker_rows),
                "automatic_count": sum(field.decision == "AUTOMATIC" for field in fields),
                "review_count": sum(field.decision == "REVIEW_REQUIRED" for field in fields),
                "geometry_field_count": len(geometry_fields),
                "geometry_fields": {field.field_name: field.value for field in geometry_fields},
                "selected_core": {
                    label: by_label[label].value
                    for label in (
                        "1a. Name of Insurance Company",
                        "1b. Insurance Carrier EIN",
                        "1c. NAIC Code",
                        "1d. Contract/Policy Number",
                        "1e. Persons Covered (End of Policy Year)",
                        "1f. Policy Year Beginning Date",
                        "1g. Policy Year Ending Date",
                        "10a. Total premiums or subscription charges paid to carrier",
                    )
                    if label in by_label
                },
                "label_values": label_values,
                "unsafe_automatic": unsafe_automatic,
                "incomplete_geometry_evidence": incomplete_geometry_evidence,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
            result["structural_pass"] = bool(fields) and not label_values and not unsafe_automatic and not incomplete_geometry_evidence
        except Exception as exc:  # pragma: no cover - QA harness reports unexpected files
            error = f"{type(exc).__name__}: {exc}"
            result = {
                "slice": item["slice"],
                "client": item["client"],
                "source_file": item["source_file"],
                "structural_pass": False,
                "error": error,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        rows.append(result)
        print(
            json.dumps(
                {
                    "slice": result["slice"],
                    "pass": result["structural_pass"],
                    "fields": result.get("field_count", 0),
                    "geometry": result.get("geometry_field_count", 0),
                    "error": error,
                }
            ),
            flush=True,
        )

    totals = {
        "documents": len(rows),
        "structural_passed": sum(row["structural_pass"] for row in rows),
        "structural_failed": sum(not row["structural_pass"] for row in rows),
        "documents_with_geometry_fields": sum(bool(row.get("geometry_field_count")) for row in rows),
        "geometry_fields": sum(row.get("geometry_field_count", 0) for row in rows),
        "label_values": sum(len(row.get("label_values", [])) for row in rows),
        "unsafe_automatic_fields": sum(len(row.get("unsafe_automatic", [])) for row in rows),
        "incomplete_geometry_evidence": sum(len(row.get("incomplete_geometry_evidence", [])) for row in rows),
        "elapsed_seconds": round(sum(row.get("elapsed_seconds", 0) for row in rows), 3),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"totals": totals, "documents": rows}, indent=2), encoding="utf-8")
    print(json.dumps(totals, indent=2))
    return 0 if totals["structural_failed"] == 0 else 1


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
