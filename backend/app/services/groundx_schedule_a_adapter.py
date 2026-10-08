"""Normalize GroundX/EyeLevel X-Ray output for the Schedule A pipeline.

X-Ray may return semantic records in ``json`` or serialize the same record at
the start of ``suggestedText``.  This module accepts both representations and
turns their carrier, contract, premium, and compensation semantics into the
existing extraction models consumed downstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any, Iterable

from app.models import (
    NormalizedExtractionField,
    ScheduleABrokerMoneyRow,
    ScheduleABrokerRow,
    SourceEvidence,
)


PROVIDER = "GroundX X-Ray adapter"


@dataclass(frozen=True)
class GroundXScheduleAAdapterResult:
    fields: list[NormalizedExtractionField]
    broker_rows: list[ScheduleABrokerRow]
    classification_signals: list[str]
    schedule_a_count: int = 1


def adapt_groundx_schedule_a_xray(raw: Any) -> GroundXScheduleAAdapterResult:
    """Return normalized Schedule A values from one X-Ray document payload."""

    if not isinstance(raw, dict):
        return GroundXScheduleAAdapterResult([], [], [], 0)

    fields: list[NormalizedExtractionField] = []
    broker_rows: list[ScheduleABrokerRow] = []
    all_text: list[str] = []
    contract_policy_numbers: set[str] = set()
    explicit_schedule_a_count = 0
    meaningful_claim_amount = False

    for chunk in raw.get("chunks") or []:
        if not isinstance(chunk, dict):
            continue
        page = _page_number(chunk)
        chunk_text = _chunk_text(chunk)
        if chunk_text:
            all_text.append(chunk_text)
        records = _chunk_records(chunk)
        explicit_schedule_a_count = max(
            explicit_schedule_a_count,
            _explicit_schedule_a_record_count(chunk),
        )
        for record in records:
            meaningful_claim_amount = meaningful_claim_amount or _record_has_meaningful_claim_amount(record)
            evidence_text = _record_evidence(record, chunk_text)
            evidence = SourceEvidence(provider=PROVIDER, page=page, source_text=evidence_text)

            carrier = _first_scalar(
                record,
                "company_name",
                "company",
                "insurance_carrier_name",
                "carrier_name",
                "name_of_insurance_carrier",
            )
            _add_field(fields, "1a. Name of Insurance Company", carrier, page, evidence_text, 0.97)

            ein = _normalize_ein(_first_scalar(record, "carrier_ein", "insurance_ein", "ein", "b_ein"))
            _add_field(fields, "1b. Insurance Carrier EIN", ein, page, evidence_text, 0.97)

            naic = _digits(_first_scalar(record, "naic_code", "naic", "c_naic_code"))
            _add_field(fields, "1c. NAIC Code", naic, page, evidence_text, 0.97)

            policy = _first_scalar(
                record,
                "policy_number",
                "contract_number",
                "contract_or_identification_number",
                "d_contract_or_identification_number",
            )
            policy = _complete_numeric_policy_from_evidence(
                policy,
                "\n".join(part for part in (chunk_text, evidence_text) if part),
            )
            if policy and _record_is_schedule_a_contract(record, policy):
                contract_policy_numbers.add(policy)
            _add_field(fields, "1d. Contract/Policy Number", policy, page, evidence_text, 0.97)

            covered = _digits(
                _first_scalar(
                    record,
                    "ending_covered_lives",
                    "covered_lives_ending",
                    "persons_covered_end",
                    "persons_covered_at_end",
                    "e_approximate_number_of_persons_covered_at_end_of_policy_or_contract_year",
                )
            )
            _add_field(fields, "1e. Persons Covered (End of Policy Year)", covered, page, evidence_text, 0.96)

            begin = _first_scalar(
                record,
                "policy_year_beginning_date",
                "policy_year_begin",
                "contract_year_from",
                "f_policy_or_contract_year_from",
            )
            end = _first_scalar(
                record,
                "policy_year_ending_date",
                "policy_year_end",
                "contract_year_to",
                "g_policy_or_contract_year_to",
            )
            period = _first_scalar(record, "policy_contract_year", "contract_year", "policy_year")
            period_begin, period_end = _date_range(period)
            _add_field(fields, "1f. Policy Year Beginning Date", begin or period_begin, page, evidence_text, 0.96)
            _add_field(fields, "1g. Policy Year Ending Date", end or period_end, page, evidence_text, 0.96)

            total_premium = _money(
                _first_scalar(
                    record,
                    "total_premium",
                    "total_premiums",
                    "gross_premium_total",
                    "total_premiums_or_subscription_charges_paid_to_carrier",
                )
            )
            if not total_premium and _normalized_key(_first_scalar(record, "row_label")) == "total premium":
                total_premium = _money(_first_scalar(record, "premium", "total_premium"))
            _add_field(
                fields,
                "10a. Total premiums or subscription charges paid to carrier",
                total_premium,
                page,
                evidence_text,
                0.96,
            )

            broker = _broker_row(record, page, evidence, chunk_text)
            if broker:
                broker_rows.append(broker)

    combined_text = "\n".join(all_text)
    _add_text_fallbacks(fields, combined_text)

    organization_code = _extract_organization_code(combined_text)
    if organization_code:
        _add_field(
            fields,
            "3e. Organizational Code",
            organization_code,
            1,
            _matching_line(combined_text, r"ORG\.?\s*(?:NUMBER|CODE)"),
            0.94,
        )
        broker_rows = [
            row.model_copy(update={"organization_code": row.organization_code or organization_code})
            for row in broker_rows
        ]

    broker_rows = _dedupe_brokers(broker_rows)
    if broker_rows:
        total_commission = _sum_money(row.commission_total for row in broker_rows)
        total_fees = _sum_money(row.fee_total for row in broker_rows)
        broker_source = "Broker rows normalized from GroundX X-Ray"
        _replace_field(fields, "3a. Name of Agent/Broker/Person", broker_rows[0].name, broker_rows[0].source_page, broker_source)
        _replace_field(fields, "3b. Amount of Commissions", total_commission, broker_rows[0].source_page, broker_source)
        _replace_field(fields, "3c. Amount of Fees", total_fees, broker_rows[0].source_page, broker_source)
        purpose = _broker_purpose(total_commission, total_fees)
        if purpose:
            _replace_field(fields, "3d. Purpose", purpose, broker_rows[0].source_page, broker_source)

    fields = _dedupe_fields(fields)
    signals: set[str] = set()
    if any(field.field_name.startswith("10a.") and _nonzero(field.value) for field in fields):
        signals.add("PREMIUM_AMOUNT_PRESENT")
    if meaningful_claim_amount:
        signals.add("CLAIM_AMOUNT_PRESENT")
    if re.search(r"\bnon\s*experience[- ]?rated\b", combined_text, re.IGNORECASE):
        signals.add("EXPLICIT_NONEXPERIENCE_RATED")
    schedule_a_count = max(1, explicit_schedule_a_count, len(contract_policy_numbers))
    if schedule_a_count > 1:
        signals.add("MULTIPLE_SCHEDULE_A_RECORDS")

    return GroundXScheduleAAdapterResult(
        fields=fields,
        broker_rows=broker_rows,
        classification_signals=sorted(signals),
        schedule_a_count=schedule_a_count,
    )


def _record_is_schedule_a_contract(record: dict[str, Any], policy: str) -> bool:
    """Count contract records, not every policy-like reference in a packet."""

    normalized_policy = " ".join(str(policy or "").split()).strip()
    if not normalized_policy or re.search(r"\b(?:see above|not available|n/?a)\b", normalized_policy, re.IGNORECASE):
        return False
    if not re.search(r"[A-Za-z0-9]", normalized_policy):
        return False

    carrier = _first_scalar(
        record,
        "company_name",
        "company",
        "insurance_carrier_name",
        "carrier_name",
        "name_of_insurance_carrier",
    )
    period = _first_scalar(
        record,
        "policy_contract_year",
        "contract_year",
        "policy_year",
        "policy_year_beginning_date",
        "contract_year_from",
    )
    identity = _first_scalar(
        record,
        "carrier_ein",
        "insurance_ein",
        "ein",
        "naic_code",
        "naic",
        "ending_covered_lives",
        "persons_covered_at_end",
        "total_premium",
        "total_premiums",
    )
    record_type = _normalized_key(_first_scalar(record, "record_type", "type"))
    schedule_record_type = record_type in {
        "policy",
        "contract",
        "schedule a",
        "schedule a record",
        "table summary",
    }
    return bool((carrier and (period or identity)) or (schedule_record_type and (period or carrier)))


def _record_has_meaningful_claim_amount(record: dict[str, Any]) -> bool:
    """Treat populated claim values as evidence, never blank template labels."""

    value = _first_scalar(
        record,
        "claims_paid",
        "claim_amount",
        "claims_amount",
        "claim_reserves",
        "increase_decrease_in_claim_reserves",
        "incurred_claims",
        "claims_charged",
        "benefit_charges",
        "9b(1). Benefit Charges (1) Claims paid",
        "9b(2). Increase (decrease) in claim reserves",
        "9b(3). Incurred claims (add(1) and (2))",
        "9b(4). Claims Charged",
    )
    return _nonzero(_money(value))


def _complete_numeric_policy_from_evidence(
    policy: str | None,
    evidence_text: str,
) -> str | None:
    """Restore a numeric suffix that X-Ray omitted from its value field.

    Some carrier tables visually split a long policy number into two adjacent
    groups. X-Ray can return only the first group as ``policy_number`` while
    retaining the full labeled value in the source text. Only extend an
    all-numeric candidate when the labeled evidence contains a longer value
    with that exact prefix; never infer or pad a number.
    """

    candidate = str(policy or "").strip()
    candidate_digits = re.sub(r"\D", "", candidate)
    if not candidate_digits or not re.fullmatch(r"[0-9\s]+", candidate):
        return policy
    match = re.search(
        r"(?:Policy\s+Number|Contract\s+or\s+identification\s+number)"
        r"[ \t]*:[ \t]*(?P<value>[0-9][0-9 \t]{5,})",
        str(evidence_text or ""),
        re.IGNORECASE,
    )
    if not match:
        return policy
    evidence_digits = re.sub(r"\D", "", match.group("value"))
    if len(evidence_digits) > len(candidate_digits) and evidence_digits.startswith(candidate_digits):
        return evidence_digits
    return policy


def _explicit_schedule_a_record_count(chunk: dict[str, Any]) -> int:
    values: list[Any] = []
    raw_json = chunk.get("json")
    if isinstance(raw_json, (dict, list)):
        values.append(raw_json)
    elif isinstance(raw_json, str):
        parsed = _embedded_json(raw_json)
        if parsed is not None:
            values.append(parsed)
    suggested = _embedded_json(chunk.get("suggestedText"))
    if suggested is not None:
        values.append(suggested)

    largest = 0
    for value in values:
        stack = [value]
        while stack:
            current = stack.pop()
            if isinstance(current, list):
                stack.extend(current)
                continue
            if not isinstance(current, dict):
                continue
            for key, nested in current.items():
                normalized = _normalized_key(key)
                if normalized in {"schedule as", "schedule a records"} and isinstance(nested, list):
                    largest = max(largest, len([item for item in nested if isinstance(item, dict)]))
                if isinstance(nested, (dict, list)):
                    stack.append(nested)
    return largest


def _chunk_records(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[Any] = []
    raw_json = chunk.get("json")
    if isinstance(raw_json, (dict, list)):
        values.append(raw_json)
    elif isinstance(raw_json, str):
        parsed = _embedded_json(raw_json)
        if parsed is not None:
            values.append(parsed)
    parsed_suggested = _embedded_json(chunk.get("suggestedText"))
    if parsed_suggested is not None:
        values.append(parsed_suggested)

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        for record in _walk_records(value):
            key = json.dumps(record, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            records.append(record)
    return records


def _walk_records(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _walk_records(item)
        return
    if not isinstance(value, dict):
        return
    yield value
    for key, nested in value.items():
        if _normalized_key(key) in {"schedule as", "schedule a records", "contracts", "records"}:
            yield from _walk_records(nested)


def _embedded_json(value: Any) -> Any | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    decoder = json.JSONDecoder()
    for marker in ("{", "["):
        index = text.find(marker)
        if index < 0:
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
            return parsed
        except json.JSONDecodeError:
            continue
    return None


def _broker_row(
    record: dict[str, Any],
    page: int | None,
    evidence: SourceEvidence,
    chunk_text: str,
) -> ScheduleABrokerRow | None:
    name = _first_scalar(record, "payee_name", "broker_name", "agent_name", "recipient_name")
    if not name:
        return None
    commission = _money(
        _first_scalar(
            record,
            "total_commission",
            "total_commissions",
            "commission_total",
            "amount_of_commissions",
        )
    )
    fees = _money(
        _first_scalar(
            record,
            "total_administrative_and_other_fees",
            "total_fees",
            "fee_total",
            "amount_of_fees",
        )
    )
    address = _first_scalar(record, "payee_address", "broker_address", "address")
    line_1, line_2, city, state, zip_code = _split_us_address(address)
    purpose = _first_scalar(record, "purpose") or _broker_purpose(commission, fees)
    organization_code = _digits(
        _first_scalar(record, "organization_code", "org_code", "org_number")
    ) or _extract_organization_code(chunk_text)
    source_text = evidence.source_text
    return ScheduleABrokerRow(
        name=name,
        address_line_1=line_1,
        address_line_2=line_2,
        city=city,
        state=state,
        zip_code=zip_code,
        organization_code=organization_code,
        purpose=purpose,
        commission_rows=(
            [ScheduleABrokerMoneyRow(amount=commission, purpose="COMMISSIONS")]
            if _nonzero(commission)
            else []
        ),
        fee_rows=(
            [ScheduleABrokerMoneyRow(amount=fees, purpose="FEES")]
            if _nonzero(fees)
            else []
        ),
        commission_total=commission,
        fee_total=fees,
        commission_source_text=source_text,
        fee_source_text=source_text,
        source_page=page,
        confidence=0.97,
        evidence=[evidence],
    )


def _add_text_fallbacks(fields: list[NormalizedExtractionField], text: str) -> None:
    if not text:
        return
    patterns = (
        ("1b. Insurance Carrier EIN", r"\bEIN\s*:\s*(\d{2}-\d{7})\b"),
        ("1c. NAIC Code", r"\bNAIC\s*:\s*(\d{5})\b"),
        (
            "1d. Contract/Policy Number",
            r"\bPolicy\s+Number[ \t]*:[ \t]*([A-Z0-9][A-Z0-9-]+)",
        ),
        (
            "1e. Persons Covered (End of Policy Year)",
            r"\bEnding\s*:\s*([0-9][0-9,]*)\b",
        ),
        (
            "10a. Total premiums or subscription charges paid to carrier",
            r"\bTotal\s+Premium\s*:\s*\$?\s*([0-9][0-9,]*(?:\.\d{2})?)",
        ),
    )
    for name, pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        value = match.group(1)
        if name.startswith("10a."):
            value = _money(value)
        _add_field(fields, name, value, 1, match.group(0), 0.94)
    period = re.search(
        r"\bPolicy\s+Contract\s+Year\s*:\s*(\d{1,2}/\d{1,2}/\d{4})\s+to\s+(\d{1,2}/\d{1,2}/\d{4})",
        text,
        re.IGNORECASE,
    )
    if period:
        _add_field(fields, "1f. Policy Year Beginning Date", period.group(1), 1, period.group(0), 0.94)
        _add_field(fields, "1g. Policy Year Ending Date", period.group(2), 1, period.group(0), 0.94)


def _add_field(
    fields: list[NormalizedExtractionField],
    field_name: str,
    value: str | None,
    page: int | None,
    source_text: str | None,
    confidence: float,
) -> None:
    clean = " ".join(str(value or "").split())
    if not clean:
        return
    fields.append(
        NormalizedExtractionField(
            field_name=field_name,
            value=clean,
            confidence=confidence,
            page=page,
            source_text=source_text,
            evidence=[SourceEvidence(provider=PROVIDER, page=page, source_text=source_text)],
        )
    )


def _replace_field(
    fields: list[NormalizedExtractionField],
    field_name: str,
    value: str | None,
    page: int | None,
    source_text: str,
) -> None:
    if not value:
        return
    fields[:] = [field for field in fields if field.field_name != field_name]
    _add_field(fields, field_name, value, page, source_text, 0.97)


def _dedupe_fields(fields: list[NormalizedExtractionField]) -> list[NormalizedExtractionField]:
    best: dict[tuple[str, str], NormalizedExtractionField] = {}
    for field in fields:
        key = (field.field_name, field.value.casefold())
        current = best.get(key)
        if current is None or field.confidence > current.confidence:
            best[key] = field
    return list(best.values())


def _dedupe_brokers(rows: list[ScheduleABrokerRow]) -> list[ScheduleABrokerRow]:
    best: dict[tuple[str, str, str, str], ScheduleABrokerRow] = {}
    for row in rows:
        key = (
            row.name.casefold(),
            str(row.address_line_1 or "").casefold(),
            str(row.commission_total or ""),
            str(row.fee_total or ""),
        )
        best.setdefault(key, row)
    return list(best.values())


def _first_scalar(value: Any, *aliases: str) -> str | None:
    wanted = {_normalized_key(alias) for alias in aliases}
    if isinstance(value, dict):
        for key, nested in value.items():
            if _normalized_key(key) in wanted and isinstance(nested, (str, int, float)):
                clean = " ".join(str(nested).split())
                if clean:
                    return clean
        for nested in value.values():
            found = _first_scalar(nested, *aliases)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _first_scalar(nested, *aliases)
            if found:
                return found
    return None


def _normalized_key(value: Any) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(value or ""))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _chunk_text(chunk: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("text", "suggestedText"):
        value = chunk.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    narrative = chunk.get("narrative")
    if isinstance(narrative, str) and narrative.strip():
        parts.append(narrative.strip())
    elif isinstance(narrative, list):
        parts.extend(str(item).strip() for item in narrative if str(item).strip())
    for record in _chunk_records_without_suggested(chunk):
        parts.extend(_text_values(record))
    return "\n".join(dict.fromkeys(parts))[:20000]


def _chunk_records_without_suggested(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    raw_json = chunk.get("json")
    value: Any = raw_json
    if isinstance(raw_json, str):
        value = _embedded_json(raw_json)
    return list(_walk_records(value)) if isinstance(value, (dict, list)) else []


def _text_values(value: Any) -> list[str]:
    output: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"text", "summary", "content", "description"} and isinstance(nested, str):
                output.append(nested)
            else:
                output.extend(_text_values(nested))
    elif isinstance(value, list):
        for item in value:
            output.extend(_text_values(item))
    return output


def _record_evidence(record: dict[str, Any], chunk_text: str) -> str:
    summary = _first_scalar(record, "summary", "description", "text")
    if summary:
        return summary[:1200]
    rendered = json.dumps(record, ensure_ascii=False, default=str)
    return (rendered or chunk_text)[:1200]


def _page_number(chunk: dict[str, Any]) -> int | None:
    pages = chunk.get("pageNumbers") or []
    if isinstance(pages, list) and pages:
        try:
            return int(pages[0])
        except (TypeError, ValueError):
            return None
    if isinstance(pages, str):
        match = re.search(r"\d+", pages)
        return int(match.group(0)) if match else None
    return None


def _date_range(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    dates = re.findall(r"\b\d{1,2}/\d{1,2}/\d{4}\b", value)
    return (dates[0], dates[1]) if len(dates) >= 2 else (None, None)


def _normalize_ein(value: str | None) -> str | None:
    digits = _digits(value)
    if not digits or len(digits) != 9:
        return None
    return f"{digits[:2]}-{digits[2:]}"


def _digits(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits or None


def _money(value: str | None) -> str | None:
    clean = re.sub(r"[^0-9.\-()]", "", str(value or ""))
    if not clean:
        return None
    if clean.startswith("(") and clean.endswith(")"):
        clean = f"-{clean[1:-1]}"
    try:
        amount = Decimal(clean)
    except InvalidOperation:
        return None
    return f"{amount:,.2f}"


def _sum_money(values: Iterable[str | None]) -> str | None:
    total = Decimal("0")
    found = False
    for value in values:
        clean = re.sub(r"[^0-9.\-()]", "", str(value or ""))
        if not clean:
            continue
        if clean.startswith("(") and clean.endswith(")"):
            clean = f"-{clean[1:-1]}"
        try:
            total += Decimal(clean)
            found = True
        except InvalidOperation:
            continue
    return f"{total:,.2f}" if found else None


def _nonzero(value: str | None) -> bool:
    clean = re.sub(r"[^0-9.\-]", "", str(value or ""))
    try:
        return bool(clean) and Decimal(clean) != 0
    except InvalidOperation:
        return False


def _split_us_address(value: str | None) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    clean = " ".join(str(value or "").split())
    if not clean:
        return None, None, None, None, None
    location = re.search(
        r"(?P<city>[A-Za-z][A-Za-z .'-]*?)\s*,?\s+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$",
        clean,
    )
    if not location:
        return clean, None, None, None, None
    prefix = clean[: location.start()].strip(" ,")
    city = location.group("city").strip(" ,")
    state = location.group("state")
    zip_code = location.group("zip")
    box = re.search(r"\bP\.?\s*O\.?\s+Box\s+\S+", prefix, re.IGNORECASE)
    if box:
        line_1 = prefix[: box.start()].strip(" ,") or None
        line_2 = prefix[box.start() :].strip(" ,") or None
    else:
        line_1, line_2 = prefix or None, None
    return line_1, line_2, city, state, zip_code


def _extract_organization_code(text: str) -> str | None:
    match = re.search(r"\bORG\.?\s*(?:NUMBER|CODE)\s*:\s*([0-9])\b", text or "", re.IGNORECASE)
    return match.group(1) if match else None


def _matching_line(text: str, pattern: str) -> str:
    for line in str(text or "").splitlines():
        if re.search(pattern, line, re.IGNORECASE):
            return line.strip()
    return ""


def _broker_purpose(commission: str | None, fees: str | None) -> str | None:
    if _nonzero(commission) and _nonzero(fees):
        return "COMMISSIONS & FEES"
    if _nonzero(commission):
        return "COMMISSIONS"
    if _nonzero(fees):
        return "FEES"
    return None
