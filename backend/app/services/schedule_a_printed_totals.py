"""Read unambiguous printed Schedule A totals without relying on carrier names."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from decimal import Decimal
import hashlib
import logging
import re

from app.models import DocumentType, ExtractedField, ExtractedFieldStatus, Filing, FormType, NormalizedExtractionField, SourceEvidence
from app.services.ftwilliams_tags import resolve_ftw_current_value, resolve_ftw_update_tag
from app.services.storage import StorageService


COVERED_TOTAL_PROVIDER = "Printed Schedule A covered-person total"
_LOGGER = logging.getLogger(__name__)
_LEGACY_SOURCE_HOLDS = {
    "WlfrTotChargesPaidAmt": (
        "10a", "Automatic extraction requires explicit nonexperience-rated/line 10 source evidence.",
    ),
    "InsPrsnCoveredEoyCnt": (
        "1e", "Enrollment tiers or an explicit covered-lives total do not support this persons-covered value.",
    ),
}
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)"
_SECTION_10 = re.compile(r"(?im)^[ \t]*(?:(?:section|line)\s+)?10[.:\s-]+non[- ]?experience[- ]rated\s+contracts?\s*$")
_NEXT_SECTION = re.compile(r"(?im)^[ \t]*(?:section\s+\d+|part\s+(?:[IVX]+)\b)")
_PREMIUM = re.compile(
    rf"(?im)^[ \t]*(?:(?:\(a\)|10\s*\.?\s*a\.?|a\.)\s*)?"
    rf"total\s+premiums?(?:\s+or\s+subscriptions?\s+charges)?\s+paid\s+to\s+(?:the\s+)?carrier"
    rf"[:. \t]*\$?[ \t]*(?P<amount>{_NUMBER}(?:\.\d{{1,2}})?)[ \t]*$",
)
_COVERED = re.compile(
    rf"approximate\s+number\s+of[ \t]+total\s*\(e\)[ \t]+(?P<total>{_NUMBER})[ \t]*\n"
    rf"(?:[^\n\d]*\n){{0,2}}[^\n]*persons\s+covered\s+at\s+end[ \t]+employees[ \t]+(?P<employees>{_NUMBER})[ \t]*\n"
    rf"(?:[^\n\d]*\n){{0,2}}[ \t]*(?:of\s+)?(?:policy|contract)\s+year[ \t]+dependents[ \t]+(?P<dependents>{_NUMBER})(?![\d,.])",
    re.I,
)


def _number(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def covered_total(source: str) -> Decimal | None:
    matches = list(_COVERED.finditer(source))
    if len(matches) != 1:
        return None
    match = matches[0]
    total = _number(match["total"])
    return total if total == _number(match["employees"]) + _number(match["dependents"]) else None


def read_printed_totals(pages: list[tuple[int, str]]) -> dict[str, tuple[Decimal, SourceEvidence]]:
    totals: dict[str, list[tuple[Decimal, SourceEvidence]]] = defaultdict(list)
    for page, text in pages:
        if match := _COVERED.search(text):
            if (count := covered_total(text)) is not None:
                totals["1e"].append((count, SourceEvidence(provider=COVERED_TOTAL_PROVIDER, page=page, source_text=match[0])))
        for heading in _SECTION_10.finditer(text):
            section = _NEXT_SECTION.split(text[heading.end():], maxsplit=1)[0]
            for match in _PREMIUM.finditer(section):
                totals["10a"].append((_number(match["amount"]), SourceEvidence(
                    provider="Printed Schedule A line 10a", page=page,
                    source_text=f"{heading[0].strip()}\n{match[0].strip()}",
                )))
    return {code: matches[0] for code, matches in totals.items() if len(matches) == 1}


def attach_printed_total_evidence(fields: list[NormalizedExtractionField], pages: list[tuple[int, str]]) -> None:
    totals = read_printed_totals(pages)
    for field in fields:
        code = field.field_name.split(".", 1)[0].strip().lower()
        if code not in totals:
            continue
        value, evidence = totals[code]
        current = re.sub(r"[$,\s]", "", field.value or "")
        if not re.fullmatch(r"\d+(?:\.\d{1,2})?", current) or Decimal(current) != value:
            continue
        if evidence not in field.evidence:
            field.evidence.append(evidence)
        field.page = evidence.page
        field.source_text = evidence.source_text


async def refresh_verified_totals(repo, filing: Filing, fields: list[ExtractedField], current: dict[str, str]) -> list[ExtractedField]:
    """Restore proposals withheld by the two legacy label checks after rereading the same PDF."""
    pending = {}
    for field in fields:
        tag = resolve_ftw_update_tag(field)
        if (field.id and field.form_type == FormType.SCHEDULE_A and tag in _LEGACY_SOURCE_HOLDS
                and field.status == ExtractedFieldStatus.LOW_CONFIDENCE and not field.proposed_value
                and not resolve_ftw_current_value(field, current).strip()):
            code, reason = _LEGACY_SOURCE_HOLDS[tag]
            if field.status_reason == "Extraction requires review; automatic proposal withheld. " + reason:
                pending[field.id] = (field, tag, code)
    if not pending:
        return fields
    documents = [doc for doc in filing.package_documents if doc.get("document_type") == DocumentType.SCHEDULE_A]
    if len(documents) != 1:
        return fields
    document = documents[0]
    expected_hash = document.get("source_sha256")
    if not expected_hash or not (document.get("file_name") or "").lower().endswith(".pdf"):
        return fields

    def load_totals():
        from app.services.extractor import extract_pdf_layout_text_pages
        data = StorageService().load_pdf(document["s3_key"], document.get("s3_bucket"), document.get("storage_path"))
        if hashlib.sha256(data).hexdigest() != expected_hash:
            return {}
        return read_printed_totals(extract_pdf_layout_text_pages(data))

    try:
        totals = await asyncio.to_thread(load_totals)
    except Exception:
        _LOGGER.warning("Could not verify stored Schedule A totals for filing %s", filing.id)
        return fields

    from app.services.ftwilliams_contract import FTWPayloadValidationError, normalize_ftw_update_value
    recovered = {}
    for field_id, (field, tag, code) in pending.items():
        if code not in totals:
            continue
        amount, evidence = totals[code]
        try:
            normalized = normalize_ftw_update_value(FormType.SCHEDULE_A, tag, field.value)
        except FTWPayloadValidationError:
            continue
        if not normalized or Decimal(normalized) != amount:
            continue
        saved = await repo.update_field(filing.id, field_id, field.value, status=ExtractedFieldStatus.MATCHED,
            status_reason=f"Printed Schedule A total verified on page {evidence.page}.", confidence=0.99,
            expected_unproposed_value=field.value)
        if saved:
            recovered[field_id] = saved
    return [recovered.get(field.id, field) for field in fields]
