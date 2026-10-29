"""Customer-authorized defaults for proposed recipients, never FTW snapshots."""
from decimal import Decimal, InvalidOperation
import json
import re

from app.models import (
    ExtractionValidationResult,
    NormalizedExtractionField,
    NormalizedExtractionResult,
    ScheduleABrokerMoneyRow,
    ScheduleABrokerRow,
    SourceEvidence,
)

DEFAULT_CODE_REASON = "Customer rule: Defaulted to 3 because organizational code was blank."
DEFAULT_NAIC_REASON = "Customer-approved rule: Use 00000 when the carrier NAIC is missing or N/A. This is a default, not a printed identifier."
MISSING_BROKER_DEFAULT_VALIDATOR = "customer_missing_broker_values"
MISSING_FIELD_DEFAULTS = {
    "1e.": ("1e. Persons Covered (End of Policy Year)", "0"),
    "3b.": ("3b. Amount of Commissions", "0"),
    "3c.": ("3c. Amount of Fees", "0"),
    "3d.": ("3d. Purpose", "Commission"),
}


def _missing_default_reason(prefix: str) -> str:
    label, value = MISSING_FIELD_DEFAULTS[prefix]
    return f"Customer-approved missing-value default: {label} = {value}. This is a configured default, not printed PDF data."


def is_approved_missing_field_default(field: NormalizedExtractionField) -> bool:
    for prefix, (_, value) in MISSING_FIELD_DEFAULTS.items():
        if field.field_name.strip().lower().startswith(prefix) and field.value == value:
            return any(e.provider == "Customer configured default" and e.source_text == _missing_default_reason(prefix)
                       for e in field.evidence)
    return False


def default_blank_broker_values(rows: list[ScheduleABrokerRow]) -> list[ScheduleABrokerRow]:
    """Default absent proposed values, never an unreadable/held value or a component total."""
    normalized = []
    for original in rows:
        row = original.model_copy(deep=True)
        if (row.name.strip() and row.decision != "REVIEW_REQUIRED"
                and not any(v.status == "ERROR" for v in row.validation_results)):
            defaults = {}
            for key, parts in (("commission_total", row.commission_rows), ("fee_total", row.fee_rows)):
                if not str(getattr(row, key) or "").strip() and not parts:
                    setattr(row, key, "0")
                    defaults[key] = "0"
            if (not str(row.purpose or "").strip()
                    and not any(str(p.purpose or "").strip() for p in row.fee_rows)):
                row.purpose = "Commission"
                defaults["purpose"] = "Commission"
            if defaults:
                row.validation_results.append(ExtractionValidationResult(
                    validator=MISSING_BROKER_DEFAULT_VALIDATOR, status="PASS",
                    reason="Customer-approved missing broker defaults; original source evidence and confidence retained.",
                    normalized_value=json.dumps(defaults, sort_keys=True),
                ))
        normalized.append(row)
    return normalized


def broker_value_was_defaulted(row: ScheduleABrokerRow, key: str) -> bool:
    for result in row.validation_results:
        if result.validator == MISSING_BROKER_DEFAULT_VALIDATOR:
            try:
                if key in json.loads(result.normalized_value or "{}"):return True
            except (ValueError, TypeError):
                continue
    return False


def apply_missing_schedule_a_field_defaults(result: NormalizedExtractionResult) -> None:
    if any(word in result.provider.casefold() for word in ("manual review", "failed", "failure")):
        return
    header = any(f.field_name.strip().lower().startswith(("1a.", "1d.")) and str(f.value or "").strip() for f in result.fields)
    brokers = [r for r in result.schedule_a_broker_rows if r.name.strip()]
    broker_context = bool(brokers) or any(f.field_name.strip().lower().startswith("3a.") and str(f.value or "").strip() for f in result.fields)
    for prefix, (label, value) in MISSING_FIELD_DEFAULTS.items():
        if not (header if prefix == "1e." else broker_context):
            continue
        matches = [f for f in result.fields if f.field_name.strip().lower().startswith(prefix)]
        field = matches[0] if matches else None
        if any(str(f.value or "").strip() or f.decision == "REVIEW_REQUIRED"
                or any(v.status == "ERROR" for v in f.validation_results)
                or any(str(c or "").strip() for c in f.candidate_values)
                or re.search(r"\b(?:unreadable|illegible|uncertain|unknown)\b", f.source_text or "", re.I) for f in matches):
            continue
        if prefix != "1e.":
            if any(r.decision == "REVIEW_REQUIRED" or any(v.status == "ERROR" for v in r.validation_results) for r in brokers):
                continue
            key = {"3b.": "commission_total", "3c.": "fee_total", "3d.": "purpose"}[prefix]
            if prefix in {"3b.", "3c."}:
                parts_key = "commission_rows" if prefix == "3b." else "fee_rows"
                if any(not str(getattr(r, key) or "").strip() and getattr(r, parts_key) for r in brokers):
                    continue
            elif any(not str(r.purpose or "").strip() and any(str(p.purpose or "").strip() for p in r.fee_rows) for r in brokers):
                continue
            if any(str(getattr(r, key) or "").strip() not in {"", value} for r in brokers):
                continue
        if field is None:
            field = NormalizedExtractionField(field_name=label)
            result.fields.append(field)
        reason = _missing_default_reason(prefix)
        field.value, field.candidate_values, field.confidence = value, [value], 1.0
        field.source_text = reason
        field.evidence.append(SourceEvidence(provider="Customer configured default", source_text=reason))
        raw = dict(result.raw) if isinstance(result.raw, dict) else {"provider_raw": result.raw}
        entry = {"field": field.field_name, "value": value, "reason": reason}
        if entry not in raw.setdefault("customer_defaults", []):
            raw["customer_defaults"].append(entry)
        result.raw = raw


def published_static_default(rule):
    if (str(rule.field_type).casefold() != "static" or "schedule a" not in str(rule.source).casefold()
            or getattr(rule.status, "value", rule.status) != "PUBLISHED"):
        return None
    match = re.fullmatch(r"\s*Always\s+(Yes|No)\s*[.!]?\s*", str(rule.client_notes or rule.notes or ""), re.I)
    return match.group(1).title() if match else None


def published_static_default_reason(rule):
    return f"Published rule {rule.key} v{rule.version}: {rule.client_notes or rule.notes}"


def apply_published_static_defaults(result, rules) -> None:
    if not result.fields and not result.schedule_a_broker_rows:
        return
    for rule in rules or []:
        default = published_static_default(rule)
        if default is None:
            continue
        field = next((f for f in result.fields if f.field_name.casefold() == rule.label.casefold()), None)
        if field is not None and str(field.value or "").strip():
            if field.value.strip().casefold() != default.casefold():
                reason = "Printed/extracted value conflicts with the published static default. Preserve the value and confirm the rule."
                field.validation_results.append(ExtractionValidationResult(validator="business_rule_confirmation", status="ERROR", reason=reason))
                field.decision = "REVIEW_REQUIRED"
            continue
        if field is None:
            field = NormalizedExtractionField(field_name=rule.label)
            result.fields.append(field)
        reason = published_static_default_reason(rule)
        field.value, field.candidate_values, field.confidence = default, [default], 1.0
        field.source_text = reason
        field.evidence = [SourceEvidence(provider="Published field rule default", source_text=reason)]
        field.decision = "AUTOMATIC" if rule.automatic_update_allowed else "REVIEW_REQUIRED"
        raw = dict(result.raw) if isinstance(result.raw, dict) else {"provider_raw": result.raw}
        entry = {"field": field.field_name, "value": default, "rule_key": rule.key, "rule_version": rule.version, "reason": reason}
        if entry not in raw.setdefault("customer_defaults", []):
            raw["customer_defaults"].append(entry)
        result.raw = raw


def default_blank_organization_codes(rows: list[ScheduleABrokerRow]) -> list[ScheduleABrokerRow]:
    normalized = []
    for original in rows:
        row = original.model_copy(deep=True)
        if row.organization_code is None or not str(row.organization_code).strip():
            row.organization_code = "3"
            row.organization_code_defaulted = True
        elif str(row.organization_code).strip() != "3":
            row.organization_code_defaulted = False
        normalized.append(row)
    return normalized


def _money_decimal(value: object) -> Decimal | None:
    text = str(value or "").strip()
    if not text:
        return None
    negative_parentheses = text.startswith("(") and text.endswith(")")
    if negative_parentheses:
        text = text[1:-1]
    text = text.replace("$", "").replace(",", "").strip()
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _explicit_zero(value: object) -> bool:
    amount = _money_decimal(value)
    return amount is not None and amount == 0


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def classify_unlabelled_broker_payments_as_fees(
    rows: list[ScheduleABrokerRow],
) -> tuple[list[ScheduleABrokerRow], list[dict[str, str]]]:
    normalized: list[ScheduleABrokerRow] = []
    evidence: list[dict[str, str]] = []
    for original in rows:
        row = original.model_copy(deep=True)
        commission = _money_decimal(row.commission_total)
        labels = " ".join(
            [
                str(row.purpose or ""),
                str(row.commission_source_text or ""),
                *(str(item.purpose or "") for item in row.commission_rows),
            ]
        )
        explicitly_commission = "commission" in labels.casefold()
        if commission is not None and commission > 0 and not explicitly_commission:
            current_fee = _money_decimal(row.fee_total) or Decimal("0")
            row.commission_total = "0"
            row.fee_total = _decimal_text(current_fee + commission)
            if row.commission_rows:
                row.fee_rows = [*row.fee_rows, *row.commission_rows]
            else:
                row.fee_rows = [
                    *row.fee_rows,
                    ScheduleABrokerMoneyRow(amount=_decimal_text(commission), purpose="Fees"),
                ]
            row.commission_rows = []
            row.purpose = "FEES"
            row.fee_source_text = row.commission_source_text or row.fee_source_text
            evidence.append(
                {
                    "rule": "unlabelled_broker_payment_to_fee",
                    "broker": row.name,
                    "amount": _decimal_text(commission),
                    "reason": "A positive broker payment without a commission label is reported as a Schedule A fee.",
                }
            )
        normalized.append(row)
    return normalized, evidence


def exclude_zero_compensation_brokers(
    rows: list[ScheduleABrokerRow],
) -> tuple[list[ScheduleABrokerRow], list[dict[str, str]]]:
    retained: list[ScheduleABrokerRow] = []
    evidence: list[dict[str, str]] = []
    for row in rows:
        if (row.decision != 'REVIEW_REQUIRED'
                and not any(v.status == 'ERROR' for v in row.validation_results)
                and not any(v.validator == MISSING_BROKER_DEFAULT_VALIDATOR for v in row.validation_results)
                and _explicit_zero(row.commission_total) and _explicit_zero(row.fee_total)):
            evidence.append(
                {
                    "rule": "exclude_zero_compensation_broker",
                    "broker": row.name,
                    "reason": "Explicit commission and fee totals are both zero.",
                }
            )
            continue
        retained.append(row)
    return retained, evidence


def apply_customer_defaults(result: NormalizedExtractionResult) -> None:
    result.schedule_a_broker_rows, fee_classification_evidence = (
        classify_unlabelled_broker_payments_as_fees(result.schedule_a_broker_rows)
    )
    result.schedule_a_broker_rows, zero_broker_evidence = exclude_zero_compensation_brokers(
        result.schedule_a_broker_rows
    )
    customer_rule_evidence = [*fee_classification_evidence, *zero_broker_evidence]
    if customer_rule_evidence:
        raw = dict(result.raw) if isinstance(result.raw, dict) else {"provider_raw": result.raw}
        raw.setdefault("customer_rules", []).extend(customer_rule_evidence)
        result.raw = raw
    result.schedule_a_broker_rows = default_blank_organization_codes(result.schedule_a_broker_rows)
    result.schedule_a_broker_rows = default_blank_broker_values(result.schedule_a_broker_rows)
    apply_missing_schedule_a_field_defaults(result)
    code_field = next((field for field in result.fields if field.field_name.strip().lower().startswith("3e.")), None)
    if code_field is None:
        code_field = NormalizedExtractionField(field_name="3e. Organizational Code", value="", confidence=0)
        result.fields.append(code_field)
        if result.schedule_a_broker_rows and not result.schedule_a_broker_rows[0].organization_code_defaulted:
            # An explicit recipient code is not a blank just because its scalar
            # counterpart was omitted by the provider.
            row = result.schedule_a_broker_rows[0]
            code_field.value = str(row.organization_code)
            code_field.confidence = row.confidence
            code_field.evidence = list(row.evidence)
            code_field.source_text = "Organizational code supplied on the first disclosed recipient."
    if code_field is not None and not str(code_field.value or "").strip():
        code_field.value = "3"
        code_field.candidate_values = ["3"]
        # Certainty of the explicit customer rule, not fabricated OCR confidence.
        code_field.confidence = 1.0
        code_field.source_text = DEFAULT_CODE_REASON
        code_field.evidence = [SourceEvidence(provider="Customer configured default", source_text=DEFAULT_CODE_REASON)]
        raw = dict(result.raw) if isinstance(result.raw, dict) else {"provider_raw": result.raw}
        raw.setdefault("customer_defaults", []).append({"field": code_field.field_name, "value": "3", "reason": DEFAULT_CODE_REASON})
        result.raw = raw
    carrier_present = any(f.field_name.strip().lower().startswith("1a.") and str(f.value or "").strip()
                          for f in result.fields)
    naic = next((f for f in result.fields if f.field_name.strip().lower().startswith("1c.")), None)
    if carrier_present and (naic is None or str(naic.value or "").strip().casefold() in {"", "n/a", "na", "not provided"}):
        if naic is None:
            naic = NormalizedExtractionField(field_name="1c. NAIC Code")
            result.fields.append(naic)
        naic.value = "00000"
        naic.candidate_values = ["00000"]
        naic.confidence = 1.0
        naic.source_text = DEFAULT_NAIC_REASON
        naic.evidence.append(SourceEvidence(provider="Customer configured default", source_text=DEFAULT_NAIC_REASON))
        raw = dict(result.raw) if isinstance(result.raw, dict) else {"provider_raw": result.raw}
        raw.setdefault("customer_defaults", []).append({"field": naic.field_name, "value": "00000", "reason": DEFAULT_NAIC_REASON})
        result.raw = raw
