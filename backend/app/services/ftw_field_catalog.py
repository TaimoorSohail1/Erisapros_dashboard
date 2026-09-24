from __future__ import annotations

import hashlib
import json
import re

from app.models import FTWFieldCatalogEntry, FormType
from app.services.field_rules import DEFAULT_FIELD_RULES, RETIRED_FIELD_RULE_KEYS
from app.services.ftwilliams_contract import (
    BUSINESS_CODE_TAGS,
    DATE_TAGS,
    EIN_TAGS,
    FTW_CONTRACT_VERSION,
    INTEGER_TAGS,
    NAIC_TAGS,
    ONE_TWO_INDICATOR_TAGS,
    PLAN_NUMBER_TAGS,
    TEXT_LIMITS,
    ZERO_ONE_INDICATOR_TAGS,
)
from app.services.ftwilliams_tags import (
    FORM_5500_CURRENT_TAGS_BY_RULE,
    FORM_5500_TAGS_BY_RULE,
    FORM_5500_UPDATE_TAGS_BY_RULE,
    SCHEDULE_A_CURRENT_TAGS_BY_RULE,
    SCHEDULE_A_TAGS_BY_RULE,
)


SUPPORTED_FTW_YEARS = ("2025", "2026")
DISCOVERED_FTW_YEARS = ("2025",)
READ_ONLY_REASON = (
    "FT Williams exposes this field for comparison but does not accept updates "
    "for the supported form years."
)
DISCOVERED_READ_ONLY_REASON = (
    "FT Williams returned this field in a 2025 current-data query. It is available for extraction "
    "and comparison, but its update tag and validation contract have not been verified."
)

# These comparison-only fields were temporarily exposed in Field Rules from
# FT Williams discovery responses. They are not useful review fields for the
# ERISAPros workflow, so keep them out of the active catalog while preserving
# any saved rule versions for audit history.
_RETIRED_DISCOVERED_TAGS = frozenset(
    {
        (FormType.FORM_5500, "ADMINName"),
        (FormType.SCHEDULE_A, "HealthInd"),
        (FormType.SCHEDULE_A, "InsFailProvideInfoText"),
        (FormType.SCHEDULE_A, "VisionInd"),
    }
)


# Read-only field names observed in successful 2025 ftwLink current-data responses
# for accessible Form 5500 and Schedule A records. Queryability does not imply
# update permission, so entries outside the verified contract remain comparison-only.
_DISCOVERED_FORM_5500_TAGS = """
ADMINAddressLine1 ADMINAddressLine2 ADMINCareOfName ADMINCity ADMINCountry ADMINEIN ADMINName
ADMINPhoneNum ADMINPostalCode ADMINProvinceOrState ADMINState ADMINZipCode AckId AdminForeignCB
AdminNameSameAsPlanSponsInd AdminSignedDate AdminSignedName AdoptedPlanSECUREAct AmendedInd Base5500Type
BenefRcvgBnftCnt BenefitCdSection412Ind BenefitGeneralAssetInd BenefitInsuranceInd BenefitTrustInd
BusinessCode CollectiveBargainInd ComplianceM1FilingRqmtInd ContribEmployersCnt DFVCProgramInd DateInvited
DfeSignedDate DfeSignedName ExtAutomaticInd ExtSpecialInd ExtSpecialText F5500ForeignAdd FilingStatus
FilingStatusAcceptDate FilingStatusFileDate FinalFilingInd FootNotePage1 FootNotePage2
Form5558ApplicationFiledInd FundingCdSection412Ind FundingGeneralAssetInd FundingInsuranceInd FundingTrustInd
InitialFilingInd LastRptPlanName LastRptPlanNum LastRptSponsEIN LastRptSponsName LockedStatus
M1ReceiptConfirmationCode NumSchAAttachedCnt PF5500ForeignAdd PSAddressLine1 PSAddressLine2 PSDCity
PSDCountry PSDPostalCode PSDProvinceOrState PSDState PSDZip PartcpAccountBalCnt PartcpAccountBalCntBoy
PlanEffDate PlanName PlanYearBeginDate PlanYearEndDate PortalSignText1 PortalSignText2 PortalSignText3
PriorYearInd RtdSepPartcpFutCnt RtdSepPartcpRcvgCnt SDAddressLine1 SDAddressLine2 SDCareOfName SDCity
SDCountry SDDbaName SDEIN SDName SDPhoneNum SDPostalCode SDProvinceOrState SDState SDZipCode
SchAAttachedInd SchCAttachedInd SchDAttachedInd SchDCGAttachedCnt SchDCGAttachedInd SchGAttachedInd
SchHAttachedInd SchIAttachedInd SchMBAttachedInd SchMEPAttachedInd SchRAttachedInd SchSBAttachedInd
SepPartcpPartlVstdCnt ShortPlanYrInd SignedStatus SponsDfePlanNum SponsSignedDate SponsSignedName
SubjM1FilingRqmtInd SubtlActRtdSepCnt TopInfoText TotActPartcpBoyCnt TotActRtdSepBenefCnt TotActivePartcpCnt
TotPartcpBoyCnt TypeDFEPlanEntityCd TypePensionBnftCode1 TypePensionBnftCode2 TypePensionBnftCode3
TypePensionBnftCode4 TypePensionBnftCode5 TypePensionBnftCode6 TypePensionBnftCode7 TypePensionBnftCode8
TypePensionBnftCode9 TypePensionBnftCode10 TypePensionBnftCode11 TypePensionBnftCode12 TypePensionBnftCode13
TypePensionBnftCode14 TypePensionBnftCode15 TypePensionBnftCode16 TypePensionBnftCode17 TypePensionBnftCode18
TypePensionBnftCode19 TypePensionBnftCode20 TypePlanEntityCd TypeWelfareBnftCode1 TypeWelfareBnftCode2
TypeWelfareBnftCode3 TypeWelfareBnftCode4 TypeWelfareBnftCode5 TypeWelfareBnftCode6 TypeWelfareBnftCode7
TypeWelfareBnftCode8 TypeWelfareBnftCode9 TypeWelfareBnftCode10 TypeWelfareBnftCode11 TypeWelfareBnftCode12
TypeWelfareBnftCode13 TypeWelfareBnftCode14 TypeWelfareBnftCode15 TypeWelfareBnftCode16 TypeWelfareBnftCode17
TypeWelfareBnftCode18 TypeWelfareBnftCode19 TypeWelfareBnftCode20
""".split()

_DISCOVERED_SCHEDULE_A_TAGS = """
AddressLine101 AddressLine102 AddressLine103 AddressLine104 AddressLine105 AddressLine106 AddressLine107
AddressLine201 AddressLine202 AddressLine203 AddressLine204 AddressLine205 AddressLine206 AddressLine207
AllocContractsGroupInd AllocContractsIndivInd AllocContractsOtherInd AllocContractsOtherText Broker
City01 City02 City03 City04 City05 City06 City07 Code01 Code02 Code03 Code04 Code05 Code06 Code07
CommPdAmt01 CommPdAmt02 CommPdAmt03 CommPdAmt04 CommPdAmt05 CommPdAmt06 CommPdAmt07
Country01 Country02 Country03 Country04 Country05 Country06 Country07 DentalInd EIN
FeesPdAmt01 FeesPdAmt02 FeesPdAmt03 FeesPdAmt04 FeesPdAmt05 FeesPdAmt06 FeesPdAmt07
FeesPdText01 FeesPdText02 FeesPdText03 FeesPdText04 FeesPdText05 FeesPdText06 FeesPdText07
FootNotePage1 FootNotePage3 FootNotePage4 ForeignAddy01 ForeignAddy02 ForeignAddy03 ForeignAddy04
ForeignAddy05 ForeignAddy06 ForeignAddy07 HealthInd HmoInd IndemnityInd InsBrokerCommTotAmt
InsBrokerFeesTotAmt InsCarrierEIN InsCarrierNAICCode InsCarrierName InsContractNum InsFailProvideInfoInd
InsFailProvideInfoText InsPolicyFromDate InsPolicyToDate InsPrsnCoveredEoyCnt LifeInsurInd LongTermDisabInd
Name02 Name03 Name04 Name05 Name06 Name07 Name1 OtherInd Override9 OverrideCommissionsAndFees
PensionAdminChrgAmt PensionBasisRatesText PensionBnftsDsbrsdAmt PensionContractCostAmt PensionContribDepAmt
PensionCostText PensionDistribBnftTermPlnInd PensionDivndCrDepAmt PensionEndPrevBalAmt PensionEoyBalAmt
PensionEoyGenAcctAmt PensionEoySepAcctAmt PensionIntCrDurYrAmt PensionOthDedAmt PensionOthDedText
PensionOtherAmt PensionOtherText PensionPremPaidTotAmt PensionTotAdditionsAmt PensionTotBalAddnAmt
PensionTotDedAmt PensionTransferFromAmt PensionTransferToAmt PensionUnpaidPremiumAmt PlanName PlanNum
PlanSponsorName PlanYearBeginDate PlanYearEndDate PostalCode01 PostalCode02 PostalCode03 PostalCode04
PostalCode05 PostalCode06 PostalCode07 PpoInd PrescriptDrugInd ProvinceOrState01 ProvinceOrState02
ProvinceOrState03 ProvinceOrState04 ProvinceOrState05 ProvinceOrState06 ProvinceOrState07 ScheduleDesc
State01 State02 State03 State04 State05 State06 State07 StopLossInd SupplementUnemployInd TempDisabInd
TopInfoText UnallocContractsDepAdminInd UnallocContractsGuarInvestInd UnallocContractsImmPartGuarInd
UnallocContractsOtherInd UnallocContractsOtherText VisionInd WlfrAcquisCostAmt WlfrAcquisCostText
WlfrClaimsChrgdAmt WlfrClaimsPaidAmt WlfrClaimsReserveAmt WlfrDivndsDueAmt WlfrHeldBnftsAmt
WlfrIncrReserveAmt WlfrIncurredClaimAmt WlfrOthReserveAmt WlfrPremiumRcvdAmt WlfrRefundAmt
WlfrRefundCashInd WlfrRefundCreditInd WlfrReserveAmt WlfrRetAdminAmt WlfrRetChargesAmt
WlfrRetCommissionsAmt WlfrRetOthChrgsAmt WlfrRetOthCostAmt WlfrRetOthExpenseAmt WlfrRetTaxesAmt
WlfrRetTotAmt WlfrTotChargesPaidAmt WlfrTotEarnedPremAmt WlfrTypeBnftOthText WlfrUnpaidDueAmt
ZipCode01 ZipCode02 ZipCode03 ZipCode04 ZipCode05 ZipCode06 ZipCode07
""".split()

_DISCOVERED_LABEL_OVERRIDES = {
    (FormType.SCHEDULE_A, "InsFailProvideInfoText"): "Insurance Carrier Missing Information Explanation",
}


def _form_type(rule_key: str) -> FormType:
    return FormType.SCHEDULE_A if rule_key.startswith("schedule_a_") else FormType.FORM_5500


def _tags(rule_key: str, form_type: FormType) -> tuple[str | None, str | None]:
    if form_type == FormType.SCHEDULE_A:
        update_tag = SCHEDULE_A_TAGS_BY_RULE.get(rule_key)
        return SCHEDULE_A_CURRENT_TAGS_BY_RULE.get(rule_key) or update_tag, update_tag
    current_tag = FORM_5500_CURRENT_TAGS_BY_RULE.get(rule_key) or FORM_5500_TAGS_BY_RULE.get(rule_key)
    return current_tag, FORM_5500_UPDATE_TAGS_BY_RULE.get(rule_key)


def _format_metadata(tag: str | None, field_type: str) -> tuple[str, str]:
    if not tag:
        return "READ_ONLY", "Read from FT Williams; never included in update XML"
    if tag in DATE_TAGS:
        return "DATE", "MM/DD/YYYY"
    if tag in INTEGER_TAGS:
        return "WHOLE_NUMBER", "Non-negative whole number"
    if tag in EIN_TAGS:
        return "EIN", "9 digits; displayed as NN-NNNNNNN"
    if tag in NAIC_TAGS:
        return "NAIC_CODE", "Exactly 5 digits"
    if tag in PLAN_NUMBER_TAGS:
        return "PLAN_NUMBER", "1 to 3 digits; sent as 3 digits"
    if tag in BUSINESS_CODE_TAGS:
        return "BUSINESS_CODE", "Exactly 6 digits"
    if tag in ZERO_ONE_INDICATOR_TAGS:
        return "YES_NO", "FT Williams 1/0 indicator"
    if tag in ONE_TWO_INDICATOR_TAGS:
        return "YES_NO", "FT Williams 1/2 indicator"
    if tag.endswith("Amt") or tag.startswith(("CommPdAmt", "FeesPdAmt")):
        return "MONEY", "Numeric amount with at most 2 decimal places"
    if tag == "ScheduleDesc":
        return "CODE", "1 to 8 letters or numbers"
    if "checkbox" in field_type.casefold() or "select" in field_type.casefold():
        return "CHOICE", "Approved FT Williams option value"
    return "TEXT", f"Text up to {TEXT_LIMITS.get(tag, 250)} characters"


def _tag_slug(tag: str) -> str:
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", tag)
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", separated)
    separated = re.sub(r"(?<=[A-Za-z])(?=\d)", "_", separated)
    return re.sub(r"[^a-z0-9]+", "_", separated.casefold()).strip("_")


def _tag_label(tag: str) -> str:
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", tag)
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", separated)
    separated = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", separated)
    return separated.replace("Amt", "Amount").replace("Ind", "Indicator").strip()


def _discovered_entry(form_type: FormType, tag: str) -> FTWFieldCatalogEntry:
    value_type, _ = _format_metadata(tag, "Dynamic")
    family = "schedule_a" if form_type == FormType.SCHEDULE_A else "form_5500"
    label = _DISCOVERED_LABEL_OVERRIDES.get((form_type, tag), _tag_label(tag))
    return FTWFieldCatalogEntry(
        key=f"ftw_discovered_{family}_{_tag_slug(tag)}",
        label=label,
        form_type=form_type,
        form_section=(
            "Schedule A - Discovered FTW fields"
            if form_type == FormType.SCHEDULE_A
            else "Form 5500 - Discovered FTW fields"
        ),
        supported_years=list(DISCOVERED_FTW_YEARS),
        value_type=value_type,
        format_hint="Current FT Williams value; update format is not yet verified",
        current_tag=tag,
        update_tag=None,
        update_supported=False,
        read_only_reason=DISCOVERED_READ_ONLY_REASON,
        contract_version=FTW_CONTRACT_VERSION,
        catalog_tier="DISCOVERED",
    )


def _build_catalog() -> tuple[FTWFieldCatalogEntry, ...]:
    entries: list[FTWFieldCatalogEntry] = []
    for rule in DEFAULT_FIELD_RULES:
        form_type = _form_type(rule.key)
        current_tag, update_tag = _tags(rule.key, form_type)
        value_type, format_hint = _format_metadata(update_tag or current_tag, rule.field_type)
        entries.append(
            FTWFieldCatalogEntry(
                key=rule.key,
                label=rule.label,
                form_type=form_type,
                form_section=rule.form_section,
                supported_years=list(SUPPORTED_FTW_YEARS),
                value_type=value_type,
                format_hint=format_hint,
                current_tag=current_tag,
                update_tag=update_tag,
                update_supported=bool(update_tag),
                read_only_reason=None if update_tag else READ_ONLY_REASON,
                contract_version=FTW_CONTRACT_VERSION,
                catalog_tier="VERIFIED",
            )
        )
    covered = {(entry.form_type, entry.current_tag) for entry in entries if entry.current_tag}
    for form_type, tags in (
        (FormType.FORM_5500, _DISCOVERED_FORM_5500_TAGS),
        (FormType.SCHEDULE_A, _DISCOVERED_SCHEDULE_A_TAGS),
    ):
        for tag in tags:
            if (form_type, tag) in _RETIRED_DISCOVERED_TAGS:
                continue
            if (form_type, tag) not in covered:
                entries.append(_discovered_entry(form_type, tag))
    return tuple(entries)


_FIELD_CATALOG = _build_catalog()
_FIELD_CATALOG_BY_KEY = {entry.key: entry for entry in _FIELD_CATALOG}


def field_catalog() -> tuple[FTWFieldCatalogEntry, ...]:
    return _FIELD_CATALOG


def field_catalog_entry(rule_key: str) -> FTWFieldCatalogEntry | None:
    return _FIELD_CATALOG_BY_KEY.get(rule_key)


def field_catalog_version() -> str:
    payload = [entry.model_dump(mode="json") for entry in _FIELD_CATALOG]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return f"{FTW_CONTRACT_VERSION}-{digest}"
