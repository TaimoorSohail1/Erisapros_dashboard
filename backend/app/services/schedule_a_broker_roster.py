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

from app.models import ExtractionValidationResult, ScheduleABrokerRow, ScheduleABrokerRosterReview, SourceEvidence

VALIDATOR = "complete_source_broker_roster"


def separate_service_provider_disclosures(result, page_texts):
    """Keep Schedule C disclosures as evidence, never Schedule A payments."""
    disclosures = list(result.schedule_a_broker_roster.other_disclosures) if result.schedule_a_broker_roster else []
    disclosure_pages = set()
    mixed_pages = set()
    for page, text in page_texts:
        section = re.search(r"Schedule\s+C\b|Service\s+Provider\s+Information", text, re.I)
        if not section:
            continue
        if re.search(r"Insurance\s+Fees\s+and\s+commissions\s+paid\s+to\s+agents", text, re.I):
            mixed_pages.add(page)
        else:
            disclosure_pages.add(page)
        disclosure_text = text[section.start():]
        for match in re.finditer(r"Broker\s+Information:\s*([^\n]+)\n([^\n]+)(.*?)(?=Broker\s+Information:|Direct\s+Billed\s+Fees|\Z)", disclosure_text, re.I | re.S):
            name, address, remainder = match.groups()
            if not name.strip() or name.lstrip().startswith('$'):
                continue
            money = re.search(r"\$\s*([\d,]+\.\d{2})", remainder)
            disclosure = {"name": name.strip(), "address": address.strip(), "page": page,
                "source_form": "SERVICE_PROVIDER_DISCLOSURE", "amount": money.group(1).replace(',', '') if money else None,
                "payment_category": "UNCLASSIFIED_DIRECT_COMPENSATION", "source_text": text}
            if disclosure not in disclosures: disclosures.append(disclosure)
    original = list(result.schedule_a_broker_rows)
    raw = dict(result.raw) if isinstance(result.raw, dict) else {}
    names = {re.sub(r'\W', '', d['name']).casefold() for d in disclosures}
    excluded = [r for r in original if r.source_page in disclosure_pages or (
        r.source_page is None and raw.get('aetna_unprovided_part_i_compensation')
        and re.sub(r'\W', '', r.name).casefold() in names)]
    result.schedule_a_broker_rows = [r for r in original if r not in excluded]
    for row in result.schedule_a_broker_rows:
        if row.source_page in mixed_pages and re.sub(r'\W', '', row.name).casefold() in names:
            row.decision = 'REVIEW_REQUIRED'
            if not any(v.validator == 'source_section_ambiguity' for v in row.validation_results):
                row.validation_results.append(ExtractionValidationResult(validator='source_section_ambiguity',status='ERROR',
                    reason='Schedule A and separate service-provider information share this page. Confirm row-level section evidence before replacement.'))
    if disclosures or excluded:
        raw['excluded_service_provider_broker_candidates'] = [r.model_dump(mode='json') for r in excluded]
        raw['service_provider_disclosure_pages'] = sorted(disclosure_pages)
        result.raw = raw
        result.schedule_a_broker_roster = ScheduleABrokerRosterReview(other_disclosures=disclosures,
            reasons=["Schedule A completeness must be established independently of separate service-provider disclosures."],
            evidence=[SourceEvidence(provider="Service-provider disclosure", page=d['page'], source_text=d['source_text']) for d in disclosures])


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


def _organization_code(row):
    return re.sub(r'^0+(?=\d)', '', str(row.organization_code or '').strip() or '3')


def _fingerprint(rows):
    from app.services.schedule_a_customer_rules import canonical_broker_payment_purpose
    keys = [(*_key(row), _organization_code(row),
             canonical_broker_payment_purpose(row.commission_total, row.fee_total)) for row in rows]
    return hashlib.sha256(json.dumps(sorted(keys, key=lambda key: json.dumps(key)), separators=(",", ":")).encode()).hexdigest()


def broker_row_has_payment(row):
    return any(value is not None and Decimal(value) != 0
               for value in (_money(row.commission_total), _money(row.fee_total)))


def broker_roster_is_complete(rows, status=None):
    from app.services.broker_review_confirmation import confirmation_matches
    if confirmation_matches(rows, status):
        return True
    if not rows or any(row.decision == "REVIEW_REQUIRED" or any(v.status == "ERROR" for v in row.validation_results)
                       for row in rows):
        return False
    if any(_money(row.commission_total) is None or _money(row.fee_total) is None for row in rows):
        return False
    fingerprint = _fingerprint(rows)
    proofs = [{v.normalized_value for v in row.validation_results if v.validator == VALIDATOR and v.status == "PASS"
        and re.fullmatch(r"[a-f0-9]{64}:" + fingerprint + r":[a-f0-9]{64}", str(v.normalized_value or ""))} for row in rows]
    return bool(set.intersection(*proofs))


def note_incomplete_source_roster(result, reason):
    previous = result.schedule_a_broker_roster
    status = previous.model_copy(deep=True) if previous else ScheduleABrokerRosterReview()
    status.state, status.mode = 'NEEDS_REVIEW', 'PRESERVE'
    status.reasons = [reason]
    result.schedule_a_broker_roster = status


def attest_source_broker_roster(result, independent_rows, source_sha256):
    rows = result.schedule_a_broker_rows
    for row in rows:
        row.validation_results = [v for v in row.validation_results if v.validator != VALIDATOR]
    if not rows or not independent_rows:
        return note_incomplete_source_roster(result, 'No complete independent full-document broker read is available; existing brokers will be preserved.')
    if not re.fullmatch(r"[a-f0-9]{64}", str(source_sha256)):
        return note_incomplete_source_roster(result, 'The source PDF identity could not be verified; existing brokers will be preserved.')
    if len(result.schedule_a_worksheet_summaries) > 1:
        return note_incomplete_source_roster(result, 'Multiple Schedule A summaries require separate source-roster verification.')
    raw = result.raw if isinstance(result.raw, dict) else {}
    coverage = raw.get('independent_source_page_coverage') or {}
    pages = coverage.get('pages') or []
    count = coverage.get('page_count')
    if (coverage.get('source_sha256') != source_sha256 or type(count) is not int or count < 1
            or len(pages) != count or {p.get('page') for p in pages} != set(range(1,count+1))
            or any(p.get('readable') is not True or not re.fullmatch(r'[a-f0-9]{64}',str(p.get('text_sha256') or '')) for p in pages)):
        return note_incomplete_source_roster(result, 'Independent full-document page coverage is incomplete or unreadable; existing brokers will be preserved.')
    if int(raw.get("semantic_resolution", {}).get("group_count") or 0) > 1:
        return note_incomplete_source_roster(result, 'Multiple source contracts require separate broker-roster verification.')
    provider = next((stage for stage in raw.get("extraction_stages", [])
                     if stage.get("stage") == "structured_provider_normalized"), {})
    try:
        provider_rows = [ScheduleABrokerRow.model_validate(row) for row in provider.get("broker_rows", [])
                         if row.get('source_page') not in raw.get('service_provider_disclosure_pages', [])]
    except (ValueError, TypeError):
        return note_incomplete_source_roster(result, 'Provider broker information could not be validated against the source roster.')
    # Running the same local parser twice is not independent corroboration.
    if not provider_rows or Counter(_key(row) for row in provider_rows) != Counter(_key(row) for row in independent_rows):
        return note_incomplete_source_roster(result, 'Provider recipients or payment categories do not agree with the independent source read.')
    if any(row.decision == "REVIEW_REQUIRED" or row.confidence < .8
           or any(v.status == "ERROR" for v in row.validation_results)
           or not row.source_page or not any(e.page == row.source_page and e.source_text for e in row.evidence)
           or _money(row.commission_total) is None or _money(row.fee_total) is None
           for row in [*rows, *independent_rows]):
        return note_incomplete_source_roster(result, 'Broker classification, amounts, confidence or page evidence are insufficient for automatic replacement.')
    if Counter(_key(row) for row in rows) != Counter(_key(row) for row in independent_rows):
        return note_incomplete_source_roster(result, 'Final recipients or payment categories do not agree with the independent source read.')
    # Provider recovery may legitimately correct its original code. The FINAL
    # code must still agree with the independent source read (including the
    # approved blank-to-3 default); matching names and amounts alone cannot
    # approve a different recipient classification.
    source_key = lambda row: (*_key(row), _organization_code(row))
    if Counter(source_key(row) for row in rows) != Counter(source_key(row) for row in independent_rows):
        return note_incomplete_source_roster(result, 'Final broker organization codes do not agree with the independent source read.')
    for prefix, attribute in (("3b.", "commission_total"), ("3c.", "fee_total")):
        totals = {Decimal(value) for field in result.fields if field.field_name.lower().startswith(prefix)
                  and field.decision != "REVIEW_REQUIRED" and (value := _money(field.value)) is not None}
        if len(totals) != 1 or sum((Decimal(_money(getattr(row, attribute))) for row in rows), Decimal(0)) not in totals:
            return note_incomplete_source_roster(result, 'Broker payments do not reconcile with verified commission and fee section totals.')
    coverage_sha256 = hashlib.sha256(json.dumps(coverage, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    proof = source_sha256 + ":" + _fingerprint(rows) + ':' + coverage_sha256
    for row in rows:
        row.validation_results.append(ExtractionValidationResult(validator=VALIDATOR, status="PASS",
            normalized_value=proof, reason="Independent full-document recipient/payment read agrees exactly with this source roster and both section totals."))


def source_roster_review(rows, previous=None):
    """Derive source eligibility from current evidence, never saved row matches."""
    status = ScheduleABrokerRosterReview.model_validate(previous).model_copy(deep=True) if previous else ScheduleABrokerRosterReview()
    status.state, status.mode = "NEEDS_REVIEW", "PRESERVE"
    status.replacement_count = sum(broker_row_has_payment(row) for row in rows)
    status.roster_sha256 = _fingerprint(rows)
    status.evidence = list(status.evidence) + [e for row in rows for e in row.evidence if e not in status.evidence]
    if broker_roster_is_complete(rows, status):
        if not status.reviewer_confirmation:
            proof = next(v.normalized_value for v in rows[0].validation_results
                         if v.validator == VALIDATOR and v.status == "PASS")
            status.source_sha256 = proof.split(":")[0]
        status.state = "VERIFIED" if status.replacement_count else "VERIFIED_EMPTY"
        status.mode = "REPLACE"
        status.reasons = []
        status.evidence = [e for row in rows for e in row.evidence]
    else:
        status.reasons = list(dict.fromkeys([*status.reasons,
            "A complete, independently corroborated Schedule A broker list and classified payments are required. Existing brokers will be preserved."]))
    return status


def bind_roster_review(rows, previous, records, identity, can_replace, source_context=None):
    """Bind the preview to the exact selected destination and its fresh roster."""
    status = source_roster_review(rows, previous)
    selected = next((r for r in records if str(r.get('ftw_seq_no') or '') == str(identity.get('ftw_seq_no') or '')), None)
    from app.services.schedule_a_broker_matching import current_schedule_a_broker_rows
    current = current_schedule_a_broker_rows(selected)
    status.existing_count = len(current)
    binding = {"identity": identity, "selected_record": selected}
    if identity.get('new_schedule_a'):
        # Creation replaces the complete set. A sibling added/edited since
        # review must invalidate approval even though no selected row exists.
        binding['current_records'] = sorted(records, key=lambda r: str(r.get('ftw_seq_no') or ''))
    status.current_snapshot_sha256 = hashlib.sha256(json.dumps(binding, sort_keys=True, default=str).encode()).hexdigest()
    from app.services.broker_review_confirmation import disclosure_items, review_token
    status.other_disclosures = disclosure_items(status)
    confirmation = status.reviewer_confirmation
    if confirmation and (confirmation.get('source_context_sha256') != source_context
                         or confirmation.get('snapshot_sha256') != status.current_snapshot_sha256):
        status.reviewer_confirmation = None
        status.state, status.mode = 'NEEDS_REVIEW', 'PRESERVE'
        status.reasons = ['Source or FTWilliam data changed after broker review. Review the current list again.']
    status.review_context_sha256 = review_token(rows, status, source_context, status.current_snapshot_sha256)
    if status.mode == 'REPLACE' and not can_replace:
        status.state, status.mode = 'NEEDS_REVIEW', 'PRESERVE'
        status.reasons = ['Source or current FTWilliam broker information cannot safely be serialized for replacement.']
    return status
