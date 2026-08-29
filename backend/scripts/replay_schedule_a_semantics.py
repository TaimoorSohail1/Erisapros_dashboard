"""Replay stored EyeLevel/GroundX candidates through the semantic layer.

This does not call the provider or mutate FT Williams.  It is a deterministic
regression runner for previously captured provider responses and their exact
source PDFs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.models import NormalizedExtractionField, NormalizedExtractionResult, ScheduleABrokerRow  # noqa: E402
from app.services.extractor import extract_pdf_layout_text_pages  # noqa: E402
from app.services.field_rules import DEFAULT_FIELD_RULES  # noqa: E402
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result  # noqa: E402
from app.services.schedule_a_semantic_layer import SemanticDocument, enrich_schedule_a_result  # noqa: E402


def replay(comparison_path: Path) -> dict:
    payload = json.loads(comparison_path.read_text(encoding="utf-8"))
    cases: list[dict] = []
    for source_case in payload.get("cases", []):
        source_path = Path(source_case["source_path"])
        fields = [
            NormalizedExtractionField(field_name=label, value=str(value), confidence=0.95)
            for label, value in source_case.get("groundx_fields", {}).items()
            if str(value or "").strip()
        ]
        brokers = [ScheduleABrokerRow.model_validate(row) for row in source_case.get("groundx_brokers", [])]
        result = NormalizedExtractionResult(
            provider=str(source_case.get("groundx_provider") or "EyeLevel/GroundX replay"),
            fields=fields,
            schedule_a_broker_rows=brokers,
            raw={"provider_raw": source_case.get("groundx_raw", {})},
        )
        pages = extract_pdf_layout_text_pages(source_path.read_bytes())
        enriched = enrich_schedule_a_result(
            result,
            SemanticDocument.from_page_texts(pages),
            rules=DEFAULT_FIELD_RULES,
        )
        resolved = resolve_schedule_a_result(enriched, rules=DEFAULT_FIELD_RULES)
        semantic = resolved.raw.get("semantic_resolution", {})
        quality = resolved.raw.get("extraction_quality", {})
        cases.append(
            {
                "client": source_case.get("client"),
                "source_relative": source_case.get("source_relative"),
                "decision": quality.get("decision"),
                "corrections": semantic.get("corrections", []),
                "ambiguities": semantic.get("ambiguities", []),
                "group_count": semantic.get("group_count", 0),
                "cross_field_errors": quality.get("cross_field_errors", []),
                "review_fields": quality.get("review_fields", []),
                "fields": {field.field_name: field.value for field in resolved.fields},
            }
        )
    return {
        "case_count": len(cases),
        "automatic_count": sum(case["decision"] == "AUTOMATIC" for case in cases),
        "review_required_count": sum(case["decision"] == "REVIEW_REQUIRED" for case in cases),
        "correction_count": sum(len(case["corrections"]) for case in cases),
        "ambiguity_count": sum(len(case["ambiguities"]) for case in cases),
        "multiple_group_count": sum(case["group_count"] > 1 for case in cases),
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comparison", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = replay(args.comparison)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
