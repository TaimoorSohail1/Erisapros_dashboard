from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

from app.models import ExtractionValidationResult, ScheduleABrokerRow, SourceEvidence


_STRUCTURED_ADDRESS_LABEL = re.compile(
    r"\b(?:ADDRESS(?:\s+LINE\s+[12])?|CITY|STATE|ST|ZIP(?:\s+CODE)?)\s*:",
    re.IGNORECASE,
)
_US_ZIP = re.compile(r"\b\d{5}(?:-\d{4})?\b")
SOURCE_REVIEW_VALIDATORS = {
    "source_zip_shape", "recipient_identity_ambiguity", "payment_classification_ambiguity",
    "business_rule_confirmation",
    "source_component_total",
    "source_organization_code_shape",
    "incomplete_source_compensation_table",
    "ocr_recipient_identity_conflict",
    "cigna_primary_source_identity",
}


def hold_local_ocr_recipient_conflicts(result, *, corroborating_rows=None) -> None:
    """A reconciled payment table does not prove an OCR recipient spelling.

    Keep the prior provider spelling for a unique same-address/payment anchor
    when the OCR spelling is a near-conflict. Never merge recipients or change
    amounts. Both source readings remain evidence and the row requires review.
    """
    raw = result.raw if isinstance(result.raw, dict) else {}
    if not raw.get('local_ocr_pages'):
        return
    stage = next((item for item in raw.get('extraction_stages', [])
                  if item.get('stage') == 'structured_provider_normalized'), {})
    prior = [ScheduleABrokerRow.model_validate(row) for row in stage.get('broker_rows', [])]
    normalize = lambda value: re.sub(r'[^A-Z0-9]', '', str(value or '').upper())
    def anchor(row):
        keys = ('address_line_1', 'address_line_2', 'city', 'state', 'zip_code')
        if not all(getattr(row, key) for key in ('name', 'address_line_1', 'city', 'state', 'zip_code')):
            return None
        return tuple(normalize(getattr(row, key)) for key in keys)
    current_counts = Counter(anchor(row) for row in result.schedule_a_broker_rows)
    prior_counts = Counter(anchor(row) for row in prior)
    def money(value):
        if value in (None, ''):
            return None
        try:
            return Decimal(re.sub(r'[$,\s]', '', str(value)))
        except InvalidOperation:
            return None
    conflicts = []
    for row in result.schedule_a_broker_rows:
        key = anchor(row)
        if key is None or current_counts[key] != 1 or prior_counts[key] != 1:
            continue
        previous = next(candidate for candidate in prior if anchor(candidate) == key)
        if row.source_page and previous.source_page and row.source_page != previous.source_page:
            continue
        left, right = normalize(row.name), normalize(previous.name)
        if left == right:
            continue
        amounts = [(money(getattr(row, k)),money(getattr(previous, k)))
                   for k in ('commission_total','fee_total')]
        # Missing and zero stay distinct source values. They may share an
        # identity anchor only when a known nonzero payment agrees and no
        # category has a conflicting known nonzero value.
        if not any(a is not None and b is not None and a == b and a != 0 for a,b in amounts):
            continue
        if any((a or Decimal(0)) != (b or Decimal(0)) for a,b in amounts):
            continue
        observed = row.name
        near_spelling = min(len(left),len(right)) >= 8 and SequenceMatcher(None,left,right).ratio() >= .9
        if near_spelling:
            row.name = previous.name
        # Re-read source pixels only at this narrow conflict seam. Similar
        # spelling alone is not approval, nor is provider confidence. Require a
        # unique full-address/page/payment anchor from the additional reader.
        independent = corroborating_rows(row.source_page) if near_spelling and row.source_page and corroborating_rows else []
        anchored = [candidate for candidate in independent if anchor(candidate) == key
                    and candidate.source_page == row.source_page]
        corroborated = len(anchored) == 1 and normalize(anchored[0].name) == right and all(
            money(getattr(anchored[0], category)) is not None
            and money(getattr(anchored[0], category)) == money(getattr(previous, category))
            for category in ('commission_total', 'fee_total'))
        if corroborated:
            evidence = SourceEvidence(provider='Corroborated recipient identity', page=row.source_page,
                source_text=f'Original local OCR: {observed}; provider: {previous.name}; separate lossless source OCR: {anchored[0].name}. Full address, page and category amounts match. No payment, purpose or organization code is replaced.')
            row.evidence.extend([*previous.evidence, *anchored[0].evidence, evidence])
            row.validation_results.append(ExtractionValidationResult(validator='ocr_recipient_identity_corroboration',
                status='PASS', reason='Separate source OCR corroborates the provider name at the unique full recipient/payment anchor.'))
            conflicts.append({'local_ocr_name': observed, 'provider_name': previous.name,
                'independent_name': anchored[0].name, 'page': row.source_page,
                'address_line_1': row.address_line_1, 'decision': 'CORROBORATED'})
            for field in result.fields:
                if field.field_name.startswith('3a.') and normalize(field.value) in {left, right}:
                    field.value = previous.name
                    field.candidate_values = [previous.name]
                    field.evidence.append(evidence)
            continue
        reason = 'Local OCR and provider spell the same uniquely anchored recipient differently. Preserve both readings and confirm the source before an automatic update.'
        row.validation_results.append(ExtractionValidationResult(
            validator='ocr_recipient_identity_conflict',status='ERROR',reason=reason))
        row.decision = 'REVIEW_REQUIRED'
        row.evidence.extend(previous.evidence)
        row.evidence.append(SourceEvidence(provider='Independent OCR recipient conflict',page=row.source_page,
            source_text=f'Local OCR name: {observed}; prior provider name: {previous.name}. {reason}'))
        conflicts.append({'local_ocr_name':observed,'provider_name':previous.name,
            'display_name':row.name,'near_spelling':near_spelling,
            'page':row.source_page,'address_line_1':row.address_line_1,'decision':'REVIEW_REQUIRED'})
        for field in result.fields:
            if field.field_name.startswith('3a.'):
                field.validation_results.append(ExtractionValidationResult(
                    validator='ocr_recipient_identity_conflict',status='ERROR',reason=reason))
                field.decision='REVIEW_REQUIRED'
    if conflicts:
        raw['local_ocr_recipient_conflicts']=conflicts
        result.raw=raw


def hold_invalid_source_organization_codes(rows) -> None:
    """Keep an explicit invalid code distinct from the approved blank default."""
    for row in rows:
        value = str(row.organization_code or '').strip()
        if not value or (re.fullmatch(r'[0-9]{1,2}',value) and int(value)<=9):
            continue
        if not any(v.validator=='source_organization_code_shape' for v in row.validation_results):
            row.validation_results.append(ExtractionValidationResult(
                validator='source_organization_code_shape',status='ERROR',
                reason='Explicit source organization code cannot be represented by the destination numeric-code contract. Preserve it for review; do not treat it as missing or apply the blank-code default.',
            ))
        row.decision='REVIEW_REQUIRED'


def hold_possible_recipient_aliases(rows) -> None:
    """Recognize possible street aliases for review, never as merge permission."""
    aliases = {"CIRCLE": "CIR", "ROAD": "RD", "STREET": "ST", "AVENUE": "AVE",
               "BOULEVARD": "BLVD", "DRIVE": "DR", "LANE": "LN", "SUITE": "STE"}
    groups = {}
    def tokens(value):
        return " ".join(re.findall(r"[A-Z0-9]+", str(value or "").upper()))
    for row in rows:
        if not all((row.name, row.address_line_1, row.city, row.state, row.zip_code)):
            continue
        street = " ".join(aliases.get(t, t) for t in tokens(row.address_line_1).split())
        key = (tokens(row.name), street, tokens(row.address_line_2), tokens(row.city),
               tokens(row.state), re.sub(r"\D", "", row.zip_code))
        groups.setdefault(key, []).append(row)
    for group in groups.values():
        if len({tokens(r.address_line_1) for r in group}) < 2:
            continue
        for row in group:
            if not any(v.validator == "recipient_identity_ambiguity" for v in row.validation_results):
                row.validation_results.append(ExtractionValidationResult(
                    validator="recipient_identity_ambiguity", status="ERROR",
                    reason="Distinct printed addresses may be abbreviations of the same recipient. Preserve the physical rows and confirm aggregation before an update.",
                ))
            row.decision = "REVIEW_REQUIRED"


def hold_invalid_source_zip_codes(rows) -> None:
    """Never repair missing ZIP digits by guessing; keep the source for review."""
    for row in rows:
        value = str(row.zip_code or "").strip()
        if not value or re.fullmatch(r"(?:\d{5}(?:-\d{4})?|\d{9})", value):
            continue
        if not any(v.validator == "source_zip_shape" for v in row.validation_results):
            row.validation_results.append(ExtractionValidationResult(
                validator="source_zip_shape", status="ERROR",
                reason="Printed ZIP is incomplete or not a supported US ZIP shape; retain it and confirm the source instead of inventing digits.",
            ))
        row.decision = "REVIEW_REQUIRED"


def broker_address_semantic_issue(field_name: str, value: object) -> str | None:
    """Reject parser fragments that combine structured broker address fields."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return None

    normalized_field = str(field_name or "").strip().lower()
    if normalized_field == "city" and (
        _STRUCTURED_ADDRESS_LABEL.search(text)
        or _US_ZIP.search(text)
        or re.search(r",\s*[A-Z]{2}(?:\s|$)", text, re.IGNORECASE)
    ):
        return (
            "expected a city name only; move any street, state, or ZIP data "
            "to the matching broker address field"
        )

    if normalized_field in {"address_line_1", "address_line_2"} and re.search(
        r"\b(?:CITY|STATE|ST|ZIP(?:\s+CODE)?)\s*:",
        text,
        re.IGNORECASE,
    ):
        return (
            "expected a street/address line only; move any city, state, or ZIP data "
            "to the matching broker address field"
        )
    return None
