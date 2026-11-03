"""Source-bound permission to replace a selected Schedule A broker roster.

An incomplete extraction must never delete an existing recipient. Require an
independent full-document table read, exact recipient/payment agreement and
reconciled section totals. An edit invalidates the attestation.
"""
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from app.models import ExtractionValidationResult, ScheduleABrokerRow

VALIDATOR = "complete_source_broker_roster"


def _money(value):
    text = re.sub(r"[$,\s]", "", str(value or ""))
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        number = Decimal(text)
        return format(number.normalize(), "f") if number.is_finite() else None
    except InvalidOperation:
        return None


def _key(row):
    normalize = lambda value: re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    return tuple(normalize(getattr(row, name)) for name in
        ("name", "address_line_1", "address_line_2", "city", "state", "zip_code")) + (
        _money(row.commission_total), _money(row.fee_total))


def _fingerprint(rows):
    return hashlib.sha256(json.dumps(sorted(_key(row) for row in rows), separators=(",", ":")).encode()).hexdigest()


def broker_row_has_payment(row):
    return any(value is not None and Decimal(value) != 0
               for value in (_money(row.commission_total), _money(row.fee_total)))


def broker_roster_is_complete(rows):
    if not rows or any(row.decision == "REVIEW_REQUIRED" or any(v.status == "ERROR" for v in row.validation_results)
                       for row in rows):
        return False
    if any(_money(row.commission_total) is None or _money(row.fee_total) is None for row in rows):
        return False
    fingerprint = _fingerprint(rows)
    proofs = [{v.normalized_value for v in row.validation_results if v.validator == VALIDATOR and v.status == "PASS"
        and re.fullmatch(r"[a-f0-9]{64}:" + fingerprint, str(v.normalized_value or ""))} for row in rows]
    return bool(set.intersection(*proofs))


def attest_source_broker_roster(result, independent_rows, source_sha256):
    rows = result.schedule_a_broker_rows
    for row in rows:
        row.validation_results = [v for v in row.validation_results if v.validator != VALIDATOR]
    if not rows or not independent_rows or not re.fullmatch(r"[a-f0-9]{64}", str(source_sha256)):
        return
    if len(result.schedule_a_worksheet_summaries) > 1:
        return
    raw = result.raw if isinstance(result.raw, dict) else {}
    if int(raw.get("semantic_resolution", {}).get("group_count") or 0) > 1:
        return
    provider = next((stage for stage in raw.get("extraction_stages", [])
                     if stage.get("stage") == "structured_provider_normalized"), {})
    try:
        provider_rows = [ScheduleABrokerRow.model_validate(row) for row in provider.get("broker_rows", [])]
    except (ValueError, TypeError):
        return
    # Running the same local parser twice is not independent corroboration.
    if not provider_rows or Counter(_key(row) for row in provider_rows) != Counter(_key(row) for row in independent_rows):
        return
    if any(row.decision == "REVIEW_REQUIRED" or row.confidence < .8
           or any(v.status == "ERROR" for v in row.validation_results)
           or not row.source_page or not any(e.page == row.source_page and e.source_text for e in row.evidence)
           or _money(row.commission_total) is None or _money(row.fee_total) is None
           for row in [*rows, *independent_rows]):
        return
    if Counter(_key(row) for row in rows) != Counter(_key(row) for row in independent_rows):
        return
    for prefix, attribute in (("3b.", "commission_total"), ("3c.", "fee_total")):
        totals = {Decimal(value) for field in result.fields if field.field_name.lower().startswith(prefix)
                  and field.decision != "REVIEW_REQUIRED" and (value := _money(field.value)) is not None}
        if len(totals) != 1 or sum((Decimal(_money(getattr(row, attribute))) for row in rows), Decimal(0)) not in totals:
            return
    proof = source_sha256 + ":" + _fingerprint(rows)
    for row in rows:
        row.validation_results.append(ExtractionValidationResult(validator=VALIDATOR, status="PASS",
            normalized_value=proof, reason="Independent full-document recipient/payment read agrees exactly with this source roster and both section totals."))
