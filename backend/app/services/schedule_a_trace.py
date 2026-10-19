"""Immutable stage evidence for extraction diagnostics, excluding credentials/raw payloads."""
from app.models import NormalizedExtractionResult


def extraction_stage(name, fields, broker_rows=()):
    return {
        "stage": name,
        "fields": [f.model_dump(mode="json") for f in fields],
        "broker_rows": [r.model_dump(mode="json") for r in broker_rows],
    }


def capture_extraction_stage(result: NormalizedExtractionResult, name: str) -> None:
    raw = dict(result.raw) if isinstance(result.raw, dict) else {"provider_raw": result.raw}
    stages = list(raw.get("extraction_stages") or [])
    stages.append(extraction_stage(name, result.fields, result.schedule_a_broker_rows))
    raw["extraction_stages"] = stages
    result.raw = raw
