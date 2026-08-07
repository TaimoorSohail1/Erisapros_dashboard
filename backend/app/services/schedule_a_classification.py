from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

from app.models import ExtractedField, FormType, ScheduleAContractType


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


def classify_schedule_a_fields(fields: list[ExtractedField]) -> ScheduleAClassification:
    values = {
        str(field.mapped_rule_key or ""): str(field.proposed_value or field.value or "")
        for field in fields
        if field.form_type == FormType.SCHEDULE_A and field.mapped_rule_key
    }
    return classify_schedule_a_values(values)


def classify_schedule_a_current(current_values: dict[str, str]) -> ScheduleAClassification:
    values = {rule_key: str(current_values.get(tag) or "") for rule_key, tag in CURRENT_TAGS_BY_RULE.items()}
    return classify_schedule_a_values(values)


def classify_schedule_a_values(values_by_rule_key: dict[str, str]) -> ScheduleAClassification:
    has_premium = any(_has_meaningful_amount(values_by_rule_key.get(rule)) for rule in EXPERIENCE_PREMIUM_RULES)
    has_claims = any(_has_meaningful_amount(values_by_rule_key.get(rule)) for rule in EXPERIENCE_CLAIM_RULES)
    has_admin = any(_has_meaningful_amount(values_by_rule_key.get(rule)) for rule in EXPERIENCE_ADMIN_RULES)
    has_other = any(_has_meaningful_amount(values_by_rule_key.get(rule)) for rule in EXPERIENCE_OTHER_RULES)
    has_10a = _has_meaningful_amount(values_by_rule_key.get(NONEXPERIENCE_PREMIUM_RULE))
    has_any_experience = has_premium or has_claims or has_admin or has_other

    if has_any_experience and not has_10a:
        return ScheduleAClassification(
            ScheduleAContractType.EXPERIENCE_RATED,
            "Experience-rated because Schedule A line 9 values are present.",
        )
    if has_10a and not has_any_experience:
        return ScheduleAClassification(
            ScheduleAContractType.NONEXPERIENCE_RATED,
            "Nonexperience-rated because line 10a premium is present and line 9 values are blank or zero.",
        )
    if has_any_experience and has_10a:
        return ScheduleAClassification(
            ScheduleAContractType.NEEDS_REVIEW,
            "Both experience-rated line 9 values and nonexperience-rated line 10a are present.",
        )
    return ScheduleAClassification(
        ScheduleAContractType.UNKNOWN,
        "No experience-rated line 9 values or nonexperience-rated line 10a premium were found.",
    )


def schedule_a_contract_type_allows_rule(contract_type: ScheduleAContractType, rule_key: str | None) -> bool:
    key = str(rule_key or "")
    if contract_type == ScheduleAContractType.EXPERIENCE_RATED:
        return key not in NONEXPERIENCE_RULES
    if contract_type == ScheduleAContractType.NONEXPERIENCE_RATED:
        return key not in EXPERIENCE_RULES
    if key in EXPERIENCE_RULES or key in NONEXPERIENCE_RULES:
        return False
    return True


def filter_schedule_a_fields_for_contract_type(
    fields: list[ExtractedField],
    contract_type: ScheduleAContractType,
) -> list[ExtractedField]:
    return [
        field
        for field in fields
        if schedule_a_contract_type_allows_rule(contract_type, field.mapped_rule_key)
    ]


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
