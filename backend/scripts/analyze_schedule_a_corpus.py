"""Run deterministic Schedule A extraction diagnostics over a private corpus.

This intentionally uses the local adapter and canonical validator only. It is
safe for CI and developer machines because it never uploads source documents.
Approved expected values belong in the separate golden-corpus manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.models import NormalizedExtractionResult  # noqa: E402
from app.services.extractor import (  # noqa: E402
    dedupe_fields,
    extract_pdf_layout_text_pages,
    local_schedule_a_pdf_result,
)
from app.services.field_rules import DEFAULT_FIELD_RULES  # noqa: E402
from app.services.intake_formats import normalize_intake_documents  # noqa: E402
from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result  # noqa: E402
from app.services.schedule_a_semantic_layer import (  # noqa: E402
    SemanticDocument,
    enrich_schedule_a_result,
)


CORE_FIELDS = (
    "1a. Name of Insurance Company",
    "1b. Insurance Carrier EIN",
    "1c. NAIC Code",
    "1d. Contract/Policy Number",
    "1e. Persons Covered (End of Policy Year)",
    "1f. Policy Year Beginning Date",
    "1g. Policy Year Ending Date",
)


def analyze_directory(source_dir: Path) -> dict:
    files = sorted(path for path in source_dir.rglob("*.pdf") if path.is_file())
    cases: list[dict] = []
    seen_hashes: dict[str, str] = {}
    duplicate_files: list[dict[str, str]] = []
    for path in files:
        source_bytes = path.read_bytes()
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        if source_hash in seen_hashes:
            duplicate_files.append(
                {
                    "file": str(path.relative_to(source_dir)),
                    "duplicate_of": seen_hashes[source_hash],
                }
            )
        else:
            seen_hashes[source_hash] = str(path.relative_to(source_dir))
        documents = normalize_intake_documents(path.name, source_bytes)
        results = [
            local_schedule_a_pdf_result(document.file_bytes, document.file_name)
            for document in documents
        ]
        combined = NormalizedExtractionResult(
            provider=" + ".join(dict.fromkeys(result.provider for result in results)),
            fields=dedupe_fields([field for result in results for field in result.fields]),
            schedule_a_broker_rows=[row for result in results for row in result.schedule_a_broker_rows],
            raw={"source_file": path.name},
        )
        page_texts: list[tuple[int, str]] = []
        for document in documents:
            if document.file_name.lower().endswith(".pdf"):
                page_texts.extend(extract_pdf_layout_text_pages(document.file_bytes))
        semantic_document = SemanticDocument.from_page_texts(page_texts)
        enriched = enrich_schedule_a_result(
            combined,
            semantic_document,
            rules=DEFAULT_FIELD_RULES,
        )
        resolved = resolve_schedule_a_result(enriched, rules=DEFAULT_FIELD_RULES)
        fields = {field.field_name: field.value for field in resolved.fields}
        quality = resolved.raw.get("extraction_quality", {})
        semantic = resolved.raw.get("semantic_resolution", {})
        cases.append(
            {
                "file": str(path.relative_to(source_dir)),
                "source_sha256": source_hash,
                "routed_names": [document.file_name for document in documents],
                "signature_corrected": any(document.original_file_name for document in documents),
                "field_count": len(resolved.fields),
                "broker_count": len(resolved.schedule_a_broker_rows),
                "core_complete": all(name in fields for name in CORE_FIELDS),
                "missing_core": [name for name in CORE_FIELDS if name not in fields],
                "decision": quality.get("decision"),
                "error_fields": quality.get("error_fields", []),
                "cross_field_errors": quality.get("cross_field_errors", []),
                "semantic_group_count": semantic.get("group_count", 0),
                "semantic_corrections": semantic.get("corrections", []),
                "semantic_ambiguities": semantic.get("ambiguities", []),
            }
        )

    total = len(cases)
    return {
        "source_dir": str(source_dir.resolve()),
        "document_count": total,
        "unique_document_count": len(seen_hashes),
        "duplicate_files": duplicate_files,
        "core_complete_count": sum(1 for case in cases if case["core_complete"]),
        "structured_broker_count": sum(1 for case in cases if case["broker_count"]),
        "automatic_count": sum(1 for case in cases if case["decision"] == "AUTOMATIC"),
        "review_required_count": sum(1 for case in cases if case["decision"] == "REVIEW_REQUIRED"),
        "signature_corrected_count": sum(1 for case in cases if case["signature_corrected"]),
        "semantic_correction_count": sum(len(case["semantic_corrections"]) for case in cases),
        "semantic_ambiguity_count": sum(len(case["semantic_ambiguities"]) for case in cases),
        "multiple_group_count": sum(1 for case in cases if case["semantic_group_count"] > 1),
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze_directory(args.source_dir)
    rendered = json.dumps(report, indent=None if args.compact else 2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(args.output.resolve())
    else:
        print(rendered)


if __name__ == "__main__":
    main()
