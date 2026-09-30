"""Customer-authorized defaults for proposed recipients, never FTW snapshots."""
from decimal import Decimal, InvalidOperation

from app.models import (
    NormalizedExtractionField,
    NormalizedExtractionResult,
    ScheduleABrokerMoneyRow,
    ScheduleABrokerRow,
    SourceEvidence,
)

DEFAULT_CODE_REASON = "Customer rule: Defaulted to 3 because organizational code was blank."


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
        if _explicit_zero(row.commission_total) and _explicit_zero(row.fee_total):
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
        raw["customer_defaults"] = [{"field": code_field.field_name, "value": "3", "reason": DEFAULT_CODE_REASON}]
        result.raw = raw
