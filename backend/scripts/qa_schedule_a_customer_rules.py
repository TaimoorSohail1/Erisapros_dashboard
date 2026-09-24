"""Source comparison for customer rules; never creates filings or sends FTW data."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.services.extractor import ExtractionService, extract_pdf_layout_text_pages, local_schedule_a_pdf_result
from app.services.schedule_a_extraction_pipeline import apply_schedule_a_pipeline
from app.services.schedule_a_semantic_layer import SemanticDocument, enrich_schedule_a_result


def relevant_fields(result):
    return [{"field": field.field_name, "value": field.value, "page": field.page,
             "confidence": field.confidence, "decision": field.decision,
             "evidence_providers": sorted({item.provider for item in field.evidence if item.provider})}
            for field in result.fields if field.field_name.lower().startswith(("1e.", "3e."))]


class ObservedExtractionService(ExtractionService):
    before_customer_semantics = None

    async def _extract_schedule_a_unresolved(self, *args, **kwargs):
        result = await super()._extract_schedule_a_unresolved(*args, **kwargs)
        self.before_customer_semantics = relevant_fields(result)
        return result


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live-eyelevel", action="store_true", help="Use the existing configured extraction provider/bucket")
    args = parser.parse_args()
    data = args.pdf.read_bytes()
    settings = get_settings()
    start = time.perf_counter()
    if args.live_eyelevel:
        if not (settings.groundx_api_key and settings.groundx_bucket_id):
            raise SystemExit("Existing Eyelevel/GroundX configuration is required.")
        service = ObservedExtractionService()
        result = await service.extract_schedule_a(data, args.pdf.name)
        before = service.before_customer_semantics
    else:
        result = local_schedule_a_pdf_result(data, args.pdf.name)
        before = relevant_fields(result)
        result = enrich_schedule_a_result(result, SemanticDocument.from_page_texts(
            extract_pdf_layout_text_pages(data)), rules=ExtractionService().field_rules)
        result = apply_schedule_a_pipeline(result,
            authoritative=settings.schedule_a_canonical_validation_enabled,
            shadow=settings.schedule_a_canonical_validation_shadow_enabled,
            rules=ExtractionService().field_rules)
    raw = result.raw if isinstance(result.raw, dict) else {}
    report = {
        "mode": "existing-provider extraction pipeline" if args.live_eyelevel else "local PDF parser",
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "elapsed_seconds": round(time.perf_counter() - start, 2),
        "provider": result.provider,
        "provider_extraction_used": (result.provider.startswith("GroundX")
            and not raw.get("manual_review_required")) if args.live_eyelevel else False,
        "manual_review_required": bool(raw.get("manual_review_required")),
        "before_semantic_rule": before,
        "after": relevant_fields(result),
        "lives_columns": raw.get("semantic_resolution", {}).get("lives_covered_columns", []),
        "semantic_decision": raw.get("semantic_resolution", {}).get("decision"),
        "canonical_authoritative": settings.schedule_a_canonical_validation_enabled,
        "canonical_shadow": settings.schedule_a_canonical_validation_shadow_enabled,
        "broker_codes": [{"code": row.organization_code, "defaulted": row.organization_code_defaulted}
                         for row in result.schedule_a_broker_rows],
        "ftw_writes": 0,
        "filings_created": 0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
