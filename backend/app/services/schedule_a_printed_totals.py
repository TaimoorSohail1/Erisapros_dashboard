"""Read unambiguous printed Schedule A totals without relying on carrier names."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from decimal import Decimal
import hashlib
import logging
import re

from app.models import (DocumentType, ExtractedField, ExtractedFieldStatus, ExtractionJobStatus, Filing, FilingStatus,
    FormType, NormalizedExtractionField, NormalizedExtractionResult, SourceEvidence)
from app.services.ftwilliams_contract import FTWPayloadValidationError, normalize_ftw_update_value
from app.services.ftwilliams_tags import resolve_ftw_current_value, resolve_ftw_update_tag
from app.services.groundx_checkpoints import EVENT as GROUNDX_CHECKPOINT_EVENT
from app.services.storage import StorageService


COVERED_TOTAL_PROVIDER = "Printed Schedule A covered-person total"
_LOGGER = logging.getLogger(__name__)
_SOURCE_EVIDENCE_HOLD = "Automatic extraction requires a source page and source text that supports the extracted value."
_WITHHELD = "Extraction requires review; automatic proposal withheld. "
_ALIAS_CONFLICT = _WITHHELD + "Multiple source candidates disagree for this field."
_ALIAS_CODES = {"InsPrsnCoveredEoyCnt": "1e", "InsPolicyToDate": "1g"}
_LEGACY_SOURCE_HOLDS = {
    "WlfrTotChargesPaidAmt": (
        "10a", frozenset({"Automatic extraction requires explicit nonexperience-rated/line 10 source evidence.", _SOURCE_EVIDENCE_HOLD}),
    ),
    "InsPrsnCoveredEoyCnt": (
        "1e", frozenset({"Enrollment tiers or an explicit covered-lives total do not support this persons-covered value.", _SOURCE_EVIDENCE_HOLD}),
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
_EXPLICIT_PAID_PREMIUM = re.compile(
    rf"(?im)^[ \t]*(?:(?:\(a\)|10\s*\.?\s*a\.?|a\.)\s*)?"
    rf"total\s+premiums?[ \t]*\*?[ \t]+or[ \t]+subscriptions?[ \t]+charges[ \t]+paid[ \t]+to[ \t]+(?:the[ \t]+)?carrier"
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
        premium_lines = {
            match.span(): (_number(match["amount"]), match[0].strip())
            for match in _EXPLICIT_PAID_PREMIUM.finditer(text)
        }
        for heading in _SECTION_10.finditer(text):
            section = _NEXT_SECTION.split(text[heading.end():], maxsplit=1)[0]
            for match in _PREMIUM.finditer(section):
                span = (heading.end() + match.start(), heading.end() + match.end())
                premium_lines[span] = (_number(match["amount"]), f"{heading[0].strip()}\n{match[0].strip()}")
        for amount, source in premium_lines.values():
            totals["10a"].append((amount, SourceEvidence(
                provider="Printed Schedule A line 10a", page=page, source_text=source,
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
    """Recover verified values from a retained PDF without resubmitting it."""
    if filing.status in {FilingStatus.DELETED, FilingStatus.SUPERSEDED}:
        return fields
    pending = {}
    aliases = {}
    for field in fields:
        if not field.id or field.form_type != FormType.SCHEDULE_A or field.status != ExtractedFieldStatus.LOW_CONFIDENCE or field.proposed_value:
            continue
        tag = resolve_ftw_update_tag(field)
        current_value = resolve_ftw_current_value(field, current).strip()
        if tag in _ALIAS_CODES and field.status_reason == _ALIAS_CONFLICT:
            value = _normalized(tag, field.value)
            if value and (not current_value or value == _normalized(tag, current_value)):
                aliases[field.id] = (field, tag, _ALIAS_CODES[tag])
        if tag in _LEGACY_SOURCE_HOLDS and not current_value:
            code, allowed_reasons = _LEGACY_SOURCE_HOLDS[tag]
            status_reason = field.status_reason or ""
            if status_reason.startswith(_WITHHELD) and set(status_reason[len(_WITHHELD):].split("; ")) <= allowed_reasons:
                pending[field.id] = (field, tag, code)
    if not pending and not aliases:
        return fields
    documents = [doc for doc in filing.package_documents if doc.get("document_type") == DocumentType.SCHEDULE_A]
    if len(documents) != 1:
        return fields
    document = documents[0]
    expected_hash = document.get("source_sha256")
    if not expected_hash or not (document.get("file_name") or "").lower().endswith(".pdf"):
        return fields

    def load_source():
        from app.services.extractor import extract_pdf_layout_text_pages
        data = StorageService().load_pdf(document["s3_key"], document.get("s3_bucket"), document.get("storage_path"))
        if hashlib.sha256(data).hexdigest() != expected_hash:
            return b"", []
        return data, extract_pdf_layout_text_pages(data)

    try:
        data, pages = await asyncio.to_thread(load_source)
    except Exception:
        _LOGGER.warning("Could not verify stored Schedule A totals for filing %s", filing.id)
        return fields

    totals = read_printed_totals(pages) if pending else {}
    verified = {}
    if aliases and data:
        try:
            stages = await _bound_extraction_stages(repo, filing.id, document["file_name"], expected_hash)
            if stages:
                verified = await asyncio.to_thread(_verified_alias_evidence, aliases, stages, data, document["file_name"], pages)
        except Exception:
            _LOGGER.warning("Could not reconcile stored Schedule A aliases for filing %s", filing.id)

    recovered = {}
    for field_id, (field, tag, code) in pending.items():
        if code not in totals:
            continue
        amount, evidence = totals[code]
        normalized = _normalized(tag, field.value)
        if normalized and Decimal(normalized) == amount:
            verified[field_id] = evidence
    for field in fields:
        if field.id not in verified:
            continue
        evidence = verified[field.id]
        saved = await repo.update_field(filing.id, field.id, field.value, status=ExtractedFieldStatus.MATCHED,
            status_reason=f"Schedule A source value verified on page {evidence.page}.", confidence=0.99,
            expected_unproposed_value=field.value, expected_status_reason=field.status_reason, verified_evidence=evidence)
        if saved:
            recovered[field.id] = saved
    return [recovered.get(field.id, field) for field in fields]


def _normalized(tag, value):
    try:
        return normalize_ftw_update_value(FormType.SCHEDULE_A, tag, value)
    except FTWPayloadValidationError:
        return ""


async def _bound_extraction_stages(repo, filing_id, file_name, source_hash):
    jobs = await repo.list_extraction_jobs(filing_id)
    if any(job.status not in {ExtractionJobStatus.COMPLETED, ExtractionJobStatus.FAILED} for job in jobs):
        return []
    job = max(jobs, key=lambda item: item.created_at, default=None)
    if not job or job.status != ExtractionJobStatus.COMPLETED or not job.id:
        return []
    for event in await repo.list_audit_logs(filing_id):
        details = event.details or {}
        binding = details.get("binding") or {}
        if (event.event == GROUNDX_CHECKPOINT_EVENT and details.get("job_id") == job.id
                and details.get("status") == "ACCEPTED" and details.get("process_id")
                and binding.get("file_name") == file_name and binding.get("form_type") == FormType.SCHEDULE_A.value
                and binding.get("source_sha256") == source_hash):
            return await repo.schedule_a_extraction_stages(filing_id, job.id, file_name)
    return []


def _verified_alias_evidence(aliases, stages, data, file_name, pages):
    from app.services.extractor import extract_fields_from_document_text
    from app.services.schedule_a_extraction_pipeline import resolve_schedule_a_result

    def by_code(fields):
        values = defaultdict(list)
        for field in fields:
            values[field.field_name.split(".", 1)[0].strip().lower()].append(field)
        return {code: matches[0] for code, matches in values.items() if len(matches) == 1}

    traces = {stage["stage"]: by_code([NormalizedExtractionField.model_validate(row) for row in stage.get("fields", [])])
        for stage in stages if stage.get("stage") in {"structured_provider_normalized", "local_parser_candidates", "final_normalized"}}
    if len(traces) != 3:
        return {}
    result = resolve_schedule_a_result(NormalizedExtractionResult(provider="Stored PDF replay",
        fields=extract_fields_from_document_text(data, file_name)))
    fresh = by_code(result.fields)
    begin = fresh.get("1f")
    end = fresh.get("1g")
    if not begin or not end or begin.decision != "AUTOMATIC" or end.decision != "AUTOMATIC":
        return {}

    def candidates(item):
        return {value.strip() for value in [item.value, *item.candidate_values] if value and value.strip()} if item else set()

    verified = {}
    for field_id, (field, tag, code) in aliases.items():
        value = _normalized(tag, field.value)
        source = fresh.get(code)
        if not source or source.decision != "AUTOMATIC" or not source.page or not source.source_text:
            continue
        provider = candidates(traces["structured_provider_normalized"].get(code))
        local = candidates(traces["local_parser_candidates"].get(code))
        final = candidates(traces["final_normalized"].get(code))
        if not value or {_normalized(tag, v) for v in provider} != {value} or local != final or field.value not in local:
            continue
        if {_normalized(tag, v) for v in candidates(source)} != {value}:
            continue
        rejected = local - {field.value}
        page_text = dict(pages).get(source.page, "")
        dates = [begin.value, end.value]
        if not rejected or not all(date in page_text for date in dates):
            continue
        if code == "1g" and rejected != {begin.value}:
            continue
        if code == "1e" and not rejected <= {date.split("/")[0] for date in dates}:
            continue
        verified[field_id] = SourceEvidence(provider="Source-bound alias replay", page=source.page, source_text=source.source_text)
    return verified
