from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.extractor import local_schedule_a_pdf_result  # noqa: E402
from app.services.field_rules import DEFAULT_FIELD_RULES  # noqa: E402
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result  # noqa: E402


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _decimal(value: object) -> Decimal | None:
    clean = re.sub(r"[^0-9.()-]", "", str(value or "")).replace("(", "-").replace(")", "")
    try:
        return Decimal(clean) if clean else None
    except InvalidOperation:
        return None


def _equivalent(label: str, left: object, right: object) -> bool:
    lower = label.casefold()
    if any(token in lower for token in ("amount", "premium", "commission", "fee", "charge", "persons covered")):
        left_decimal, right_decimal = _decimal(left), _decimal(right)
        if left_decimal is not None and right_decimal is not None:
            return left_decimal == right_decimal
    return _key(left) == _key(right)


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay the 230 Schedule A structural families through the canonical pipeline.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "tmp" / "phase2_live_flow_20260831" / "core_layout_manifest.json",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        default=ROOT / "tmp" / "phase2_live_flow_20260831" / "source_audit.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "tmp" / "phase3_system_hardening_20260901" / "layout_regression.json",
    )
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    reference_rows = {
        int(row["slice"]): row
        for row in json.loads(args.reference.read_text(encoding="utf-8"))
    }
    results: list[dict] = []
    for item in manifest:
        slice_number = int(item["slice"])
        path = Path(item["local_path"])
        raw = local_schedule_a_pdf_result(path.read_bytes(), path.name, rules=list(DEFAULT_FIELD_RULES))
        resolved = resolve_schedule_a_result(raw, rules=list(DEFAULT_FIELD_RULES))
        current = {field.field_name: field for field in resolved.fields if str(field.value or "").strip()}
        reference = {
            str(field.get("field_name") or ""): field
            for field in (reference_rows.get(slice_number, {}).get("source_fields") or [])
            if str(field.get("value") or "").strip()
        }
        differences = []
        for label in sorted(set(reference) | set(current)):
            expected = reference.get(label)
            actual = current.get(label)
            if expected and actual and _equivalent(label, expected.get("value"), actual.value):
                continue
            differences.append(
                {
                    "field": label,
                    "expected": expected.get("value") if expected else None,
                    "actual": actual.value if actual else None,
                }
            )
        unsafe_automatic = [
            {
                "field": field.field_name,
                "value": field.value,
                "errors": [check.reason for check in field.validation_results if check.status == "ERROR"],
            }
            for field in resolved.fields
            if field.decision == "AUTOMATIC"
            and any(check.status == "ERROR" for check in field.validation_results)
        ]
        missing_evidence_automatic = [
            field.field_name
            for field in resolved.fields
            if field.decision == "AUTOMATIC"
            and not any(
                check.validator == "source_evidence" and check.status == "PASS"
                for check in field.validation_results
            )
        ]
        result = {
            "slice": slice_number,
            "structural_family_id": item["structural_family_id"],
            "layout_id": item["layout_id"],
            "layout_name": item["layout_name"],
            "source_file": item["source_file"],
            "ocr_risk": item["ocr_risk"],
            "field_count": len(current),
            "automatic_count": sum(field.decision == "AUTOMATIC" for field in resolved.fields),
            "review_count": sum(field.decision == "REVIEW_REQUIRED" for field in resolved.fields),
            "quality_decision": (resolved.raw or {}).get("extraction_quality", {}).get("decision"),
            "differences": differences,
            "unsafe_automatic": unsafe_automatic,
            "missing_evidence_automatic": missing_evidence_automatic,
            "passed": not differences and not unsafe_automatic and not missing_evidence_automatic,
        }
        results.append(result)
        print(
            json.dumps(
                {
                    "slice": slice_number,
                    "family": item["structural_family_id"],
                    "passed": result["passed"],
                    "review": result["review_count"],
                }
            ),
            flush=True,
        )

    totals = {
        "layouts": len(results),
        "passed": sum(row["passed"] for row in results),
        "failed": sum(not row["passed"] for row in results),
        "automatic_fields": sum(row["automatic_count"] for row in results),
        "review_fields": sum(row["review_count"] for row in results),
        "unsafe_automatic_fields": sum(len(row["unsafe_automatic"]) for row in results),
        "automatic_fields_missing_evidence": sum(len(row["missing_evidence_automatic"]) for row in results),
        "reference_differences": sum(len(row["differences"]) for row in results),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"totals": totals, "layouts": results}, indent=2), encoding="utf-8")
    print(json.dumps(totals, indent=2))
    return 0 if totals["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
