from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

from app.models import ExtractedField, ExtractedFieldStatus, FieldRule, FieldRuleApplicability, FormType, ScheduleAContractType


EXPERIENCE_PREMIUM_RULES = {
    "schedule_a_part_iii_9a_premiums_1_amount_received",
    "schedule_a_part_iii_9a_2_increase_decrease_in_amount_due_but_unpaid",
    "schedule_a_part_iii_9a_3_increase_decrease_in_unearned_premium_reserve",
    "schedule_a_part_iii_9a_4_earned_1_2_3",
}

EXPERIENCE_CLAIM_RULES = {
    "schedule_a_part_iii_9b_1_benefit_charges_1_claims_paid",
    "schedule_a_part_iii_9b_2_increase_decrease_in_claim_reserves",
    "schedule_a_part_iii_9b_3_incurred_claims_add_1_and_2",
    "schedule_a_part_iii_9b_4_claims_charged",
}

EXPERIENCE_ADMIN_RULES = {
    "schedule_a_part_iii_9c_1_a_commissions",
    "schedule_a_part_iii_9c_1_b_administrative_service_or_other_fees",
    "schedule_a_part_iii_9c_1_c_other_specific_acquisition_costs",
    "schedule_a_part_iii_9c_1_d_other_expenses",
    "schedule_a_part_iii_9c_1_e_taxes",
    "schedule_a_part_iii_9c_1_f_charges_for_risks_or_other_contingencies",
    "schedule_a_part_iii_9c_1_g_other_retention_charges",
    "schedule_a_part_iii_9c_1_h_total_retention",
}

EXPERIENCE_OTHER_RULES = {
    "schedule_a_part_iii_9c_2_dividends_or_retroactive_rate_refunds",
    "schedule_a_part_iii_9d_1_status_of_policyholder_reserves_at_end_of_year_1_amount_held_to_provide_benefits_after_retirement",
    "schedule_a_part_iii_9d_2_claim_reserves",
    "schedule_a_part_iii_9d_3_other_reserves",
    "schedule_a_part_iii_9e_dividends_or_retroactive_rate_refunds_due",
}

NONEXPERIENCE_PREMIUM_RULE = "schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier"
EXPERIENCE_RULES = EXPERIENCE_PREMIUM_RULES | EXPERIENCE_CLAIM_RULES | EXPERIENCE_ADMIN_RULES | EXPERIENCE_OTHER_RULES
NONEXPERIENCE_RULES = {NONEXPERIENCE_PREMIUM_RULE}
NONEXPERIENCE_ZERO_RULES = {
    "schedule_a_part_iii_9a_4_earned_1_2_3",
    "schedule_a_part_iii_9b_3_incurred_claims_add_1_and_2",
    "schedule_a_part_iii_9c_1_h_total_retention",
}

CURRENT_TAGS_BY_RULE = {
    "schedule_a_part_iii_9a_premiums_1_amount_received": "WlfrPremiumRcvdAmt",
    "schedule_a_part_iii_9a_2_increase_decrease_in_amount_due_but_unpaid": "WlfrUnpaidDueAmt",
    "schedule_a_part_iii_9a_3_increase_decrease_in_unearned_premium_reserve": "WlfrReserveAmt",
    "schedule_a_part_iii_9a_4_earned_1_2_3": "WlfrTotEarnedPremAmt",
    "schedule_a_part_iii_9b_1_benefit_charges_1_claims_paid": "WlfrClaimsPaidAmt",
    "schedule_a_part_iii_9b_2_increase_decrease_in_claim_reserves": "WlfrIncrReserveAmt",
    "schedule_a_part_iii_9b_3_incurred_claims_add_1_and_2": "WlfrIncurredClaimAmt",
    "schedule_a_part_iii_9b_4_claims_charged": "WlfrClaimsChrgdAmt",
    "schedule_a_part_iii_9c_1_a_commissions": "WlfrRetCommissionsAmt",
    "schedule_a_part_iii_9c_1_b_administrative_service_or_other_fees": "WlfrRetAdminAmt",
    "schedule_a_part_iii_9c_1_c_other_specific_acquisition_costs": "WlfrRetOthCostAmt",
    "schedule_a_part_iii_9c_1_d_other_expenses": "WlfrRetOthExpenseAmt",
    "schedule_a_part_iii_9c_1_e_taxes": "WlfrRetTaxesAmt",
    "schedule_a_part_iii_9c_1_f_charges_for_risks_or_other_contingencies": "WlfrRetChargesAmt",
    "schedule_a_part_iii_9c_1_g_other_retention_charges": "WlfrRetOthChrgsAmt",
    "schedule_a_part_iii_9c_1_h_total_retention": "WlfrRetTotAmt",
    "schedule_a_part_iii_9c_2_dividends_or_retroactive_rate_refunds": "WlfrRefundAmt",
    "schedule_a_part_iii_9d_1_status_of_policyholder_reserves_at_end_of_year_1_amount_held_to_provide_benefits_after_retirement": "WlfrHeldBnftsAmt",
    "schedule_a_part_iii_9d_2_claim_reserves": "WlfrClaimsReserveAmt",
    "schedule_a_part_iii_9d_3_other_reserves": "WlfrOthReserveAmt",
    "schedule_a_part_iii_9e_dividends_or_retroactive_rate_refunds_due": "WlfrDivndsDueAmt",
    NONEXPERIENCE_PREMIUM_RULE: "WlfrTotChargesPaidAmt",
}


@dataclass(frozen=True)
class ScheduleAClassification:
    contract_type: ScheduleAContractType
    reason: str
    confidence: float = 1.0
    evidence: tuple[str, ...] = ()


def classification_signals_from_text(text: str | None) -> list[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower())
    signals: set[str] = set()
    nonexperience_pattern = r"\bnon\s*experience\s*rated\b"
    if re.search(nonexperience_pattern, normalized):
        signals.add("EXPLICIT_NONEXPERIENCE_RATED")
    without_nonexperience = re.sub(nonexperience_pattern, " ", normalized)
    # A carrier worksheet may print the line 9 heading followed by "not
    # applicable" immediately before its line 10 values. That form heading is
    # not evidence that the contract itself is experience-rated.
    without_nonexperience = re.sub(
        r"\bexperience\s*rated(?:\s+contracts?)?\b.{0,80}?\b(?:not\s+applicable|n\s+a)\b",
        " ",
        without_nonexperience,
    )
    for match in re.finditer(r"\bexperience\s*rated\b", without_nonexperience):
        before = without_nonexperience[max(0, match.start() - 80) : match.start()]
        after = without_nonexperience[match.end() : match.end() + 100]
        # Explanatory instructions such as "may be combined if contracts are
        # experience rated" describe a possible filing rule, not this record.
        if re.search(r"\b(?:if|may|might|could|when|where)\b", before):
            continue
        if re.search(r"\b(?:not\s+applicable|n\s+a)\b", after):
            continue
        signals.add("EXPLICIT_EXPERIENCE_RATED")
        break
    return sorted(signals)


def classify_schedule_a_fields(
    fields: list[ExtractedField],
    classification_signals: list[str] | None = None,
) -> ScheduleAClassification:
    values = {
        str(field.mapped_rule_key or ""): str(field.proposed_value or field.value or "")
        for field in fields
        if field.form_type == FormType.SCHEDULE_A and field.mapped_rule_key
    }
    signals = [*(classification_signals or []), *_classification_signals_from_fields(fields)]
    return classify_schedule_a_values(values, signals)


def classify_schedule_a_current(current_values: dict[str, str]) -> ScheduleAClassification:
    values = {rule_key: str(current_values.get(tag) or "") for rule_key, tag in CURRENT_TAGS_BY_RULE.items()}
    return classify_schedule_a_values(values)


def apply_schedule_a_classification(
    fields: list[ExtractedField],
    classification_signals: list[str] | None = None,
) -> ScheduleAClassification:
    for field in fields:
        if not str(field.status_reason or "").startswith("Automatically derived"):
            continue
        original_value = str(field.value or "").strip()
        field.proposed_value = original_value
        field.status = ExtractedFieldStatus.MATCHED if original_value else ExtractedFieldStatus.MISSING
        field.status_reason = (
            "Restored the extracted value before automatic Schedule A classification."
            if original_value
            else "No extracted value was available before automatic Schedule A classification."
        )

    classification = classify_schedule_a_fields(fields, classification_signals)
    classification_source = _trusted_classification_source(fields, classification)
    if classification.contract_type == ScheduleAContractType.NONEXPERIENCE_RATED:
        line_10a = next((field for field in fields if field.mapped_rule_key == NONEXPERIENCE_PREMIUM_RULE), None)
        premium_source = _premium_amount_source(fields)
        if line_10a and premium_source and not _has_meaningful_amount(line_10a.proposed_value or line_10a.value):
            line_10a.proposed_value = str(premium_source.proposed_value or premium_source.value or "").strip()
            trusted_premium = _field_has_trusted_source_evidence(premium_source)
            line_10a.status = (
                ExtractedFieldStatus.MATCHED if trusted_premium else ExtractedFieldStatus.LOW_CONFIDENCE
            )
            line_10a.confidence = premium_source.confidence if trusted_premium else min(premium_source.confidence, 0.5)
            line_10a.status_reason = (
                f"Automatically derived from premium evidence in {premium_source.source_field_name}."
                if trusted_premium
                else "Automatically derived from premium evidence, but needs Review because the source field lacks trusted page-level evidence."
            )
            line_10a.page = premium_source.page
            line_10a.source_text = premium_source.source_text
            line_10a.updated_at = datetime.utcnow()

    zero_rules = (
        {NONEXPERIENCE_PREMIUM_RULE}
        if classification.contract_type == ScheduleAContractType.EXPERIENCE_RATED
        else NONEXPERIENCE_ZERO_RULES
    )
    for field in fields:
        if field.form_type != FormType.SCHEDULE_A or field.mapped_rule_key not in zero_rules:
            continue
        direct_source = field if _field_has_trusted_source_evidence(field) and str(field.value or "").strip() else None
        trusted_source = direct_source or classification_source
        field.proposed_value = "0"
        field.status = (
            ExtractedFieldStatus.MATCHED if trusted_source else ExtractedFieldStatus.LOW_CONFIDENCE
        )
        field.confidence = (
            min(field.confidence or trusted_source.confidence, trusted_source.confidence)
            if trusted_source
            else min(field.confidence, 0.5)
        )
        rating_label = (
            "experience-rated"
            if classification.contract_type == ScheduleAContractType.EXPERIENCE_RATED
            else "nonexperience-rated"
        )
        if trusted_source:
            field.page = trusted_source.page
            field.source_text = trusted_source.source_text
            field.status_reason = (
                f"Automatically derived as zero for a {rating_label} Schedule A using page-level classification evidence."
            )
        else:
            field.status_reason = (
                f"Automatically derived as zero for a {rating_label} Schedule A, but needs Review because page-level classification evidence is missing or uncertain."
            )
        field.updated_at = datetime.utcnow()
    return classification


def _field_has_trusted_source_evidence(field: ExtractedField | None) -> bool:
    return bool(
        field
        and field.status == ExtractedFieldStatus.MATCHED
        and field.page is not None
        and str(field.source_text or "").strip()
    )


def _trusted_classification_source(
    fields: list[ExtractedField],
    classification: ScheduleAClassification,
) -> ExtractedField | None:
    for field in fields:
        if not _field_has_trusted_source_evidence(field):
            continue
        text = " ".join(
            str(value or "")
            for value in (field.mapped_rule_key, field.source_field_name, field.source_text)
        ).lower()
        if classification.contract_type == ScheduleAContractType.EXPERIENCE_RATED:
            if field.mapped_rule_key in EXPERIENCE_PREMIUM_RULES or "experience rated" in text:
                return field
        elif classification.contract_type == ScheduleAContractType.NONEXPERIENCE_RATED:
            if field.mapped_rule_key == NONEXPERIENCE_PREMIUM_RULE or "nonexperience rated" in text:
                return field
    return None


def _premium_amount_source(fields: list[ExtractedField]) -> ExtractedField | None:
    candidates: list[ExtractedField] = []
    for field in fields:
        value = str(field.proposed_value or field.value or "")
        if not _has_meaningful_amount(value):
            continue
        text = " ".join(
            str(item or "")
            for item in (field.source_field_name, field.mapped_label, field.source_text)
        ).lower()
        if re.search(r"\bpremiums?\b|\bsubscription\s+charges?\b", text):
            candidates.append(field)
    return max(candidates, key=lambda field: field.confidence, default=None)


def classify_schedule_a_values(
    values_by_rule_key: dict[str, str],
    classification_signals: list[str] | None = None,
) -> ScheduleAClassification:
    signals = set(classification_signals or [])
    has_premium = any(_has_meaningful_amount(values_by_rule_key.get(rule)) for rule in EXPERIENCE_PREMIUM_RULES)
    has_claims = any(_has_meaningful_amount(values_by_rule_key.get(rule)) for rule in EXPERIENCE_CLAIM_RULES)
    has_10a = _has_meaningful_amount(values_by_rule_key.get(NONEXPERIENCE_PREMIUM_RULE))
    has_generic_premium = has_10a or "PREMIUM_AMOUNT_PRESENT" in signals
    has_generic_claims = "CLAIM_AMOUNT_PRESENT" in signals

    if has_premium:
        return ScheduleAClassification(
            ScheduleAContractType.EXPERIENCE_RATED,
            "Experience-rated because a meaningful Schedule A line 9a premium value is present.",
            0.99,
            ("LINE_9A_AMOUNT_PRESENT",),
        )
    if "EXPLICIT_EXPERIENCE_RATED" in signals:
        return ScheduleAClassification(
            ScheduleAContractType.EXPERIENCE_RATED,
            "Experience-rated because the Schedule A explicitly labels the premiums as experience rated.",
            0.98,
            ("EXPLICIT_EXPERIENCE_RATED",),
        )
    if has_generic_premium and (has_claims or has_generic_claims):
        return ScheduleAClassification(
            ScheduleAContractType.EXPERIENCE_RATED,
            "Experience-rated because the Schedule A contains both premium and claim amounts.",
            0.95,
            ("PREMIUM_AMOUNT_PRESENT", "CLAIM_AMOUNT_PRESENT"),
        )
    if "EXPLICIT_NONEXPERIENCE_RATED" in signals:
        return ScheduleAClassification(
            ScheduleAContractType.NONEXPERIENCE_RATED,
            "Nonexperience-rated because the Schedule A explicitly labels the premiums as nonexperience rated.",
            0.98,
            ("EXPLICIT_NONEXPERIENCE_RATED",),
        )
    if has_10a or has_generic_premium:
        return ScheduleAClassification(
            ScheduleAContractType.NONEXPERIENCE_RATED,
            "Nonexperience-rated because premium amounts are present without claim amounts or experience-rated evidence.",
            0.9,
            ("PREMIUM_AMOUNT_PRESENT",),
        )
    return ScheduleAClassification(
        ScheduleAContractType.NONEXPERIENCE_RATED,
        "Nonexperience-rated by the default rule because the available evidence does not clearly establish experience rating.",
        0.6,
        ("DEFAULT_NONEXPERIENCE",),
    )


def _classification_signals_from_fields(fields: list[ExtractedField]) -> list[str]:
    signals: set[str] = set()
    for field in fields:
        if field.form_type not in {None, FormType.SCHEDULE_A}:
            continue
        text = " ".join(
            str(value or "")
            for value in (
                field.source_field_name,
                field.mapped_label,
                field.source_text,
            )
        ).lower()
        compact = re.sub(r"[^a-z0-9]+", " ", text)
        signals.update(classification_signals_from_text(compact))

        value = str(field.proposed_value or field.value or "")
        if not _has_meaningful_amount(value):
            continue
        if re.search(r"\bpremiums?\b|\bsubscription\s+charges?\b", compact):
            signals.add("PREMIUM_AMOUNT_PRESENT")
        if re.search(r"\bclaims?\b|\bbenefit\s+charges?\b", compact):
            signals.add("CLAIM_AMOUNT_PRESENT")
    return sorted(signals)


def schedule_a_contract_type_allows_rule(contract_type: ScheduleAContractType, rule_key: str | None) -> bool:
    key = str(rule_key or "")
    if contract_type == ScheduleAContractType.EXPERIENCE_RATED:
        if key == NONEXPERIENCE_PREMIUM_RULE:
            return True
        return key not in NONEXPERIENCE_RULES
    if contract_type == ScheduleAContractType.NONEXPERIENCE_RATED:
        return key in NONEXPERIENCE_ZERO_RULES or key not in EXPERIENCE_RULES
    if key in EXPERIENCE_RULES or key in NONEXPERIENCE_RULES:
        return False
    return True


def filter_schedule_a_fields_for_contract_type(
    fields: list[ExtractedField],
    contract_type: ScheduleAContractType,
    rules: list[FieldRule] | None = None,
) -> list[ExtractedField]:
    applicability_by_key = {rule.key: rule.applicability for rule in (rules or [])}

    def is_allowed(field: ExtractedField) -> bool:
        key = str(field.mapped_rule_key or "")
        derived_zero = _is_zero_amount(field.proposed_value) and str(field.status_reason or "").startswith("Automatically derived")
        if contract_type == ScheduleAContractType.EXPERIENCE_RATED and key == NONEXPERIENCE_PREMIUM_RULE:
            return derived_zero
        if contract_type == ScheduleAContractType.NONEXPERIENCE_RATED and key in NONEXPERIENCE_ZERO_RULES:
            return derived_zero
        applicability = applicability_by_key.get(str(field.mapped_rule_key or ""))
        if applicability == FieldRuleApplicability.EXPERIENCE:
            return contract_type == ScheduleAContractType.EXPERIENCE_RATED
        if applicability == FieldRuleApplicability.NONEXPERIENCE:
            return contract_type == ScheduleAContractType.NONEXPERIENCE_RATED
        return schedule_a_contract_type_allows_rule(contract_type, field.mapped_rule_key)

    return [
        field
        for field in fields
        if is_allowed(field)
    ]


def _is_zero_amount(value: str | None) -> bool:
    number = _decimal_from_text(str(value or ""))
    return number == 0 if number is not None else False


def _has_meaningful_amount(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.lower() in {"n/a", "na", "not applicable", "none", "null"}:
        return False
    number = _decimal_from_text(text)
    if number is not None:
        return number != 0
    return False


def _decimal_from_text(value: str) -> Decimal | None:
    clean = re.sub(r"[^0-9.\-()]", "", value)
    if not clean:
        return None
    if clean.startswith("(") and clean.endswith(")"):
        clean = "-" + clean[1:-1]
    try:
        return Decimal(clean)
    except InvalidOperation:
        return None
