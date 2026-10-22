"""Resolve reviewed interpretations from an operator-controlled local registry.

The registry is not a document upload, provider response or filename routing
table. Exact source hashes bind each approved interpretation. No setting here
enables updates, selects filings or changes automation eligibility.
"""
import hashlib
import json
from pathlib import Path

from app.services.schedule_a_confirmed_clarifications import ConfirmedClarifications


def clarification_for_source(file_bytes: bytes, registry_path: str | Path | None):
    if not registry_path:
        return None
    path = Path(registry_path)
    with path.open('rb') as stream:
        contents = stream.read(1_048_577)
    if len(contents) > 1_048_576:
        raise ValueError('Clarification registry exceeds the 1 MiB limit')
    data = json.loads(contents)
    if not isinstance(data, dict) or data.get('schema_version') != 1 or not isinstance(data.get('approvals'), list):
        raise ValueError('Unsupported clarification registry contract')
    records = {}
    for item in data['approvals']:
        if not isinstance(item, dict) or not isinstance(item.get('rules'), list):
            raise ValueError('Invalid clarification record')
        try:
            record = ConfirmedClarifications(**dict(item, rules=frozenset(item['rules'])))
        except (TypeError, AttributeError, KeyError) as exc:
            raise ValueError('Invalid clarification record') from exc
        if record.source_sha256 in records:
            raise ValueError('Duplicate source clarification approval')
        records[record.source_sha256] = record
    return records.get(hashlib.sha256(file_bytes).hexdigest())
