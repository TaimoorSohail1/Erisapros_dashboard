from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

from app.models import FormType
from app.services.ftwilliams_tags import FORM_5500_UPDATE_TAGS_BY_RULE, SCHEDULE_A_TAGS_BY_RULE


FTW_CONTRACT_VERSION = "2026-08"


@dataclass(frozen=True)
class FTWFieldValidationIssue:
    tag: str
    value: str
    reason: str


class FTWPayloadValidationError(ValueError):
    def __init__(self, issues: list[FTWFieldValidationIssue]):
        self.issues = issues
        details = "; ".join(
            f"{issue.tag}:{issue.value} ({issue.reason})"
            for issue in issues
        )
        super().__init__(f"FT Williams pre-send validation failed: {details}")


FORM_5500_ALLOWED_UPDATE_TAGS = set(FORM_5500_UPDATE_TAGS_BY_RULE.values()) | {
    "FundingInsuranceInd",
    "FundingCdSection412Ind",
    "FundingTrustInd",
    "FundingGeneralAssetInd",
    "BenefitInsuranceInd",
    "BenefitCdSection412Ind",
    "BenefitTrustInd",
    "BenefitGeneralAssetInd",
}

SCHEDULE_A_ALLOWED_UPDATE_TAGS = set(SCHEDULE_A_TAGS_BY_RULE.values()) | {"ScheduleDesc"}

SCHEDULE_A_REPEATABLE_BROKER_TAG_BASES = {
    "ProvinceOrState",
    "AddressLine1",
    "AddressLine2",
    "FeesPdText",
    "CommPdAmt",
    "FeesPdAmt",
    "ForeignAddy",
    "PostalCode",
    "ZipCode",
    "Country",
    "State",
    "City",
    "Code",
    "Name",
}

DATE_TAGS = {
    "PLAN_EFF_DATE",
    "FORM_PLAN_YEAR_BEGIN_DATE",
    "FORM_TAX_PRD",
    "InsPolicyFromDate",
    "InsPolicyToDate",
    "PlanYearBeginDate",
    "PlanYearEndDate",
}

INTEGER_TAGS = {
    "TotPartcpBoyCnt",
    "SubtlActRtdSepCnt",
    "TotActPartcpBoyCnt",
    "TotActivePartcpCnt",
    "RtdSepPartcpRcvgCnt",
    "RtdSepPartcpFutCnt",
    "InsPrsnCoveredEoyCnt",
}

EIN_TAGS = {"InsCarrierEIN", "EIN", "SPONS_DFE_EIN"}
NAIC_TAGS = {"InsCarrierNAICCode"}
PLAN_NUMBER_TAGS = {"SPONS_DFE_PN", "PlanNum"}
BUSINESS_CODE_TAGS = {"BUSINESS_CODE"}
ZERO_ONE_INDICATOR_TAGS = {
    "FundingInsuranceInd",
    "FundingCdSection412Ind",
    "FundingTrustInd",
    "FundingGeneralAssetInd",
    "BenefitInsuranceInd",
    "BenefitCdSection412Ind",
    "BenefitTrustInd",
    "BenefitGeneralAssetInd",
}
ONE_TWO_INDICATOR_TAGS = {"InsFailProvideInfoInd"}

TEXT_LIMITS = {
    "PLAN_NAME0": 140,
    "SPONSOR_DFE_NAME0": 70,
    "SPONS_DFE_MAIL_STR_ADDRESS": 35,
    "SPONS_DFE_CITY": 30,
    "SPONS_DFE_STATE": 2,
    "SPONS_DFE_ZIP_CODE": 10,
    "ADMIN_NAME0": 70,
    "InsCarrierName": 70,
    "InsContractNum": 40,
    "PlanName": 140,
    "ScheduleDesc": 8,
}


def normalize_ftw_update_value(form_type: FormType, tag: str, value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    if not _tag_is_allowed(form_type, tag):
        _raise(tag, text, f"tag is not approved by FTW contract {FTW_CONTRACT_VERSION}")

    if tag in DATE_TAGS:
        normalized = _normalize_date(text)
        if normalized is None:
            _raise(tag, text, "expected a valid date in MM/DD/YYYY, MM-DD-YYYY, or YYYY-MM-DD format")
        return normalized

    if tag in INTEGER_TAGS:
        return _normalize_integer(tag, text)

    if tag in EIN_TAGS:
        digits = re.sub(r"\D", "", text)
        if len(digits) != 9 or re.search(r"[A-Za-z]", text):
            _raise(tag, text, "expected a 9-digit EIN")
        return f"{digits[:2]}-{digits[2:]}"

    if tag in NAIC_TAGS:
        if not re.fullmatch(r"\d{5}", text):
            _raise(tag, text, "expected exactly 5 digits")
        return text

    if tag in PLAN_NUMBER_TAGS:
        if not re.fullmatch(r"\d{1,3}", text):
            _raise(tag, text, "expected a numeric plan number with at most 3 digits")
        return text.zfill(3)

    if tag in BUSINESS_CODE_TAGS:
        if not re.fullmatch(r"\d{6}", text):
            _raise(tag, text, "expected exactly 6 digits")
        return text

    if tag == "SPONS_DFE_STATE":
        if not re.fullmatch(r"[A-Za-z]{2}", text):
            _raise(tag, text, "expected a two-letter US state code")
        return text.upper()

    if tag == "SPONS_DFE_ZIP_CODE":
        digits = re.sub(r"\D", "", text)
        if len(digits) not in {5, 9} or re.search(r"[A-Za-z]", text):
            _raise(tag, text, "expected a 5- or 9-digit US ZIP code")
        return digits if len(digits) == 5 else f"{digits[:5]}-{digits[5:]}"

    if tag in ZERO_ONE_INDICATOR_TAGS:
        mapped = _normalized_choice(text, {"1": "1", "y": "1", "yes": "1", "true": "1", "insurance": "1", "0": "0", "n": "0", "no": "0", "false": "0"})
        if mapped is None:
            _raise(tag, text, "expected a yes/no indicator")
        return mapped

    if tag in ONE_TWO_INDICATOR_TAGS:
        mapped = _normalized_choice(text, {"1": "1", "y": "1", "yes": "1", "true": "1", "2": "2", "0": "2", "n": "2", "no": "2", "false": "2"})
        if mapped is None:
            _raise(tag, text, "expected a yes/no indicator")
        return mapped

    if _is_money_tag(tag):
        return _normalize_money(tag, text)

    if tag == "ScheduleDesc":
        normalized = re.sub(r"[^A-Za-z0-9]", "", text).upper()
        if not normalized or len(normalized) > TEXT_LIMITS[tag]:
            _raise(tag, text, "expected 1 to 8 letters or numbers")
        return normalized

    if tag == "InsCarrierName" and re.search(
        r"\b(?:and\s+)?affiliates?\b|\b(?:d/?b/?a|a/?k/?a|formerly)\b|[\"“”]",
        text,
        flags=re.IGNORECASE,
    ):
        _raise(tag, text, "use the exact legal carrier name without affiliate or alias text")

    max_length = _text_limit(tag)
    if any(ord(character) < 32 for character in text):
        _raise(tag, text, "control characters are not allowed")
    if len(text) > max_length:
        _raise(tag, text, f"maximum length is {max_length} characters")
    return re.sub(r"\s+", " ", text)


def _tag_is_allowed(form_type: FormType, tag: str) -> bool:
    if form_type == FormType.FORM_5500:
        return tag in FORM_5500_ALLOWED_UPDATE_TAGS
    if form_type == FormType.SCHEDULE_A:
        repeatable = "|".join(sorted(SCHEDULE_A_REPEATABLE_BROKER_TAG_BASES))
        return tag in SCHEDULE_A_ALLOWED_UPDATE_TAGS or bool(
            re.fullmatch(rf"(?:{repeatable})(?:\d+|XX)", tag)
        )
    return False


def _normalize_date(value: str) -> str | None:
    for pattern in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, pattern).strftime("%m/%d/%Y")
        except ValueError:
            continue
    return None


def _normalize_integer(tag: str, value: str) -> str:
    if not re.fullmatch(r"\d+(?:,\d{3})*", value):
        _raise(tag, value, "expected a non-negative whole number")
    return value.replace(",", "")


def _normalize_money(tag: str, value: str) -> str:
    negative_parentheses = value.startswith("(") and value.endswith(")")
    inner = value[1:-1].strip() if negative_parentheses else value
    inner = inner.replace("$", "").strip()
    if not re.fullmatch(r"-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?", inner):
        _raise(tag, value, "expected a numeric amount with at most 2 decimal places")
    normalized = inner.replace(",", "")
    if negative_parentheses:
        normalized = f"-{normalized}"
    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        _raise(tag, value, "expected a valid numeric amount")
    rendered = format(amount, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _is_money_tag(tag: str) -> bool:
    return tag.endswith("Amt") or bool(
        re.fullmatch(r"(?:CommPdAmt|FeesPdAmt)(?:\d+|XX)", tag)
    )


def _text_limit(tag: str) -> int:
    if re.fullmatch(r"Name(?:\d+|XX)", tag):
        return 70
    if re.fullmatch(r"FeesPdText(?:\d+|XX)", tag):
        return 70
    if re.fullmatch(r"Code(?:\d+|XX)", tag):
        return 3
    return TEXT_LIMITS.get(tag, 250)


def _normalized_choice(value: str, choices: dict[str, str]) -> str | None:
    return choices.get(value.strip().lower())


def _raise(tag: str, value: str, reason: str) -> None:
    raise FTWPayloadValidationError([FTWFieldValidationIssue(tag=tag, value=value, reason=reason)])
