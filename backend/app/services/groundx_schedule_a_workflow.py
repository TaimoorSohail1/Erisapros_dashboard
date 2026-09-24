"""GroundX structured extraction contract for Schedule A documents.

The contract is generated from published Field Rules.  That keeps aliases and
new scalar rules data-driven while the repeating broker table remains an
explicit canonical structure shared by every Schedule A layout.
"""

from __future__ import annotations

import calendar
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Any, Iterable

import yaml

from app.models import (
    FieldRule,
    FormType,
    NormalizedExtractionField,
    NormalizedExtractionResult,
    ScheduleABrokerMoneyRow,
    ScheduleABrokerRow,
    SourceEvidence,
)
from app.services.field_rules import form_type_for_rule, normalize_name


MAX_FIELDS_PER_GROUP = 20
STRUCTURED_PROVIDER = "GroundX structured extract"
WORKFLOW_CONTRACT_VERSION = 3
_AMBIGUOUS_EXPERIENCE_IDENTIFIERS = {
    normalize_name(value)
    for value in (
        "Premiums",
        "Total Premium",
        "Amount Received",
        "Premiums Received",
        "Premium Received",
        "Commissions",
        "Commissions Paid",
        "Total Commissions Paid",
        "Service Fees",
        "Admin Fees",
        "Administrative Expenses",
        "Benefit Charges",
        "Claims",
        "Reserves",
        "Taxes",
        "Earned",
        "Other expenses",
        "Other Expense",
        "Other Reserves",
        "Total",
    )
}


def _schedule_a_rules(rules: Iterable[FieldRule]) -> list[FieldRule]:
    return [rule for rule in rules if form_type_for_rule(rule) == FormType.SCHEDULE_A]


def _workflow_rules(rules: Iterable[FieldRule]) -> list[FieldRule]:
    """Return only values that belong to the uploaded Schedule A source.

    Reference/Lookup values (the Form 5500 plan name, plan number, sponsor EIN,
    and plan-year headers) come from the matched filing, not from the carrier
    statement. Asking EyeLevel for them encourages it to copy nearby carrier or
    contract values into fields that are intentionally populated downstream.
    """
    return [rule for rule in _schedule_a_rules(rules) if rule.field_type != "Reference/Lookup"]


def _is_broker_rule(rule: FieldRule) -> bool:
    return rule.label.strip().lower().startswith(("3a.", "3b.", "3c.", "3d.", "3e."))


def schedule_a_schema_version(rules: Iterable[FieldRule]) -> str:
    payload = {
        "workflow_contract_version": WORKFLOW_CONTRACT_VERSION,
        "rules": [
            {
                "key": rule.key,
                "version": rule.version,
                "label": rule.label,
                "aliases": rule.aliases,
                "field_type": rule.field_type,
                "cardinality": str(getattr(rule.cardinality, "value", rule.cardinality)),
                "validators": rule.validators,
            }
            for rule in sorted(_workflow_rules(rules), key=lambda item: item.key)
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()[:12]


def build_schedule_a_extraction_mapping(rules: Iterable[FieldRule]) -> dict[str, Any]:
    relevant = sorted(_workflow_rules(rules), key=lambda item: (item.order, item.key))
    buckets: dict[str, list[FieldRule]] = {
        "schedule_a_policy": [],
        "schedule_a_compensation": [],
        "schedule_a_plan_header": [],
        "schedule_a_financial": [],
        "schedule_a_other": [],
    }
    for rule in relevant:
        if _is_broker_rule(rule):
            continue
        label = rule.label.strip().lower()
        if label.startswith(("1a.", "1b.", "1c.", "1d.", "1e.", "1f.", "1g.")):
            buckets["schedule_a_policy"].append(rule)
        elif label.startswith(("3a.", "3b.", "3c.", "3d.", "3e.")):
            buckets["schedule_a_compensation"].append(rule)
        elif label.startswith(("4a.", "4b.", "4c.", "4d.", "4e.")):
            buckets["schedule_a_plan_header"].append(rule)
        elif label.startswith(("9", "10", "11")):
            buckets["schedule_a_financial"].append(rule)
        else:
            buckets["schedule_a_other"].append(rule)

    groups: dict[str, Any] = {}
    for bucket_name, bucket_rules in buckets.items():
        for chunk_index in range(0, len(bucket_rules), MAX_FIELDS_PER_GROUP):
            chunk = bucket_rules[chunk_index : chunk_index + MAX_FIELDS_PER_GROUP]
            if not chunk:
                continue
            suffix = "" if chunk_index == 0 else f"_{chunk_index // MAX_FIELDS_PER_GROUP + 1}"
            group_name = f"{bucket_name}{suffix}"
            step_name = f"extract_{group_name}"
            groups[group_name] = {
                "workflow_step": step_name,
                "role": "statement",
                "prompt": {
                    "instructions": (
                        "Extract only the Schedule A values represented by this group. "
                        "Use labels and table structure, not page position. Do not infer a value "
                        "that is absent, and do not copy examples, instructions, subtotals, or totals "
                        "into a different field."
                    )
                },
                "fields": {
                    rule.key: {
                        "workflow_output_key": _workflow_output_key(rule.key),
                        **_field_definition(rule),
                    }
                    for rule in chunk
                },
            }

    groups["broker_totals"] = _broker_totals_definition(relevant)
    groups["broker_rows"] = _broker_rows_definition(relevant)
    custom_steps = [
        {
            "name": group["workflow_step"],
            "level": "document",
            "kind": "keys",
        }
        for group_name, group in groups.items()
    ]
    branches = [
        {
            "group": group_name,
            "chain": ["reconcile_statement", "save_statement"],
        }
        for group_name in groups
    ]
    return {
        "extraction_policy_version": "v1",
        "workflow": {
            "custom_steps": custom_steps,
            "agent_chain": [{"parallel": branches}],
        },
        **groups,
    }


def schedule_a_workflow_yaml(rules: Iterable[FieldRule]) -> str:
    return yaml.safe_dump(
        build_schedule_a_extraction_mapping(rules),
        sort_keys=False,
        allow_unicode=True,
        width=120,
    )


def _field_definition(rule: FieldRule) -> dict[str, Any]:
    identifiers = _field_identifiers(rule)
    value_type = _groundx_type(rule)
    instructions = _field_instructions(rule, value_type)
    return {
        "prompt": {
            "description": f"Schedule A field {rule.label}",
            "identifiers": identifiers,
            "instructions": instructions,
            "required": bool(rule.required),
            "type": value_type,
        }
    }


def _field_identifiers(rule: FieldRule) -> list[str]:
    identifiers = _dedupe_strings([rule.label, rule.ftw_field, *rule.aliases])
    if not rule.label.strip().lower().startswith("9"):
        return identifiers
    protected = {normalize_name(rule.label), normalize_name(rule.ftw_field or "")}
    return [
        identifier
        for identifier in identifiers
        if normalize_name(identifier) in protected
        or normalize_name(identifier) not in _AMBIGUOUS_EXPERIENCE_IDENTIFIERS
    ]


def _groundx_type(rule: FieldRule) -> str:
    validators = {str(item).strip().lower() for item in rule.validators}
    if rule.label.strip().lower().startswith("1e."):
        return "int"
    if "boolean" in validators:
        return "bool"
    if "integer" in validators:
        return "int"
    if "currency" in validators:
        return "float"
    return "str"


def _field_instructions(rule: FieldRule, value_type: str) -> str:
    validators = {str(item).strip().lower() for item in rule.validators}
    base = f"Return only the value for {rule.label}. If it is absent, return null; never guess."
    label = rule.label.strip().lower()
    if label.startswith("1e."):
        return (
            f"{base} Return a positive whole number only when the source explicitly labels it as approximate persons covered "
            "at the end of the policy or contract year. Never calculate or sum a value from exposure, enrollment-tier, employee, "
            "dependent, subscriber, or membership columns. Ignore instructions to consult another report."
        )
    if label.startswith("9a. premiums: (1)"):
        return (
            f"{base} Extract only Part III line 9 experience-rated Amount Received. Do not copy line 10a or a generic "
            "nonexperience-rated premium total into this field."
        )
    if label.startswith("10a."):
        return (
            f"{base} Extract only the primary Schedule A Part III line 10a nonexperience-rated total. Prefer the main "
            "Schedule A summary over appendix allocation pages. If the source contains multiple premium rows without an "
            "explicit total, return null rather than selecting one row or calculating a total."
        )
    if "date" in validators:
        return f"{base} Return dates as YYYY-MM-DD and preserve the actual calendar date."
    if value_type in {"int", "float"}:
        return f"{base} Return a numeric value without currency symbols or thousands separators."
    if "ein" in validators:
        return f"{base} Return the nine-digit EIN, preserving its hyphen when printed."
    return f"{base} Return text exactly as printed, with surrounding whitespace removed."


def _broker_rows_definition(rules: list[FieldRule]) -> dict[str, Any]:
    by_prefix = {
        prefix: next((rule for rule in rules if rule.label.lower().startswith(prefix)), None)
        for prefix in ("3a.", "3b.", "3c.", "3d.", "3e.")
    }

    def identifiers(prefix: str, defaults: list[str]) -> list[str]:
        rule = by_prefix.get(prefix)
        if not rule:
            return defaults
        return _dedupe_strings([rule.label, rule.ftw_field, *rule.aliases, *defaults])

    row_fields: dict[str, Any] = {
        "name": _prompt_field(
            identifiers("3a.", ["Agent/Broker/Person", "Broker / Person"]),
            "Return the legal name for this broker or compensation recipient row, not a column heading. Exclude agent numbers, "
            "table borders, and a stray standalone I caused by a border after a numeric name.",
        ),
        "address_line_1": _prompt_field(["Address", "Street Address"], "Return the first street-address line."),
        "address_line_2": _prompt_field(["Address 2", "Suite", "Unit"], "Return the second address line when present."),
        "city": _prompt_field(["City"], "Return the city for this same broker row."),
        "state": _prompt_field(["State", "Province"], "Return the state or province abbreviation."),
        "zip_code": _prompt_field(["ZIP", "Postal Code", "Zip Code"], "Return the postal code exactly as printed."),
        "organization_code": _prompt_field(
            identifiers("3e.", ["Organization Code", "Org Code"]),
            "Return the organization code for this same broker row.",
        ),
        "commission_total": _prompt_field(
            identifiers("3b.", ["Commissions Paid", "Amount of Commissions Paid"]),
            "Return only this broker row's commission amount as a number, not the document total.",
            "float",
        ),
        "fee_total": _prompt_field(
            identifiers("3c.", ["Fees Paid", "Amount of Fees Paid"]),
            "Return only this broker row's fee amount as a number, not the document total.",
            "float",
        ),
        "purpose": _prompt_field(
            identifiers("3d.", ["Purpose", "Purpose for Which Paid"]),
            "Return the purpose associated with this same broker row.",
        ),
    }
    for field_name, field in row_fields.items():
        field["workflow_output_key"] = field_name
    return {
        "workflow_step": "extract_broker_rows",
        "role": "statement",
        "prompt": {
            "instructions": (
                "Extract recipients only from the primary Schedule A Part I persons-receiving-commissions-and-fees table. "
                "Do not extract allocation rows from appendix-to-1a/1b/1c pages, footnotes, benefit detail, or Schedule C. "
                "Extract every distinct agent, broker, consultant, or other compensation recipient in that primary table as a separate row. "
                "Keep name, address, commissions, fees, purpose, and organization code from the same visual row together. "
                "Return each recipient exactly once. Exclude column headings, narrative instructions, subtotals, and document totals."
            )
        },
        "fields": row_fields,
    }


def _broker_totals_definition(rules: list[FieldRule]) -> dict[str, Any]:
    commission = next((rule for rule in rules if rule.label.lower().startswith("3b.")), None)
    fees = next((rule for rule in rules if rule.label.lower().startswith("3c.")), None)

    def identifiers(rule: FieldRule | None, defaults: list[str]) -> list[str]:
        if not rule:
            return defaults
        return _dedupe_strings([rule.label, rule.ftw_field, *rule.aliases, *defaults])

    return {
        "workflow_step": "extract_broker_totals",
        "role": "statement",
        "prompt": {
            "instructions": (
                "Extract only the primary Schedule A Part I line 2 total commissions and total fees. "
                "Use a clearly labeled summary total from the main Schedule A section. Do not use an individual recipient row, "
                "appendix allocation, footnote, Schedule C value, or benefit-detail value. Return null when the primary totals are absent."
            )
        },
        "fields": {
            "commission_total": {
                "workflow_output_key": "commission_total",
                **_prompt_field(
                    identifiers(commission, ["Total Amount of commissions paid", "Total commissions paid"]),
                    "Return the primary Part I line 2(a) total commissions as a number.",
                    "float",
                ),
            },
            "fee_total": {
                "workflow_output_key": "fee_total",
                **_prompt_field(
                    identifiers(fees, ["Total Amount of fees paid", "Total fees paid"]),
                    "Return the primary Part I line 2(b) total fees as a number.",
                    "float",
                ),
            },
        },
    }


def _prompt_field(identifiers: list[str], instructions: str, value_type: str = "str") -> dict[str, Any]:
    clean_identifiers = _dedupe_strings(identifiers)
    return {
        "prompt": {
            "description": clean_identifiers[0] if clean_identifiers else "Schedule A broker row value",
            "identifiers": clean_identifiers,
            "instructions": f"{instructions} If absent, return null; never guess.",
            "type": value_type,
        }
    }


def _workflow_output_key(value: str) -> str:
    if len(value) <= 64:
        return value
    digest = sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{value[:53]}_{digest}"


def _dedupe_strings(values: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        clean = " ".join(str(value or "").split())
        normalized = normalize_name(clean)
        if not clean or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(clean)
    return output


def normalize_groundx_schedule_a_extract(
    payload: Any,
    rules: Iterable[FieldRule],
) -> NormalizedExtractionResult:
    relevant = _workflow_rules(rules)
    root = _unwrap_payload(payload)
    canonical_by_key = {rule.key: rule for rule in relevant}
    aliases: dict[str, list[FieldRule]] = {}
    for rule in relevant:
        for name in [rule.key, rule.label, rule.ftw_field, *rule.aliases]:
            normalized = normalize_name(str(name or ""))
            if normalized:
                aliases.setdefault(normalized, []).append(rule)

    selected: dict[str, NormalizedExtractionField] = {}
    for path, key, raw_value in _walk_scalar_candidates(root):
        if "broker_rows" in path:
            continue
        rule = canonical_by_key.get(key)
        if rule is None:
            candidates = aliases.get(normalize_name(key), [])
            unique = {candidate.key: candidate for candidate in candidates}
            rule = next(iter(unique.values())) if len(unique) == 1 else None
        if rule is None:
            continue
        if _is_broker_rule(rule):
            continue
        value, metadata = _value_and_metadata(raw_value)
        normalized_value = _normalize_rule_value(rule, value)
        if normalized_value is None:
            continue
        evidence = _evidence_from_metadata(metadata, normalized_value)
        confidence = _float_or_default(metadata.get("confidence"), 0.97)
        field = NormalizedExtractionField(
            field_name=rule.label,
            value=normalized_value,
            candidate_values=[normalized_value],
            confidence=confidence,
            page=_int_or_none(metadata.get("page") or metadata.get("pageNumber")),
            source_text=_first_text(metadata, "sourceText", "source_text", "context"),
            evidence=[evidence],
        )
        current = selected.get(rule.key)
        if current is None:
            selected[rule.key] = field
            continue
        field_quality = _structured_candidate_quality(field)
        current_quality = _structured_candidate_quality(current)
        if field_quality > current_quality:
            selected[rule.key] = field
            continue
        if field_quality < current_quality:
            continue
        candidate_values = _dedupe_strings(
            [*current.candidate_values, current.value, *field.candidate_values, field.value]
        )
        evidence = [*current.evidence, *field.evidence]
        if field.confidence > current.confidence:
            field.candidate_values = candidate_values
            field.evidence = evidence
            selected[rule.key] = field
        else:
            current.candidate_values = candidate_values
            current.evidence = evidence

    broker_rows = [_normalize_broker_row(row) for row in _find_broker_rows(root)]
    broker_rows = _consolidate_broker_rows([row for row in broker_rows if row.name.strip()])
    broker_totals = _find_broker_totals(root)
    for field in _broker_summary_fields(broker_rows, relevant, broker_totals=broker_totals):
        selected[field.field_name] = field
    normalized_fields = _drop_copied_experience_values(list(selected.values()))
    return NormalizedExtractionResult(
        provider=STRUCTURED_PROVIDER,
        fields=normalized_fields,
        raw=payload,
        schedule_a_broker_rows=broker_rows,
    )


def _structured_candidate_quality(field: NormalizedExtractionField) -> tuple[int, int]:
    source_texts = [field.source_text, *(item.source_text for item in field.evidence)]
    value_key = normalize_name(field.value)
    source_support = any(
        value_key and value_key in normalize_name(str(source or ""))
        for source in source_texts
    )
    position_support = any(
        item.bounding_box is not None or item.table_cell is not None
        for item in field.evidence
    )
    return int(source_support), int(position_support)


def _unwrap_payload(payload: Any) -> Any:
    current = payload
    for _ in range(4):
        if not isinstance(current, dict):
            break
        next_value = None
        for key in ("extract", "extraction", "data", "result", "output"):
            candidate = current.get(key)
            if isinstance(candidate, (dict, list)):
                next_value = candidate
                break
        if next_value is None:
            break
        current = next_value
    return current


def _walk_scalar_candidates(value: Any, path: tuple[str, ...] = ()):
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_scalar_candidates(item, (*path, str(index)))
        return
    if not isinstance(value, dict):
        return
    if "value" in value and not isinstance(value.get("value"), (dict, list)):
        if path:
            yield path, path[-1], value
        return
    for key, item in value.items():
        if isinstance(item, (dict, list)):
            yield from _walk_scalar_candidates(item, (*path, str(key)))
        else:
            yield (*path, str(key)), str(key), item


def _value_and_metadata(raw: Any) -> tuple[Any, dict[str, Any]]:
    if isinstance(raw, dict) and "value" in raw:
        return raw.get("value"), raw
    return raw, {}


def _evidence_from_metadata(metadata: dict[str, Any], value: str) -> SourceEvidence:
    box = metadata.get("boundingBox") or metadata.get("bounding_box")
    bounding_box = None
    if isinstance(box, (list, tuple)) and len(box) == 4:
        try:
            bounding_box = tuple(float(item) for item in box)
        except (TypeError, ValueError):
            bounding_box = None
    row = _int_or_none(metadata.get("row") or metadata.get("rowIndex"))
    column = _int_or_none(
        metadata.get("column")
        or metadata.get("columnIndex")
        or metadata.get("col")
    )
    return SourceEvidence(
        provider=STRUCTURED_PROVIDER,
        page=_int_or_none(metadata.get("page") or metadata.get("pageNumber")),
        source_text=_first_text(metadata, "sourceText", "source_text", "context"),
        bounding_box=bounding_box,
        table_cell=(row, column) if row is not None and column is not None else None,
    )


def _find_broker_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            found = _find_broker_rows(item)
            if found:
                return found
        return []
    if not isinstance(value, dict):
        return []
    for key, item in value.items():
        if normalize_name(str(key)) == "broker rows":
            candidate = item.get("rows") if isinstance(item, dict) else item
            if isinstance(candidate, list):
                return [row for row in candidate if isinstance(row, dict)]
        found = _find_broker_rows(item)
        if found:
            return found
    return []


def _find_broker_totals(value: Any) -> dict[str, str | None]:
    if isinstance(value, list):
        for item in value:
            found = _find_broker_totals(item)
            if any(found.values()):
                return found
        return {"commission_total": None, "fee_total": None}
    if not isinstance(value, dict):
        return {"commission_total": None, "fee_total": None}
    for key, item in value.items():
        if normalize_name(str(key)) == "broker totals":
            mappings = [item] if isinstance(item, dict) else item if isinstance(item, list) else []
            for mapping in mappings:
                if not isinstance(mapping, dict):
                    continue
                totals = {
                    "commission_total": _normalize_money(
                        _string_value(_value_and_metadata(mapping.get("commission_total"))[0])
                    ),
                    "fee_total": _normalize_money(
                        _string_value(_value_and_metadata(mapping.get("fee_total"))[0])
                    ),
                }
                if any(totals.values()):
                    return totals
        found = _find_broker_totals(item)
        if any(found.values()):
            return found
    return {"commission_total": None, "fee_total": None}


def _normalize_broker_row(raw: dict[str, Any]) -> ScheduleABrokerRow:
    raw_values = {normalize_name(str(key)): value for key, value in raw.items()}
    values = {
        key: _string_value(_value_and_metadata(value)[0])
        for key, value in raw_values.items()
    }

    def get(*names: str) -> str | None:
        for name in names:
            value = values.get(normalize_name(name))
            if value not in {None, ""}:
                return value
        return None

    def metadata(*names: str) -> dict[str, Any]:
        for name in names:
            raw_value = raw_values.get(normalize_name(name))
            if isinstance(raw_value, dict):
                return _value_and_metadata(raw_value)[1]
        return {}

    commission = _normalize_money(get("commission_total", "commissions", "amount_of_commissions"))
    fee = _normalize_money(get("fee_total", "fees", "amount_of_fees"))
    commission_metadata = metadata("commission_total", "commissions", "amount_of_commissions")
    fee_metadata = metadata("fee_total", "fees", "amount_of_fees")
    purpose = get("purpose")
    organization_code = _normalize_organization_code(get("organization_code", "org_code"))
    evidence = [
        _evidence_from_metadata(item, _string_value(item.get("value")) or "")
        for item in raw.values()
        if isinstance(item, dict) and "value" in item
    ]
    source_page = _int_or_none(raw.get("page") or raw.get("pageNumber"))
    if source_page is None:
        source_page = next((item.page for item in evidence if item.page), None)
    if not evidence:
        evidence = [SourceEvidence(provider=STRUCTURED_PROVIDER, page=source_page)]
    return ScheduleABrokerRow(
        name=get("name", "broker_name", "agent_name") or "",
        address_line_1=get("address_line_1", "address", "street_address"),
        address_line_2=get("address_line_2", "address_2"),
        city=get("city"),
        state=get("state", "province"),
        zip_code=get("zip_code", "zip", "postal_code"),
        organization_code=organization_code,
        commission_rows=[ScheduleABrokerMoneyRow(amount=commission, purpose=purpose)] if _nonzero_money(commission) else [],
        fee_rows=[ScheduleABrokerMoneyRow(amount=fee, purpose=purpose)] if _nonzero_money(fee) else [],
        commission_total=commission,
        fee_total=fee,
        commission_source_text=_first_text(
            commission_metadata, "sourceText", "source_text", "context"
        ),
        fee_source_text=_first_text(fee_metadata, "sourceText", "source_text", "context"),
        source_page=source_page,
        confidence=_float_or_default(raw.get("confidence"), 0.97),
        evidence=evidence,
    )


def _normalize_rule_value(rule: FieldRule, value: Any) -> str | None:
    clean = _string_value(value)
    if clean is None:
        return None
    label = rule.label.strip().lower()
    validators = {str(item).strip().lower() for item in rule.validators}

    if label.startswith("1e."):
        match = re.fullmatch(
            r"([\d,]+)(?:\s+(?:employees?|persons?|people|lives?|members?|subscribers?|covered))?",
            clean,
            re.IGNORECASE,
        )
        if not match:
            return None
        count = match.group(1).replace(",", "")
        return count if int(count) > 0 else None

    if "date" in validators or label.startswith(("1f.", "1g.")):
        return _normalize_date(clean, end_date=label.startswith("1g."))
    if "ein" in validators or label.startswith("1b."):
        digits = re.sub(r"\D", "", clean)
        return f"{digits[:2]}-{digits[2:]}" if len(digits) == 9 else None
    if "naic" in validators or label.startswith("1c."):
        digits = re.sub(r"\D", "", clean)
        return digits if 4 <= len(digits) <= 6 else None
    return clean


def _normalize_date(value: str, *, end_date: bool) -> str | None:
    for pattern in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value.strip(), pattern).strftime("%m/%d/%Y")
        except ValueError:
            continue
    for pattern in ("%B %Y", "%b %Y"):
        try:
            parsed = datetime.strptime(value.strip(), pattern)
        except ValueError:
            continue
        day = calendar.monthrange(parsed.year, parsed.month)[1] if end_date else 1
        return parsed.replace(day=day).strftime("%m/%d/%Y")
    return None


def _normalize_money(value: str | None) -> str | None:
    clean = re.sub(r"[^0-9.()-]", "", str(value or "")).replace("(", "-").replace(")", "")
    if not clean:
        return None
    try:
        amount = Decimal(clean)
    except InvalidOperation:
        return None
    return _decimal_string(amount)


def _decimal_string(value: Decimal) -> str:
    normalized = format(value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _nonzero_money(value: str | None) -> bool:
    try:
        return value is not None and Decimal(value) != 0
    except InvalidOperation:
        return False


def _normalize_organization_code(value: str | None) -> str | None:
    match = re.match(r"\s*([1-6])(?:\D|$)", str(value or ""))
    return match.group(1) if match else None


def _consolidate_broker_rows(rows: list[ScheduleABrokerRow]) -> list[ScheduleABrokerRow]:
    consolidated: dict[tuple[str, str, str], ScheduleABrokerRow] = {}
    order: list[tuple[str, str, str]] = []
    for row in rows:
        identity = (
            normalize_name(row.name),
            normalize_name(row.address_line_1 or ""),
            normalize_name(row.zip_code or ""),
        )
        current = consolidated.get(identity)
        if current is None:
            current = row.model_copy(deep=True)
            current.commission_rows = []
            current.fee_rows = []
            current.commission_total = "0"
            current.fee_total = "0"
            consolidated[identity] = current
            order.append(identity)
        current.commission_total = _decimal_string(
            Decimal(current.commission_total or "0") + Decimal(row.commission_total or "0")
        )
        current.fee_total = _decimal_string(
            Decimal(current.fee_total or "0") + Decimal(row.fee_total or "0")
        )
        current.commission_rows.extend(row.commission_rows)
        current.fee_rows.extend(row.fee_rows)
        current.evidence.extend(row.evidence)
        current.confidence = min(current.confidence, row.confidence)
    return [consolidated[identity] for identity in order]


def _broker_summary_fields(
    rows: list[ScheduleABrokerRow],
    rules: list[FieldRule],
    *,
    broker_totals: dict[str, str | None] | None = None,
) -> list[NormalizedExtractionField]:
    broker_totals = broker_totals or {}
    if not rows and not any(broker_totals.values()):
        return []
    by_prefix = {
        prefix: next((rule for rule in rules if rule.label.lower().startswith(prefix)), None)
        for prefix in ("3a.", "3b.", "3c.", "3d.", "3e.")
    }
    commission_total = sum((Decimal(row.commission_total or "0") for row in rows), Decimal("0"))
    fee_total = sum((Decimal(row.fee_total or "0") for row in rows), Decimal("0"))
    purposes = list(
        dict.fromkeys(
            money_row.purpose
            for row in rows
            for money_row in [*row.commission_rows, *row.fee_rows]
            if money_row.purpose
        )
    )
    values = {
        "3a.": rows[0].name if rows else None,
        "3b.": broker_totals.get("commission_total") or _decimal_string(commission_total),
        "3c.": broker_totals.get("fee_total") or _decimal_string(fee_total),
        "3d.": "; ".join(purposes) or None,
        "3e.": rows[0].organization_code if rows else None,
    }
    output: list[NormalizedExtractionField] = []
    for prefix, value in values.items():
        rule = by_prefix.get(prefix)
        if not rule or value in {None, ""}:
            continue
        output.append(
            NormalizedExtractionField(
                field_name=rule.label,
                value=str(value),
                confidence=min((row.confidence for row in rows), default=0.97),
                page=rows[0].source_page if rows else None,
                source_text="GroundX structured broker rows",
                evidence=[SourceEvidence(provider=STRUCTURED_PROVIDER, source_text="GroundX structured broker rows")],
            )
        )
    return output


def _drop_copied_experience_values(
    fields: list[NormalizedExtractionField],
) -> list[NormalizedExtractionField]:
    """Remove line 9 values copied from line 10 or Part I compensation.

    A nonexperience-rated carrier statement often prints generic premiums,
    commissions, and fees. Those words are also aliases for experience-rated
    line 9 fields, so a model may emit the same amount into both sections. The
    duplicate amounts are evidence of cross-section copying, not two values.
    """

    def find(prefix: str) -> NormalizedExtractionField | None:
        return next(
            (field for field in fields if field.field_name.strip().lower().startswith(prefix.lower())),
            None,
        )

    line_9a = find("9a. premiums")
    line_10a = find("10a.")
    copied_9a = bool(
        line_9a
        and line_10a
        and _normalize_money(line_9a.value) == _normalize_money(line_10a.value)
    )
    copied_labels: set[str] = {line_9a.field_name} if copied_9a and line_9a else set()
    if copied_9a:
        for experience_prefix, compensation_prefix in (("9c(1)(a).", "3b."), ("9c(1)(b).", "3c.")):
            experience = find(experience_prefix)
            compensation = find(compensation_prefix)
            if (
                experience
                and compensation
                and _normalize_money(experience.value) == _normalize_money(compensation.value)
            ):
                copied_labels.add(experience.field_name)
    return [field for field in fields if field.field_name not in copied_labels]


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (str, int, float)):
        clean = " ".join(str(value).split())
        return clean or None
    return None


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _first_text(mapping: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
