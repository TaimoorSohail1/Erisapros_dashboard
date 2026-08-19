from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from app.models import ExtractedField, FormType


SCHEDULE_A_TAGS_BY_RULE = {
    "schedule_a_part_i_1a_name_of_insurance_company": "InsCarrierName",
    "schedule_a_part_i_1b_insurance_carrier_ein": "InsCarrierEIN",
    "schedule_a_part_i_1c_naic_code": "InsCarrierNAICCode",
    "schedule_a_part_i_1d_contract_policy_number": "InsContractNum",
    "schedule_a_part_i_1e_persons_covered_end_of_policy_year": "InsPrsnCoveredEoyCnt",
    "schedule_a_part_i_1f_policy_year_beginning_date": "InsPolicyFromDate",
    "schedule_a_part_i_1g_policy_year_ending_date": "InsPolicyToDate",
    "schedule_a_part_i_3a_name_of_agent_broker_person": "Name1",
    "schedule_a_part_i_3b_amount_of_commissions": "CommPdAmt1",
    "schedule_a_part_i_3c_amount_of_fees": "FeesPdAmt1",
    "schedule_a_part_i_3d_purpose": "FeesPdText1",
    "schedule_a_part_i_3e_organizational_code": "Code1",
    "schedule_a_part_iv_4a_plan_name": "PlanName",
    "schedule_a_part_iv_4b_plan_number_pn": "PlanNum",
    "schedule_a_part_iv_4c_sponsor_ein": "EIN",
    "schedule_a_part_iv_4d_plan_year_beginning_date": "PlanYearBeginDate",
    "schedule_a_part_iv_4e_plan_year_ending_date": "PlanYearEndDate",
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
    "schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier": "WlfrTotChargesPaidAmt",
    "schedule_a_part_iii_11_did_the_insurance_company_fail_to_provide_any_information_necessary_to_complete_schedule_a": "InsFailProvideInfoInd",
}


FORM_5500_TAGS_BY_RULE = {
    "form_5500_part_i_1a_plan_name": "PLAN_NAME0",
    "form_5500_part_i_1b_plan_number_pn": "SPONS_DFE_PN",
    "form_5500_part_i_1c_plan_effective_date": "PLAN_EFF_DATE",
    "form_5500_part_i_1d_plan_sponsor_name": "SPONS_DFE_NAME0",
    "form_5500_part_i_1e_plan_sponsor_ein": "SPONS_DFE_EIN",
    "form_5500_part_i_1f_plan_sponsor_address": "SPONS_DFE_MAIL_STR_ADDRESS",
    "form_5500_part_i_1g_business_code": "BUSINESS_CODE",
    "form_5500_part_i_2a_plan_administrator_name": "ADMIN_NAME0",
    "form_5500_part_i_6_plan_year_beginning_date": "FORM_PLAN_YEAR_BEGIN_DATE",
    "form_5500_part_i_7_plan_year_ending_date": "FORM_TAX_PRD",
    "form_5500_part_ii_4_plan_characteristic_codes": "TYPE_WELFARE_BNFT_CODE1",
    "form_5500_part_ii_8c_welfare_benefit_features": "TYPE_WELFARE_BNFT_CODE1",
    "form_5500_part_ii_9_plan_funding_arrangement": "FundingInsuranceInd",
    "form_5500_part_ii_10a_plan_benefit_arrangement": "BenefitInsuranceInd",
    "form_5500_part_ii_10b_schedules_attached": "SCH_A_ATTACHED_IND",
    "form_5500_part_ii_11_total_participants_at_beginning_of_year": "TotPartcpBoyCnt",
    "form_5500_part_ii_12_total_participants_at_end_of_year": "SubtlActRtdSepCnt",
    "form_5500_part_ii_13_active_participants_at_beginning": "TotActPartcpBoyCnt",
    "form_5500_part_ii_14_active_participants_at_end": "TotActivePartcpCnt",
    "form_5500_part_ii_15_retired_separated_participants_receiving_benefits": "RtdSepPartcpRcvgCnt",
    "form_5500_part_ii_16_other_retired_separated_participants_entitled_to_benefits": "RtdSepPartcpFutCnt",
}


SCHEDULE_A_CURRENT_TAGS_BY_RULE = {
    "schedule_a_part_i_1a_name_of_insurance_company": "InsCarrierName",
    "schedule_a_part_i_1b_insurance_carrier_ein": "InsCarrierEIN",
    "schedule_a_part_i_1c_naic_code": "InsCarrierNAICCode",
    "schedule_a_part_i_1d_contract_policy_number": "InsContractNum",
    "schedule_a_part_i_1e_persons_covered_end_of_policy_year": "InsPrsnCoveredEoyCnt",
    "schedule_a_part_i_1f_policy_year_beginning_date": "InsPolicyFromDate",
    "schedule_a_part_i_1g_policy_year_ending_date": "InsPolicyToDate",
    "schedule_a_part_i_3a_name_of_agent_broker_person": "Name1",
    "schedule_a_part_i_3b_amount_of_commissions": "CommPdAmt01",
    "schedule_a_part_i_3c_amount_of_fees": "FeesPdAmt01",
    "schedule_a_part_i_3d_purpose": "FeesPdText01",
    "schedule_a_part_i_3e_organizational_code": "Code01",
    "schedule_a_part_iv_4a_plan_name": "PlanName",
    "schedule_a_part_iv_4b_plan_number_pn": "PlanNum",
    "schedule_a_part_iv_4c_sponsor_ein": "EIN",
    "schedule_a_part_iv_4d_plan_year_beginning_date": "PlanYearBeginDate",
    "schedule_a_part_iv_4e_plan_year_ending_date": "PlanYearEndDate",
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
    "schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier": "WlfrTotChargesPaidAmt",
    "schedule_a_part_iii_11_did_the_insurance_company_fail_to_provide_any_information_necessary_to_complete_schedule_a": "InsFailProvideInfoInd",
}


FORM_5500_CURRENT_TAGS_BY_RULE = {
    "form_5500_part_i_1a_plan_name": "PlanName",
    "form_5500_part_i_1b_plan_number_pn": "SponsDfePlanNum",
    "form_5500_part_i_1c_plan_effective_date": "PlanEffDate",
    "form_5500_part_i_1d_plan_sponsor_name": "SDName",
    "form_5500_part_i_1e_plan_sponsor_ein": "SDEIN",
    "form_5500_part_i_1g_business_code": "BusinessCode",
    "form_5500_part_i_2a_plan_administrator_name": "ADMINName",
    "form_5500_part_i_6_plan_year_beginning_date": "PlanYearBeginDate",
    "form_5500_part_i_7_plan_year_ending_date": "PlanYearEndDate",
    "form_5500_part_ii_9_plan_funding_arrangement": "FundingInsuranceInd",
    "form_5500_part_ii_10a_plan_benefit_arrangement": "BenefitInsuranceInd",
    "form_5500_part_ii_10b_schedules_attached": "SchAAttachedInd",
    "form_5500_part_ii_11_total_participants_at_beginning_of_year": "TotPartcpBoyCnt",
    "form_5500_part_ii_12_total_participants_at_end_of_year": "SubtlActRtdSepCnt",
    "form_5500_part_ii_13_active_participants_at_beginning": "TotActPartcpBoyCnt",
    "form_5500_part_ii_14_active_participants_at_end": "TotActivePartcpCnt",
    "form_5500_part_ii_15_retired_separated_participants_receiving_benefits": "RtdSepPartcpRcvgCnt",
    "form_5500_part_ii_16_other_retired_separated_participants_entitled_to_benefits": "RtdSepPartcpFutCnt",
}

FORM_5500_UNSUPPORTED_UPDATE_RULES = {
    "form_5500_part_i_1a_plan_name",
    "form_5500_part_i_1e_plan_sponsor_ein",
    "form_5500_part_i_1f_plan_sponsor_address",
    "form_5500_part_i_2a_plan_administrator_name",
}

FORM_5500_UPDATE_TAGS_BY_RULE = {
    rule_key: tag
    for rule_key, tag in FORM_5500_TAGS_BY_RULE.items()
    if rule_key not in FORM_5500_UNSUPPORTED_UPDATE_RULES
}


FORM_5500_PRIOR_YEAR_ANNUAL_RULES = {
    "form_5500_part_i_6_plan_year_beginning_date",
    "form_5500_part_i_7_plan_year_ending_date",
    "form_5500_part_ii_11_total_participants_at_beginning_of_year",
    "form_5500_part_ii_12_total_participants_at_end_of_year",
    "form_5500_part_ii_13_active_participants_at_beginning",
    "form_5500_part_ii_14_active_participants_at_end",
    "form_5500_part_ii_15_retired_separated_participants_receiving_benefits",
    "form_5500_part_ii_16_other_retired_separated_participants_entitled_to_benefits",
}

SCHEDULE_A_PRIOR_YEAR_ANNUAL_RULES = {
    "schedule_a_part_i_1e_persons_covered_end_of_policy_year",
    "schedule_a_part_i_1f_policy_year_beginning_date",
    "schedule_a_part_i_1g_policy_year_ending_date",
    "schedule_a_part_i_3b_amount_of_commissions",
    "schedule_a_part_i_3c_amount_of_fees",
    "schedule_a_part_iv_4d_plan_year_beginning_date",
    "schedule_a_part_iv_4e_plan_year_ending_date",
    "schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier",
} | {
    rule_key
    for rule_key in SCHEDULE_A_CURRENT_TAGS_BY_RULE
    if rule_key.startswith("schedule_a_part_iii_9")
}


def strip_prior_year_annual_values(form_type: FormType, current_values: dict[str, str]) -> dict[str, str]:
    """Keep stable FTW identity data while suppressing annual carry-forward values."""
    if not current_values:
        return {}

    if form_type == FormType.FORM_5500:
        rules = FORM_5500_PRIOR_YEAR_ANNUAL_RULES
        mappings = (FORM_5500_CURRENT_TAGS_BY_RULE, FORM_5500_UPDATE_TAGS_BY_RULE)
    elif form_type == FormType.SCHEDULE_A:
        rules = SCHEDULE_A_PRIOR_YEAR_ANNUAL_RULES
        mappings = (SCHEDULE_A_CURRENT_TAGS_BY_RULE, SCHEDULE_A_TAGS_BY_RULE)
    else:
        return dict(current_values)

    annual_tags = {
        str(mapping.get(rule_key) or "").casefold()
        for rule_key in rules
        for mapping in mappings
        if mapping.get(rule_key)
    }
    protected: dict[str, str] = {}
    for tag, value in current_values.items():
        normalized_tag = str(tag or "").strip()
        if normalized_tag.casefold() in annual_tags:
            continue
        if form_type == FormType.SCHEDULE_A and re.fullmatch(
            r"(?:CommPdAmt|FeesPdAmt)0*\d+",
            normalized_tag,
            flags=re.IGNORECASE,
        ):
            continue
        protected[tag] = value
    return protected


def resolve_ftw_tag(field: ExtractedField) -> str | None:
    if field.form_type == FormType.FORM_5500:
        return FORM_5500_TAGS_BY_RULE.get(str(field.mapped_rule_key or "")) or existing_real_tag(field)
    if field.form_type == FormType.SCHEDULE_A:
        return SCHEDULE_A_TAGS_BY_RULE.get(str(field.mapped_rule_key or "")) or existing_real_tag(field)
    return existing_real_tag(field)


def resolve_ftw_update_tag(field: ExtractedField) -> str | None:
    if field.form_type == FormType.FORM_5500:
        return FORM_5500_UPDATE_TAGS_BY_RULE.get(str(field.mapped_rule_key or ""))
    if field.form_type == FormType.SCHEDULE_A:
        return SCHEDULE_A_TAGS_BY_RULE.get(str(field.mapped_rule_key or ""))
    return None


def resolve_ftw_current_tag(field: ExtractedField) -> str | None:
    if field.form_type == FormType.FORM_5500:
        return FORM_5500_CURRENT_TAGS_BY_RULE.get(str(field.mapped_rule_key or "")) or resolve_ftw_tag(field)
    if field.form_type == FormType.SCHEDULE_A:
        return SCHEDULE_A_CURRENT_TAGS_BY_RULE.get(str(field.mapped_rule_key or "")) or resolve_ftw_tag(field)
    return resolve_ftw_tag(field)


def resolve_ftw_current_value(field: ExtractedField, current_values: dict[str, str]) -> str:
    rule_key = str(field.mapped_rule_key or "")
    if field.form_type == FormType.FORM_5500:
        if rule_key == "form_5500_part_ii_9_plan_funding_arrangement":
            return _indicator_summary(
                current_values,
                [
                    ("FundingInsuranceInd", "Insurance"),
                    ("FundingCdSection412Ind", "Code section 412(e)(3) insurance contracts"),
                    ("FundingTrustInd", "Trust"),
                    ("FundingGeneralAssetInd", "General assets of the sponsor"),
                ],
            )
        if rule_key == "form_5500_part_ii_10a_plan_benefit_arrangement":
            return _indicator_summary(
                current_values,
                [
                    ("BenefitInsuranceInd", "Insurance"),
                    ("BenefitCdSection412Ind", "Code section 412(e)(3) insurance contracts"),
                    ("BenefitTrustInd", "Trust"),
                    ("BenefitGeneralAssetInd", "General assets of the sponsor"),
                ],
            )
        if rule_key == "form_5500_part_ii_10b_schedules_attached":
            return _indicator_summary(
                current_values,
                [
                    ("SchRAttachedInd", "R"),
                    ("SchMBAttachedInd", "MB"),
                    ("SchSBAttachedInd", "SB"),
                    ("SchDCGAttachedInd", "DCG"),
                    ("SchMEPAttachedInd", "MEP"),
                    ("SchHAttachedInd", "H"),
                    ("SchIAttachedInd", "I"),
                    ("SchAAttachedInd", "A"),
                    ("SchCAttachedInd", "C"),
                    ("SchDAttachedInd", "D"),
                    ("SchGAttachedInd", "G"),
                ],
            )
        if rule_key == "form_5500_part_i_1f_plan_sponsor_address":
            return _join_address(current_values, "SDAddressLine1", "SDAddressLine2", "SDCity", "SDState", "SDZipCode")
        if rule_key == "form_5500_part_i_2a_plan_administrator_name" and str(current_values.get("AdminNameSameAsPlanSponsInd", "")).strip() == "1":
            return str(current_values.get("SDName", "") or "").strip()
        if rule_key in {"form_5500_part_ii_4_plan_characteristic_codes", "form_5500_part_ii_8c_welfare_benefit_features"}:
            codes = [str(current_values.get(f"TypeWelfareBnftCode{index}", "") or "").strip() for index in range(1, 21)]
            return " ".join(code for code in codes if code)

    tag = resolve_ftw_current_tag(field)
    value = str(current_values.get(tag or "", "") or "").strip()
    if value:
        return value

    return value


def existing_real_tag(field: ExtractedField) -> str | None:
    tag = str(field.xml_tag or "").strip()
    if tag and not tag.lower().startswith("field_"):
        return tag
    if field.ftw_field and looks_like_ftw_tag(field.ftw_field):
        return field.ftw_field
    return None


def looks_like_ftw_tag(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]*", value.strip()))


def normalize_compare_value(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    if _looks_like_date(text):
        return re.sub(r"\D", "", text)
    text = text.replace("&", " and ")
    return re.sub(r"\s+", " ", re.sub(r"[,\.;:]", " ", text)).strip()


def values_meaningfully_different(
    current_value: object,
    proposed_value: object,
    *,
    tag: str | None = None,
) -> bool:
    current = str(current_value or "").strip()
    proposed = str(proposed_value or "").strip()
    if tag == "InsFailProvideInfoInd":
        current_indicator = _one_two_indicator_value(current)
        proposed_indicator = _one_two_indicator_value(proposed)
        if current_indicator and proposed_indicator:
            return current_indicator != proposed_indicator
    if _looks_numeric(current) and _looks_numeric(proposed):
        current_number = _parse_decimal(current)
        proposed_number = _parse_decimal(proposed)
        if current_number is not None and proposed_number is not None:
            return abs(current_number - proposed_number) >= Decimal("0.5")
    return normalize_compare_value(current) != normalize_compare_value(proposed)


def _one_two_indicator_value(value: str) -> str | None:
    return {
        "1": "1",
        "y": "1",
        "yes": "1",
        "true": "1",
        "2": "2",
        "0": "2",
        "n": "2",
        "no": "2",
        "false": "2",
    }.get(value.strip().casefold())


def _indicator_summary(current_values: dict[str, str], indicators: list[tuple[str, str]]) -> str:
    labels = [label for key, label in indicators if str(current_values.get(key, "") or "").strip() in {"1", "Y", "Yes", "YES", "true", "True"}]
    return ", ".join(labels)


def _join_address(current_values: dict[str, str], line_1: str, line_2: str, city: str, state: str, zip_code: str) -> str:
    street = ", ".join(part for part in [current_values.get(line_1, ""), current_values.get(line_2, "")] if str(part or "").strip())
    locality = " ".join(part for part in [current_values.get(city, ""), current_values.get(state, ""), current_values.get(zip_code, "")] if str(part or "").strip())
    return ", ".join(part for part in [street, locality] if part)


def _looks_like_date(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return len(digits) == 8 and bool(re.fullmatch(r"[0-9/\-]+", value))


def _looks_numeric(value: str) -> bool:
    return bool(re.fullmatch(r"[$()0-9,.\- ]+", value))


def _parse_decimal(value: str) -> Decimal | None:
    cleaned = value.strip().replace("$", "").replace(",", "")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
