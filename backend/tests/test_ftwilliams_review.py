import asyncio
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.repositories as repositories
from app.config import get_settings
from app.models import (
    DocumentType,
    ExtractedField,
    ExtractedFieldStatus,
    FieldPriority,
    Filing,
    FilingStatus,
    FormType,
    FTWilliamsBrokerMatchDecision,
    FTWilliamsBrokerMatchesRequest,
    FTWilliamsScheduleABrokerRowsRequest,
    FTWilliamsManualMatchRequest,
    FTWilliamsComparisonField,
    FTWilliamsPlanLookup,
    FTWilliamsPlanLookupStatus,
    FTWilliamsQueryResponse,
    FTWilliamsReview,
    FTWilliamsReviewStatus,
    FTWilliamsScheduleAMatchRequest,
    FTWilliamsStatusItem,
    ScheduleABrokerRow,
    ScheduleAContractType,
)
from app.services.ftwilliams import FTWilliamsService
from app.services.ftwilliams_review import FTWilliamsReviewService, clear_ftw_current_snapshot_cache
from app.services.ftwilliams_tags import (
    FORM_5500_UPDATE_TAGS_BY_RULE,
    resolve_ftw_tag,
    resolve_ftw_update_tag,
    values_meaningfully_different,
)
from app.services.xml_builder import build_proposed_ftw_xml, build_single_document_update_xml


def run_async(coro):
    return asyncio.run(coro)


def sample_filing() -> Filing:
    return Filing(
        file_name="2025 Filing SPY",
        content_type="application/vnd.erisapros.filing-package",
        file_size=100,
        document_type=DocumentType.SCHEDULE_A,
        s3_key="sharefile-package/test",
        intake_source="SHAREFILE",
    )


class FakeFTWilliamsService(FTWilliamsService):
    def __init__(self):
        self.calls = []

    def status(self) -> dict:
        return {"configured": True}

    async def run_query(self, payload):
        self.calls.append(payload)
        request_xml = self.mask_key_id(self.build_request_xml(payload))
        if payload.operation == "query_plan":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="Plan",
                        error_code="0",
                        plan_id=payload.plan_id,
                        ftw_customer_id="782023768",
                        ftw_plan_id="959357188",
                        query_results={
                            "PlanNumber": "501",
                            "PlanLine1": "Crest Discount Foods, Inc. Flexible Benefits Plan",
                        },
                    )
                ],
            )
        if payload.operation == "query_5500":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="5500",
                        error_code="0",
                        ftw_customer_id=payload.ftw_customer_id,
                        ftw_plan_id=payload.ftw_plan_id,
                        query_results={
                            "PlanName": "Crest Discount Foods, Inc. Flexible Benefits Plan",
                            "SDEIN": "73-0759701",
                        },
                    )
                ],
            )
        if payload.operation == "query_schedule_a":
            if payload.ftw_seq_no == "2":
                return FTWilliamsQueryResponse(
                    operation=payload.operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    success=True,
                    raw_response="<ftwLinkResponse />",
                    statuses=[
                        FTWilliamsStatusItem(
                            type="ScheduleA",
                            error_code="0",
                            ftw_customer_id=payload.ftw_customer_id,
                            ftw_plan_id=payload.ftw_plan_id,
                            ftw_seq_no=payload.ftw_seq_no,
                            query_results={
                                "ScheduleDesc": "BCBS-O",
                            "InsCarrierName": "BlueCross BlueShield of Oklahoma",
                            "InsCarrierEIN": "36-1236610",
                            "InsContractNum": "Y00979",
                            },
                        )
                    ],
                )
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=False,
                raw_response="<ftwLinkResponse />",
                statuses=[FTWilliamsStatusItem(type="DOLScheduleAData", error_code="59", error_desc="Could not locate form")],
            )
        raise AssertionError(f"Unexpected FT Williams operation: {payload.operation}")


class FakeFTWilliamsFallbackService(FTWilliamsService):
    def __init__(self):
        self.calls = []
        self.current_year_available = False

    def status(self) -> dict:
        return {"configured": True}

    async def run_query(self, payload):
        self.calls.append(payload)
        request_xml = self.mask_key_id(self.build_request_xml(payload))
        if payload.operation == "query_plan":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="Plan",
                        error_code="0",
                        plan_id=payload.plan_id,
                        ftw_customer_id="900000001",
                        ftw_plan_id="900000002",
                        query_results={
                            "PlanNumber": "501",
                            "PlanLine1": "Midwest Hose & Specialty Health and Welfare Benefits Plan",
                        },
                    )
                ],
            )
        if payload.operation == "query_5500":
            if payload.year == "2024" and not self.current_year_available:
                return FTWilliamsQueryResponse(
                    operation=payload.operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    success=False,
                    raw_response="<ftwLinkResponse />",
                    statuses=[FTWilliamsStatusItem(type="DOL5500Data", error_code="59", error_desc="Could not locate form")],
                )
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="5500",
                        error_code="0",
                        ftw_customer_id="900000001",
                        ftw_plan_id="900000002",
                        query_results={
                            "PLAN_NAME0": "Midwest Hose & Specialty Health and Welfare Benefits Plan",
                            "TotPartcpBoyCnt": "999",
                            "PlanYearBeginDate": f"01/01/{payload.year}",
                            "PlanYearEndDate": f"12/31/{payload.year}",
                        },
                    )
                ],
            )
        if payload.operation == "query_schedule_a":
            if payload.year == "2024" and not self.current_year_available:
                return FTWilliamsQueryResponse(
                    operation=payload.operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    success=False,
                    raw_response="<ftwLinkResponse />",
                    statuses=[FTWilliamsStatusItem(type="DOLScheduleAData", error_code="59", error_desc="Could not locate form")],
                )
            if payload.ftw_seq_no == "4":
                return FTWilliamsQueryResponse(
                    operation=payload.operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    success=True,
                    raw_response="<ftwLinkResponse />",
                    statuses=[
                        FTWilliamsStatusItem(
                            type="ScheduleA",
                            error_code="0",
                            ftw_customer_id="900000001",
                            ftw_plan_id="900000002",
                            ftw_seq_no=payload.ftw_seq_no,
                            query_results={
                                "InsCarrierName": "Medical Mutual",
                                "InsCarrierEIN": "73-0000001",
                                "InsContractNum": "MED-4455",
                                "InsPrsnCoveredEoyCnt": "888",
                                "InsPolicyFromDate": f"01/01/{payload.year}",
                                "InsPolicyToDate": f"12/31/{payload.year}",
                                "CommPdAmt01": "111111",
                                "FeesPdAmt01": "222222",
                                "WlfrPremiumRcvdAmt": "333333",
                                "WlfrClaimsPaidAmt": "444444",
                                "WlfrClaimsReserveAmt": "555555",
                                "WlfrTotChargesPaidAmt": "666666",
                            },
                        )
                    ],
                )
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=False,
                raw_response="<ftwLinkResponse />",
                statuses=[FTWilliamsStatusItem(type="DOLScheduleAData", error_code="59", error_desc="Could not locate form")],
            )
        raise AssertionError(f"Unexpected FT Williams operation: {payload.operation}")


class FakeFTWilliamsWeakScheduleService(FTWilliamsService):
    def __init__(self):
        self.calls = []

    def status(self) -> dict:
        return {"configured": True}

    async def run_query(self, payload):
        self.calls.append(payload)
        request_xml = self.mask_key_id(self.build_request_xml(payload))
        if payload.operation == "query_plan":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="Plan",
                        error_code="0",
                        plan_id=payload.plan_id,
                        ftw_customer_id="1678923231",
                        ftw_plan_id="2025984898",
                        query_results={"PlanNumber": "501", "PlanLine1": "Community Legal Aid Socal Wrap Benefit Plan"},
                    )
                ],
            )
        if payload.operation == "query_5500":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[FTWilliamsStatusItem(type="5500", error_code="0", query_results={"PLAN_NAME0": "Community Legal Aid Socal Wrap Benefit Plan"})],
            )
        if payload.operation == "query_schedule_a" and payload.ftw_seq_no == "4":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="ScheduleA",
                        error_code="0",
                        ftw_seq_no="4",
                        query_results={"ScheduleDesc": "4-1"},
                    )
                ],
            )
        if payload.operation == "query_schedule_a" and not payload.ftw_seq_no:
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="ScheduleA",
                        error_code="0",
                        ftw_seq_no="4",
                        query_results={
                            "ScheduleDesc": "4-1",
                            "InsCarrierName": "Metropolitan Life Insurance Company",
                            "InsCarrierEIN": "13-5581829",
                            "InsContractNum": "5393054",
                        },
                    )
                ],
            )
        if payload.operation == "query_schedule_a":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=False,
                raw_response="<ftwLinkResponse />",
                statuses=[FTWilliamsStatusItem(type="DOLScheduleAData", error_code="59", error_desc="Could not locate form")],
            )
        raise AssertionError(f"Unexpected FT Williams operation: {payload.operation}")


class FakeFTWilliamsCurrentTagService(FTWilliamsService):
    def __init__(self):
        self.calls = []

    def status(self) -> dict:
        return {"configured": True}

    async def run_query(self, payload):
        self.calls.append(payload)
        request_xml = self.mask_key_id(self.build_request_xml(payload))
        if payload.operation == "query_plan":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="Plan",
                        error_code="0",
                        plan_id=payload.plan_id,
                        ftw_customer_id="571475632",
                        ftw_plan_id="682436783",
                        query_results={"PlanNumber": "501"},
                    )
                ],
            )
        if payload.operation == "query_5500":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="5500",
                        error_code="0",
                        ftw_customer_id="571475632",
                        ftw_plan_id="682436783",
                        query_results={
                            "PlanName": "Midwest Hose Plan",
                            "SponsDfePlanNum": "501",
                            "PlanEffDate": "10/01/2011",
                            "PlanYearEndDate": "09/30/2025",
                            "SDEIN": "73-1185740",
                            "SDAddressLine1": "750 E MAIN ST",
                            "SDCity": "STAMFORD",
                            "SDState": "CT",
                            "SDZipCode": "06902-3831",
                            "TotPartcpBoyCnt": "315",
                            "SubtlActRtdSepCnt": "298",
                            "TotActPartcpBoyCnt": "249",
                            "TotActivePartcpCnt": "279",
                            "SchAAttachedInd": "1",
                            "BenefitInsuranceInd": "1",
                            "FundingInsuranceInd": "1",
                        },
                    )
                ],
            )
        if payload.operation == "query_schedule_a":
            if payload.ftw_seq_no == "1":
                return FTWilliamsQueryResponse(
                    operation=payload.operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    success=True,
                    raw_response="<ftwLinkResponse />",
                    statuses=[
                        FTWilliamsStatusItem(
                            type="ScheduleA",
                            error_code="0",
                            ftw_customer_id="571475632",
                            ftw_plan_id="682436783",
                            ftw_seq_no="1",
                            query_results={
                                "InsCarrierName": "UnitedHealthcare",
                                "InsCarrierEIN": "36-2739571",
                                "InsCarrierNAICCode": "98765",
                                "InsContractNum": "1246876",
                                "InsFailProvideInfoInd": "2",
                                "Name1": "NFP LLC",
                                "CommPdAmt01": "111893",
                                "FeesPdText01": "COMMISSIONS AND FEES",
                            },
                        )
                    ],
                )
            if payload.ftw_seq_no == "3":
                return FTWilliamsQueryResponse(
                    operation=payload.operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    success=True,
                    raw_response="<ftwLinkResponse />",
                    statuses=[
                        FTWilliamsStatusItem(
                            type="ScheduleA",
                            error_code="0",
                            ftw_customer_id="571475632",
                            ftw_plan_id="682436783",
                            ftw_seq_no="3",
                            query_results={
                                "InsCarrierName": "Other Carrier",
                                "InsCarrierEIN": "11-1111111",
                                "InsContractNum": "OTHER-3",
                            },
                        )
                    ],
                )
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=False,
                raw_response="<ftwLinkResponse />",
                statuses=[FTWilliamsStatusItem(type="ScheduleA", error_code="59", error_desc="Could not locate form")],
            )
        raise AssertionError(f"Unexpected FT Williams operation: {payload.operation}")


class FakeFTWilliamsScheduleOnlyService(FTWilliamsService):
    def status(self) -> dict:
        return {"configured": True}

    async def run_query(self, payload):
        request_xml = self.mask_key_id(self.build_request_xml(payload))
        if payload.operation == "query_plan":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="Plan",
                        error_code="0",
                        plan_id=payload.plan_id,
                        ftw_customer_id="571475632",
                        ftw_plan_id="682436783",
                        query_results={"PlanNumber": "501"},
                    )
                ],
            )
        if payload.operation == "query_5500":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=False,
                raw_response="<ftwLinkResponse />",
                statuses=[FTWilliamsStatusItem(type="DOL5500Data", error_code="59", error_desc="Could not locate form")],
            )
        if payload.operation == "query_schedule_a":
            if payload.ftw_seq_no == "1":
                return FTWilliamsQueryResponse(
                    operation=payload.operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    success=True,
                    raw_response="<ftwLinkResponse />",
                    statuses=[
                        FTWilliamsStatusItem(
                            type="ScheduleA",
                            error_code="0",
                            ftw_customer_id="571475632",
                            ftw_plan_id="682436783",
                            ftw_seq_no="1",
                            query_results={
                                "InsCarrierName": "UnitedHealthcare Insurance Company",
                                "InsContractNum": "1246876",
                                "Name1": "Old Broker Name",
                            },
                        )
                    ],
                )
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=False,
                raw_response="<ftwLinkResponse />",
                statuses=[FTWilliamsStatusItem(type="ScheduleA", error_code="59", error_desc="Could not locate form")],
            )
        raise AssertionError(f"Unexpected FT Williams operation: {payload.operation}")


class FakeFTWilliamsEquitableMismatchService(FTWilliamsService):
    def status(self) -> dict:
        return {"configured": True}

    async def run_query(self, payload):
        request_xml = self.mask_key_id(self.build_request_xml(payload))
        if payload.operation == "query_plan":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="Plan",
                        error_code="0",
                        plan_id=payload.plan_id,
                        ftw_customer_id="1030568255",
                        ftw_plan_id="1275899341",
                        query_results={"PlanNumber": "501"},
                    )
                ],
            )
        if payload.operation == "query_5500":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="5500",
                        error_code="0",
                        ftw_customer_id="1030568255",
                        ftw_plan_id="1275899341",
                        query_results={
                            "PlanName": "HAROLD BROTHERS MECHANICAL CONTRACTORS INC. LIFE AND DISABILITY PLAN",
                            "SponsDfePlanNum": "501",
                            "SDEIN": "26-3189470",
                            "PlanYearBeginDate": "01/01/2024",
                            "PlanYearEndDate": "09/30/2025",
                        },
                    )
                ],
            )
        if payload.operation == "query_schedule_a":
            if payload.ftw_seq_no == "1":
                return FTWilliamsQueryResponse(
                    operation=payload.operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    success=True,
                    raw_response="<ftwLinkResponse />",
                    statuses=[
                        FTWilliamsStatusItem(
                            type="ScheduleA",
                            error_code="0",
                            ftw_customer_id="1030568255",
                            ftw_plan_id="1275899341",
                            ftw_seq_no="1",
                            query_results={
                                "ScheduleDesc": "1-1",
                                "PlanYearBeginDate": "01/01/2024",
                                "PlanYearEndDate": "09/30/2024",
                                "PlanName": "HAROLD BROTHERS MECHANICAL CONTRACTORS INC. LIFE AND DISABILITY PLAN",
                                "PlanNum": "501",
                                "PlanSponsorName": "HAROLD BROTHERS MECHANICAL CONTRACTORS INC.",
                                "EIN": "26-3189470",
                                "InsCarrierName": "EQUITABLE FINANCIAL LIFE INSURANCE COMPANY OF AMERICA",
                                "InsCarrierEIN": "86-0222062",
                                "InsCarrierNAICCode": "78077",
                                "InsContractNum": "011335",
                                "InsPrsnCoveredEoyCnt": "279",
                                "InsPolicyFromDate": "01/01/2024",
                                "InsPolicyToDate": "09/30/2024",
                                "Name1": "COMPREHENSIVE BENEFITS ADMINISTATOR",
                                "CommPdAmt01": "4297",
                                "FeesPdAmt01": "0",
                                "FeesPdText01": "COMMISSIONS ",
                                "Code01": "3",
                                "WlfrTotChargesPaidAmt": "35560",
                            },
                        )
                    ],
                )
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=False,
                raw_response="<ftwLinkResponse />",
                statuses=[FTWilliamsStatusItem(type="ScheduleA", error_code="59", error_desc="Could not locate form")],
            )
        raise AssertionError(f"Unexpected FT Williams operation: {payload.operation}")


class FakeFTWilliamsPlanNotFoundService(FTWilliamsService):
    def __init__(self):
        self.calls = []

    def status(self) -> dict:
        return {"configured": True}

    async def run_query(self, payload):
        self.calls.append(payload)
        request_xml = self.mask_key_id(self.build_request_xml(payload))
        if payload.operation == "query_plan":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=False,
                raw_response="<ftwLinkResponse />",
                statuses=[FTWilliamsStatusItem(type="PlanData", error_code="56", error_desc="Could not locate existing plan.")],
            )
        if payload.operation == "archive_5500_get_data":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=False,
                raw_response="<ftwLinkResponse />",
                statuses=[FTWilliamsStatusItem(type="Archive5500", error_code="56", error_desc="Could not locate existing plan.")],
            )
        if payload.operation == "plan_ids_batch":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse><Status><Type>PlanIDs_Batch</Type><ErrorCode>0</ErrorCode></Status></ftwLinkResponse>",
                statuses=[FTWilliamsStatusItem(type="PlanIDs_Batch", error_code="0")],
            )
        raise AssertionError(f"Current-data query should not run after failed plan lookup: {payload.operation}")


class FakeFTWilliamsArchivePlanLookupService(FTWilliamsService):
    def __init__(self):
        self.calls = []

    def status(self) -> dict:
        return {"configured": True}

    async def run_query(self, payload):
        self.calls.append(payload)
        request_xml = self.mask_key_id(self.build_request_xml(payload))
        if payload.operation == "query_plan":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=False,
                raw_response="<ftwLinkResponse />",
                statuses=[FTWilliamsStatusItem(type="PlanData", error_code="18", error_desc="Could not locate existing plan")],
            )
        if payload.operation == "archive_5500_get_data":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="""<?xml version="1.0" encoding="UTF-8" ?>
<ftwLinkResponse>
  <Status>
    <Type>Archive5500</Type>
    <ErrorCode>0</ErrorCode>
    <QueryResults>
      <CompanyName>Camino Health Center</CompanyName>
      <PlanLine1>CAMINO HEALTH CENTER WRAP BENEFIT PLAN</PlanLine1>
      <CompanyEmployerID>33-0574214</CompanyEmployerID>
      <PlanNumber>501</PlanNumber>
      <FTWCustomerID>111222333</FTWCustomerID>
      <FTWPlanID>444555666</FTWPlanID>
    </QueryResults>
  </Status>
</ftwLinkResponse>""",
                statuses=[FTWilliamsStatusItem(type="Archive5500", error_code="0")],
            )
        if payload.operation == "query_5500":
            self._assert_ftw_ids(payload)
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="DOL5500",
                        error_code="0",
                        ftw_customer_id="111222333",
                        ftw_plan_id="444555666",
                        query_results={"PLAN_NAME0": "CAMINO HEALTH CENTER WRAP BENEFIT PLAN"},
                    )
                ],
            )
        if payload.operation == "query_schedule_a":
            self._assert_ftw_ids(payload)
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=False,
                raw_response="<ftwLinkResponse />",
                statuses=[FTWilliamsStatusItem(type="DOLScheduleAData", error_code="59", error_desc="Could not locate form")],
            )
        raise AssertionError(f"Unexpected FT Williams operation: {payload.operation}")

    def _assert_ftw_ids(self, payload):
        assert payload.ftw_customer_id == "111222333"
        assert payload.ftw_plan_id == "444555666"


class FakeFTWilliamsArchiveNameLookupService(FTWilliamsService):
    def __init__(self):
        self.calls = []

    def status(self) -> dict:
        return {"configured": True}

    async def run_query(self, payload):
        self.calls.append(payload)
        request_xml = self.mask_key_id(self.build_request_xml(payload))
        if payload.operation == "query_plan":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=False,
                raw_response="<ftwLinkResponse />",
                statuses=[FTWilliamsStatusItem(type="PlanData", error_code="18", error_desc="Company ID '04-2382160' not valid.")],
            )
        if payload.operation == "archive_5500_get_data":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=False,
                raw_response="<ftwLinkResponse />",
                statuses=[FTWilliamsStatusItem(type="Archive5500", error_code="18", error_desc="Archive5500 lookup did not find a matching plan.")],
            )
        if payload.operation == "archive_5500_ein_lookup":
            if payload.company_name != "Worcester Community Action Council, Inc." or payload.company_state != "MA":
                return FTWilliamsQueryResponse(
                    operation=payload.operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    success=True,
                    raw_response="""<?xml version="1.0" encoding="UTF-8" ?>
<ftwLinkResponse>
  <Status>
    <Type>Archive5500</Type>
    <ErrorCode>0</ErrorCode>
  </Status>
</ftwLinkResponse>""",
                    statuses=[FTWilliamsStatusItem(type="Archive5500", error_code="0")],
                )
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="""<?xml version="1.0" encoding="UTF-8" ?>
<ftwLinkResponse>
  <Status>
    <Type>Archive5500</Type>
    <ErrorCode>0</ErrorCode>
    <QueryResults>
      <CompanyName>Worcester Community Action</CompanyName>
      <PlanLine1>WORCESTER COMMUNITY ACTION HEALTH &amp; WELFARE BENEFIT PLAN</PlanLine1>
      <CompanyEmployerID>04-2382160</CompanyEmployerID>
      <PlanNumber>501</PlanNumber>
      <FTWCustomerID>222333444</FTWCustomerID>
      <FTWPlanID>555666777</FTWPlanID>
    </QueryResults>
  </Status>
</ftwLinkResponse>""",
                statuses=[FTWilliamsStatusItem(type="Archive5500", error_code="0")],
            )
        if payload.operation == "query_5500":
            self._assert_ftw_ids(payload)
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="DOL5500",
                        error_code="0",
                        ftw_customer_id="222333444",
                        ftw_plan_id="555666777",
                        query_results={"PLAN_NAME0": "WORCESTER COMMUNITY ACTION HEALTH & WELFARE BENEFIT PLAN"},
                    )
                ],
            )
        if payload.operation == "query_schedule_a":
            self._assert_ftw_ids(payload)
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=False,
                raw_response="<ftwLinkResponse />",
                statuses=[FTWilliamsStatusItem(type="DOLScheduleAData", error_code="59", error_desc="Could not locate form")],
            )
        raise AssertionError(f"Unexpected FT Williams operation: {payload.operation}")

    def _assert_ftw_ids(self, payload):
        assert payload.ftw_customer_id == "222333444"
        assert payload.ftw_plan_id == "555666777"


class FakeFTWilliamsSameCustomerPlanLookupService(FTWilliamsService):
    def __init__(self):
        self.calls = []

    def status(self) -> dict:
        return {"configured": True}

    async def run_query(self, payload):
        self.calls.append(payload)
        request_xml = self.mask_key_id(self.build_request_xml(payload))
        if payload.operation == "query_plan" and payload.plan_id == "33-0574214501":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=False,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="Plan",
                        error_code="18",
                        error_desc="Could not locate existing plan for Customer:33-0574214501 ftw:",
                        customer_id="33-0574214",
                        plan_id="33-0574214501",
                    )
                ],
            )
        if payload.operation == "query_plan" and payload.plan_id == "33-0574214":
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="Plan",
                        error_code="0",
                        plan_id="33-0574214",
                        ftw_customer_id="1280119512",
                        ftw_plan_id="1559780665",
                        query_results={
                            "PlanNumber": "501",
                            "PlanLine1": "CAMINO HEALTH CENTER WRAP BENEFIT PLAN",
                            "PlanYearEnd": "11-30",
                        },
                    )
                ],
            )
        if payload.operation == "query_5500":
            self._assert_ftw_ids(payload)
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=True,
                raw_response="<ftwLinkResponse />",
                statuses=[
                    FTWilliamsStatusItem(
                        type="5500",
                        error_code="0",
                        ftw_customer_id="1280119512",
                        ftw_plan_id="1559780665",
                        query_results={
                            "PlanName": "CAMINO HEALTH CENTER WRAP BENEFIT PLAN",
                            "SponsDfePlanNum": "501",
                            "SDEIN": "33-0574214",
                            "TotPartcpBoyCnt": "117",
                        },
                    )
                ],
            )
        if payload.operation == "query_schedule_a":
            self._assert_ftw_ids(payload)
            if payload.ftw_seq_no == "3":
                return FTWilliamsQueryResponse(
                    operation=payload.operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    success=True,
                    raw_response="<ftwLinkResponse />",
                    statuses=[
                        FTWilliamsStatusItem(
                            type="ScheduleA",
                            error_code="0",
                            ftw_customer_id="1280119512",
                            ftw_plan_id="1559780665",
                            ftw_seq_no="3",
                            query_results={
                                "InsCarrierName": "Anthem Blue Cross",
                                "InsCarrierEIN": "95-3761285",
                                "InsContractNum": "ANTHEM-1",
                            },
                        )
                    ],
                )
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=request_xml,
                success=False,
                raw_response="<ftwLinkResponse />",
                statuses=[FTWilliamsStatusItem(type="DOLScheduleAData", error_code="59", error_desc="Could not locate form")],
            )
        raise AssertionError(f"Unexpected FT Williams operation: {payload.operation}")

    def _assert_ftw_ids(self, payload):
        assert payload.ftw_customer_id == "1280119512"
        assert payload.ftw_plan_id == "1559780665"


class FTWilliamsReviewFlowTests(unittest.TestCase):
    def test_plan_matching_rejects_a_different_plan_year_even_when_ein_and_plan_number_match(self):
        service = FTWilliamsReviewService()
        lookup = FTWilliamsPlanLookup(
            company_employer_id="12-3456789",
            plan_number="501",
            year="2025",
            plan_name="Acme Health and Welfare Plan",
            company_name_candidates=["Acme Corporation"],
        )
        matching = {
            "CompanyEmployerID": "12-3456789",
            "PlanNumber": "501",
            "PlanYear": "2025",
            "PlanLine1": "Acme Health and Welfare Plan",
            "CompanyName": "Acme Corporation",
        }
        wrong_year = {**matching, "PlanYear": "2024"}

        self.assertGreaterEqual(service._plan_lookup_score(matching, lookup), 10)
        self.assertLess(service._plan_lookup_score(wrong_year, lookup), 0)
        self.assertEqual(service._plan_lookup_matches([wrong_year], lookup), [])

    def test_plan_ids_batch_probes_only_a_bounded_filtered_candidate_set(self):
        class LargePlanBatchFTWilliamsService(FTWilliamsService):
            def __init__(self):
                self.calls = []

            def status(self) -> dict:
                return {"configured": True}

            def build_request_xml(self, payload) -> str:
                return f"<Request operation=\"{payload.operation}\" />"

            def mask_key_id(self, value: str) -> str:
                return value

            async def run_query(self, payload):
                self.calls.append(payload)
                if payload.operation == "plan_ids_batch":
                    statuses = []
                    for index in range(3352):
                        prefix = "TARGET" if index < 20 else "UNRELATED"
                        statuses.append(
                            f"<Status><Type>PlanIDs_Batch</Type><ErrorCode>0</ErrorCode>"
                            f"<CustomerID>{prefix}-COMPANY-{index}</CustomerID>"
                            f"<PlanID>{prefix}-PLAN-{index}</PlanID>"
                            f"<FTWCustomerID>{100000 + index}</FTWCustomerID>"
                            f"<FTWPlanID>{200000 + index}</FTWPlanID></Status>"
                        )
                    raw_response = f"<ftwLinkResponse>{''.join(statuses)}</ftwLinkResponse>"
                    return FTWilliamsQueryResponse(
                        operation=payload.operation,
                        configured=True,
                        sent=True,
                        request_xml="<Request operation=\"plan_ids_batch\" />",
                        success=True,
                        raw_response=raw_response,
                        statuses=self.parse_response(raw_response),
                    )
                if payload.operation == "query_plan":
                    return FTWilliamsQueryResponse(
                        operation=payload.operation,
                        configured=True,
                        sent=True,
                        request_xml="<Request operation=\"query_plan\" />",
                        success=False,
                        raw_response="<ftwLinkResponse />",
                        statuses=[FTWilliamsStatusItem(type="PlanData", error_code="18", error_desc="No match")],
                    )
                raise AssertionError(f"Unexpected operation {payload.operation}")

        fake_ftw = LargePlanBatchFTWilliamsService()
        service = FTWilliamsReviewService(fake_ftw)
        lookup = FTWilliamsPlanLookup(
            company_employer_id="13-1994506",
            plan_number="503",
            plan_name="TARGET HEALTH AND WELFARE PLAN",
            sponsor_name="TARGET AMERICAS LTD",
        )

        error = run_async(service._try_plan_ids_batch_lookup(lookup, repo=None))
        probes = [call for call in fake_ftw.calls if call.operation == "query_plan"]

        self.assertLessEqual(len(probes), 10)
        self.assertIn("manual", error.lower())

    def test_zero_one_schedule_a_indicators_compare_to_yes_no_values(self) -> None:
        self.assertFalse(values_meaningfully_different("1", "Yes", tag="HealthInd"))
        self.assertFalse(values_meaningfully_different("0", "No", tag="VisionInd"))
        self.assertTrue(values_meaningfully_different("0", "Yes", tag="HealthInd"))

    def test_equivalent_structured_addresses_do_not_create_a_false_update(self) -> None:
        self.assertFalse(
            values_meaningfully_different(
                "815 2ND AVENUE, 9TH FLOOR, NEW YORK NY 10017-4503",
                "815 2ND AVENUE 9TH FLOOR NEW YORK NY 100174503",
                tag="SPONS_DFE_MAIL_STR_ADDRESS",
            )
        )
        self.assertTrue(
            values_meaningfully_different(
                "815 2ND AVENUE, NEW YORK NY 10017-4503",
                "915 2ND AVENUE, NEW YORK NY 10017-4503",
                tag="SPONS_DFE_MAIL_STR_ADDRESS",
            )
        )

    def test_schedule_a_readback_accepts_ftw_whole_dollar_normalization(self) -> None:
        service = FTWilliamsReviewService()

        normalized = service._compare_readback_document(
            FormType.SCHEDULE_A,
            {"WlfrTotChargesPaidAmt": "53977.12"},
            {"WlfrTotChargesPaidAmt": "53977"},
        )
        rounded_half_dollar = service._compare_readback_document(
            FormType.SCHEDULE_A,
            {"WlfrTotChargesPaidAmt": "497.50"},
            {"WlfrTotChargesPaidAmt": "498"},
        )
        materially_different = service._compare_readback_document(
            FormType.SCHEDULE_A,
            {"WlfrTotChargesPaidAmt": "53978"},
            {"WlfrTotChargesPaidAmt": "53977"},
        )

        self.assertEqual(normalized, [])
        self.assertEqual(rounded_half_dollar, [])
        self.assertEqual(materially_different[0]["tag"], "WlfrTotChargesPaidAmt")

    def test_schedule_a_readback_verifies_broker_multipart_rows(self) -> None:
        service = FTWilliamsReviewService()
        expected = {
            "InsCarrierName": "Cigna",
            "__subparts__": {
                "Broker": [
                    {"NameXX": "First Broker", "CommPdAmtXX": "250"},
                    {"NameXX": "Second Broker", "FeesPdAmtXX": "200"},
                ]
            },
        }
        actual = {
            "InsCarrierName": "Cigna",
            "Name1": "First Broker",
            "CommPdAmt01": "250",
            "Name02": "Second Broker",
            "FeesPdAmt02": "200",
        }
        actual_subparts = {
            "Broker": [
                {"Name1": "First Broker", "CommPdAmt01": "250"},
                {"Name02": "Second Broker", "FeesPdAmt02": "200"},
            ]
        }

        matches = service._compare_readback_document(
            FormType.SCHEDULE_A,
            expected,
            actual,
            actual_subparts=actual_subparts,
        )
        mismatches = service._compare_readback_document(
            FormType.SCHEDULE_A,
            expected,
            actual,
            actual_subparts={
                "Broker": [
                    {"Name1": "First Broker", "CommPdAmt01": "999"},
                    {"Name02": "Second Broker", "FeesPdAmt02": "200"},
                ]
            },
        )

        self.assertEqual(matches, [])
        self.assertTrue(any(item["tag"] == "Broker[1]/CommPdAmtXX" for item in mismatches))

    def test_schedule_a_readback_matches_brokers_by_identity_when_ftw_reorders_rows(self) -> None:
        service = FTWilliamsReviewService()
        expected = {
            "InsCarrierName": "Cigna",
            "__subparts__": {
                "Broker": [
                    {"NameXX": "First Broker", "AddressLine1XX": "100 Main St", "CommPdAmtXX": "250"},
                    {"NameXX": "Second Broker", "AddressLine1XX": "200 Oak St", "FeesPdAmtXX": "200"},
                ]
            },
        }

        mismatches = service._compare_readback_document(
            FormType.SCHEDULE_A,
            expected,
            {"InsCarrierName": "Cigna"},
            actual_subparts={
                "Broker": [
                    {"Name1": "Second Broker", "AddressLine101": "200 Oak St", "FeesPdAmt01": "200"},
                    {"Name02": "First Broker", "AddressLine102": "100 Main St", "CommPdAmt02": "250"},
                ]
            },
        )

        self.assertEqual(mismatches, [])

    def test_schedule_a_readback_uses_combined_identity_for_duplicate_broker_names_and_addresses(self) -> None:
        service = FTWilliamsReviewService()
        expected = {
            "__subparts__": {
                "Broker": [
                    {
                        "NameXX": "RSC Insurance Brokerage",
                        "AddressLine1XX": "160 Federal St Fl 2",
                    },
                    {
                        "NameXX": "RSC Insurance Brokerage Inc",
                        "AddressLine1XX": "485 Lexington Ave",
                    },
                    {
                        "NameXX": "RSC Insurance Brokerage Inc",
                        "AddressLine1XX": "160 Federal St Fl 2",
                        "CityXX": "Boston",
                        "CommPdAmtXX": "196",
                    }
                ]
            }
        }

        mismatches = service._compare_readback_document(
            FormType.SCHEDULE_A,
            expected,
            {},
            actual_subparts={
                "Broker": [
                    {"Name1": "RSC Insurance Brokerage", "AddressLine101": "160 Federal St Fl 2"},
                    {"Name02": "RSC Insurance Brokerage Inc", "AddressLine102": "485 Lexington Ave"},
                    {
                        "Name03": "RSC Insurance Brokerage Inc",
                        "AddressLine103": "160 Federal St Fl 2",
                        "City03": "Boston",
                        "CommPdAmt03": "196",
                    },
                ]
            },
        )

        self.assertEqual(mismatches, [])

    def test_form_5500_only_update_does_not_require_schedule_a_payload(self) -> None:
        service = FTWilliamsReviewService()
        review = FTWilliamsReview(
            filing_id="filing-1",
            schedule_a_candidates=[{"ftw_seq_no": "1"}],
            fields=[
                FTWilliamsComparisonField(
                    label="Active participants",
                    form_type=FormType.FORM_5500,
                    ftw_tag="TotActivePartcpCnt",
                    proposed_value="100",
                    changed=True,
                    update_included=True,
                ),
                FTWilliamsComparisonField(
                    label="Schedule A attached",
                    form_type=FormType.FORM_5500,
                    ftw_tag="SchAAttachedInd",
                    current_value="1",
                    proposed_value="1",
                    changed=False,
                    update_included=False,
                ),
            ],
        )

        self.assertIsNone(service._missing_required_schedule_a_payload(review))

    def test_schedule_a_update_still_requires_schedule_a_payload(self) -> None:
        service = FTWilliamsReviewService()
        review = FTWilliamsReview(
            filing_id="filing-1",
            schedule_a_match={"ftw_seq_no": "1"},
            fields=[
                FTWilliamsComparisonField(
                    label="Total premiums",
                    form_type=FormType.SCHEDULE_A,
                    ftw_tag="WlfrTotChargesPaidAmt",
                    proposed_value="100",
                    changed=True,
                    update_included=True,
                )
            ],
        )

        self.assertIsNotNone(service._missing_required_schedule_a_payload(review))

    def test_reconciliation_cannot_override_failed_readback_verification(self) -> None:
        service = FTWilliamsReviewService()

        self.assertFalse(
            service._reconciled_update_is_safe(
                attempted_count=2,
                remaining_count=0,
                current_query_success=True,
                verification_attempted=True,
                verification_mismatches=[{"tag": "DOLScheduleAData[2]/InsCarrierName"}],
            )
        )
        self.assertTrue(
            service._reconciled_update_is_safe(
                attempted_count=2,
                remaining_count=0,
                current_query_success=True,
                verification_attempted=False,
                verification_mismatches=[],
            )
        )

    def test_only_forms_with_changed_included_fields_need_an_update_payload(self) -> None:
        service = FTWilliamsReviewService()
        fields = [
            FTWilliamsComparisonField(
                label="Active participants",
                form_type=FormType.FORM_5500,
                ftw_tag="TotActivePartcpCnt",
                changed=True,
                update_included=True,
            ),
            FTWilliamsComparisonField(
                label="Carrier name",
                form_type=FormType.SCHEDULE_A,
                ftw_tag="InsCarrierName",
                changed=False,
                update_included=True,
            ),
        ]

        self.assertTrue(service._comparison_has_updates(fields, FormType.FORM_5500))
        self.assertFalse(service._comparison_has_updates(fields, FormType.SCHEDULE_A))

    def test_noop_schedule_payload_is_pruned_before_a_form_only_send(self) -> None:
        service = FTWilliamsReviewService()
        review = FTWilliamsReview(
            filing_id="filing-1",
            update_xml_5500="<DOL5500Data />",
            update_xml_schedule_a="<DOLScheduleAData />",
            fields=[
                FTWilliamsComparisonField(
                    label="Active participants",
                    form_type=FormType.FORM_5500,
                    changed=True,
                    update_included=True,
                ),
                FTWilliamsComparisonField(
                    label="Carrier name",
                    form_type=FormType.SCHEDULE_A,
                    changed=False,
                    update_included=True,
                ),
            ],
        )

        service._prune_noop_update_payloads(review)

        self.assertEqual(review.update_xml_5500, "<DOL5500Data />")
        self.assertEqual(review.update_xml_schedule_a, "")

    def test_schedule_a_readback_matches_preserved_records_by_ftw_sequence(self) -> None:
        service = FTWilliamsReviewService()
        documents = service._update_documents(
            """<?xml version="1.0" encoding="utf-8"?>
            <ftwLink><DataBatch>
              <DOLScheduleAData><FTWSeqNo>1</FTWSeqNo><InsCarrierEIN>36-2739571</InsCarrierEIN><WlfrTotChargesPaidAmt>100</WlfrTotChargesPaidAmt></DOLScheduleAData>
              <DOLScheduleAData><FTWSeqNo>2</FTWSeqNo><InsCarrierEIN>36-2739571</InsCarrierEIN><WlfrTotChargesPaidAmt>200</WlfrTotChargesPaidAmt></DOLScheduleAData>
            </DataBatch></ftwLink>""",
            "DOLScheduleAData",
        )
        statuses = [
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no="1",
                query_results={"InsCarrierEIN": "36-2739571", "WlfrTotChargesPaidAmt": "100"},
            ),
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no="2",
                query_results={"InsCarrierEIN": "36-2739571", "WlfrTotChargesPaidAmt": "200"},
            ),
        ]

        self.assertEqual(documents[1]["__ftw_seq_no"], "2")
        matched = service._match_readback_schedule_status(documents[1], statuses)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.ftw_seq_no, "2")
        self.assertEqual(
            service._compare_readback_document(
                FormType.SCHEDULE_A,
                documents[1],
                matched.query_results,
            ),
            [],
        )

    def test_schedule_a_readback_falls_back_to_unique_identity_when_sequence_moves(self) -> None:
        service = FTWilliamsReviewService()
        expected = {
            "__ftw_seq_no": "4",
            "InsCarrierName": "Cigna Health and Life Insurance Company",
            "InsContractNum": "3346625",
            "InsCarrierEIN": "59-1031071",
        }
        statuses = [
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no="4",
                query_results={
                    "InsCarrierName": "Anthem Blue Cross",
                    "InsContractNum": "300683",
                    "InsCarrierEIN": "23-7391136",
                },
            ),
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no="5",
                query_results={
                    "InsCarrierName": "Cigna Health and Life Insurance Company",
                    "InsContractNum": "3346625",
                    "InsCarrierEIN": "59-1031071",
                },
            ),
        ]

        matched = service._match_readback_schedule_status(expected, statuses)

        self.assertIsNotNone(matched)
        self.assertEqual(matched.ftw_seq_no, "5")

    def test_schedule_a_readback_full_scans_when_targeted_sequences_are_incomplete(self) -> None:
        service = FTWilliamsReviewService()
        service.ftwilliams.run_query = AsyncMock(
            return_value=FTWilliamsQueryResponse(
                operation="query_schedule_a",
                configured=True,
                sent=True,
                request_xml="targeted-request",
                success=True,
                statuses=[],
            )
        )
        fallback_status = FTWilliamsStatusItem(
            type="ScheduleA",
            error_code="0",
            ftw_seq_no="1",
            query_results={"InsContractNum": "300683"},
        )
        service._query_schedule_a_statuses = AsyncMock(
            return_value=([fallback_status], ["scan-request"], ["scan-response"], None)
        )
        review = FTWilliamsReview(
            filing_id="filing-1",
            schedule_a_records=[{"ftw_seq_no": "1", "query_results": {"InsContractNum": "300683"}}],
        )

        statuses, requests, responses, error = run_async(
            service._query_schedule_a_readback(review, {}, require_full_scan=False)
        )

        self.assertEqual(statuses, [fallback_status])
        self.assertEqual(requests, ["targeted-request", "scan-request"])
        self.assertEqual(responses, ["scan-response"])
        self.assertIsNone(error)
        service._query_schedule_a_statuses.assert_awaited_once()

    def setUp(self):
        clear_ftw_current_snapshot_cache()
        repositories._repository = repositories.MemoryRepository()

    def tearDown(self):
        clear_ftw_current_snapshot_cache()
        repositories._repository = None

    def test_resolves_dashboard_rules_to_real_ftw_tags(self):
        schedule_field = ExtractedField(
            filing_id="filing",
            source_field_name="1a. Name of Insurance Company",
            normalized_field_name="carrier",
            mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
            mapped_label="1a. Name of Insurance Company",
            form_type=FormType.SCHEDULE_A,
            source_document_type=DocumentType.SCHEDULE_A,
            priority=FieldPriority.HIGH,
            value="ABC Insurance",
            proposed_value="ABC Insurance",
        )
        worksheet_field = ExtractedField(
            filing_id="filing",
            source_field_name="1a. Plan Name",
            normalized_field_name="plan_name",
            mapped_rule_key="form_5500_part_i_1a_plan_name",
            mapped_label="1a. Plan Name",
            form_type=FormType.FORM_5500,
            source_document_type=DocumentType.PLAN_WORKSHEET,
            priority=FieldPriority.HIGH,
            value="ABC Plan",
            proposed_value="ABC Plan",
        )

        self.assertEqual(resolve_ftw_tag(schedule_field), "InsCarrierName")
        self.assertEqual(resolve_ftw_tag(worksheet_field), "PLAN_NAME0")

        sponsor_field = ExtractedField(
            filing_id="filing",
            source_field_name="1d. Plan Sponsor Name",
            normalized_field_name="sponsor_name",
            mapped_rule_key="form_5500_part_i_1d_plan_sponsor_name",
            mapped_label="1d. Plan Sponsor Name",
            form_type=FormType.FORM_5500,
            source_document_type=DocumentType.PLAN_WORKSHEET,
            priority=FieldPriority.HIGH,
            value="ABC Sponsor",
            proposed_value="ABC Sponsor",
        )

        self.assertEqual(resolve_ftw_tag(sponsor_field), "SPONSOR_DFE_NAME0")
        self.assertEqual(resolve_ftw_update_tag(sponsor_field), "SDName")

    def test_form_5500_update_map_uses_sandbox_verified_current_tags(self):
        expected = {
            "form_5500_part_i_1a_plan_name": "PlanName",
            "form_5500_part_i_1b_plan_number_pn": "SponsDfePlanNum",
            "form_5500_part_i_1c_plan_effective_date": "PlanEffDate",
            "form_5500_part_i_1d_plan_sponsor_name": "SDName",
            "form_5500_part_i_1e_plan_sponsor_ein": "SDEIN",
            "form_5500_part_i_1f_plan_sponsor_address": "SDAddressLine1",
            "form_5500_part_i_1g_business_code": "BusinessCode",
            "form_5500_part_i_2a_plan_administrator_name": "ADMINName",
            "form_5500_part_i_6_plan_year_beginning_date": "PlanYearBeginDate",
            "form_5500_part_i_7_plan_year_ending_date": "PlanYearEndDate",
            "form_5500_part_ii_4_plan_characteristic_codes": "TypeWelfareBnftCode1",
            "form_5500_part_ii_8c_welfare_benefit_features": "TypeWelfareBnftCode1",
            "form_5500_part_ii_10b_schedules_attached": "SchAAttachedInd",
        }

        for rule_key, tag in expected.items():
            self.assertEqual(FORM_5500_UPDATE_TAGS_BY_RULE.get(rule_key), tag)

        rejected_legacy_tags = {
            "PLAN_NAME0",
            "SPONS_DFE_PN",
            "PLAN_EFF_DATE",
            "SPONSOR_DFE_NAME0",
            "SPONS_DFE_EIN",
            "SPONS_DFE_MAIL_STR_ADDRESS",
            "BUSINESS_CODE",
            "ADMIN_NAME0",
            "FORM_PLAN_YEAR_BEGIN_DATE",
            "FORM_TAX_PRD",
            "TYPE_WELFARE_BNFT_CODE1",
            "SCH_A_ATTACHED_IND",
        }
        self.assertTrue(rejected_legacy_tags.isdisjoint(FORM_5500_UPDATE_TAGS_BY_RULE.values()))

    def test_schedule_a_policy_dates_are_writable_when_the_selected_record_year_is_safe(self):
        def schedule_date(rule_key: str, label: str, value: str) -> ExtractedField:
            return ExtractedField(
                filing_id="filing",
                source_field_name=label,
                normalized_field_name=label.lower(),
                mapped_rule_key=rule_key,
                mapped_label=label,
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value=value,
                proposed_value=value,
            )

        fields = [
            schedule_date(
                "schedule_a_part_i_1f_policy_year_beginning_date",
                "1f. Policy Year Beginning Date",
                "01/01/2025",
            ),
            schedule_date(
                "schedule_a_part_i_1g_policy_year_ending_date",
                "1g. Policy Year Ending Date",
                "12/31/2025",
            ),
        ]

        safe = FTWilliamsReviewService()._safe_update_fields(
            fields,
            FormType.SCHEDULE_A,
            {"InsPolicyFromDate": "01/01/2024", "InsPolicyToDate": "12/31/2024"},
        )

        self.assertEqual(
            {field.mapped_rule_key for field in safe},
            {field.mapped_rule_key for field in fields},
        )

    def test_administrator_name_change_is_blocked_when_ft_requires_full_contact_block(self):
        administrator = ExtractedField(
            filing_id="filing",
            source_field_name="2a. Plan Administrator Name",
            normalized_field_name="administrator_name",
            mapped_rule_key="form_5500_part_i_2a_plan_administrator_name",
            mapped_label="2a. Plan Administrator Name",
            form_type=FormType.FORM_5500,
            source_document_type=DocumentType.PLAN_WORKSHEET,
            priority=FieldPriority.HIGH,
            value="LESLIE HANLEY",
            proposed_value="LESLIE HANLEY",
        )

        safe = FTWilliamsReviewService()._safe_update_fields(
            [administrator],
            FormType.FORM_5500,
            {
                "ADMINName": "NEW YORK YANKEES PARTNERSHIP",
                "SDName": "NEW YORK YANKEES PARTNERSHIP",
                "AdminNameSameAsPlanSponsInd": "1",
            },
        )

        self.assertEqual(safe, [])

        comparison = FTWilliamsReviewService()._comparison_fields(
            [administrator],
            {
                "ADMINName": "NEW YORK YANKEES PARTNERSHIP",
                "SDName": "NEW YORK YANKEES PARTNERSHIP",
                "AdminNameSameAsPlanSponsInd": "1",
            },
            {},
            update_fields=safe,
        )

        self.assertFalse(comparison[0].update_included)
        self.assertIn("complete FT Williams administrator contact block", comparison[0].update_exclusion_reason or "")

    def test_administrator_name_can_restore_same_as_sponsor(self):
        administrator = ExtractedField(
            filing_id="filing",
            source_field_name="2a. Plan Administrator Name",
            normalized_field_name="administrator_name",
            mapped_rule_key="form_5500_part_i_2a_plan_administrator_name",
            mapped_label="2a. Plan Administrator Name",
            form_type=FormType.FORM_5500,
            source_document_type=DocumentType.PLAN_WORKSHEET,
            priority=FieldPriority.HIGH,
            value="NEW YORK YANKEES PARTNERSHIP",
            proposed_value="NEW YORK YANKEES PARTNERSHIP",
        )

        safe = FTWilliamsReviewService()._safe_update_fields(
            [administrator],
            FormType.FORM_5500,
            {
                "ADMINName": "LESLIE HANLEY",
                "SDName": "NEW YORK YANKEES PARTNERSHIP",
                "AdminNameSameAsPlanSponsInd": "0",
            },
        )

        self.assertEqual(safe, [administrator])

    def test_prepare_review_reports_pre_send_validation_without_crashing(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="14. Active Participants at End",
                        normalized_field_name="active_participants_end",
                        mapped_rule_key="form_5500_part_ii_14_active_participants_at_end",
                        mapped_label="14. Active Participants at End",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="one hundred",
                        proposed_value="one hundred",
                    )
                ]
            )
        )

        review = run_async(FTWilliamsReviewService().prepare_review(filing.id, send_queries=False))

        self.assertIsNotNone(review.client_error)
        self.assertEqual(review.client_error.code, "FTW_PRE_SEND_VALIDATION")
        self.assertEqual(review.client_error.rejected_fields[0].tag, "TotActivePartcpCnt")
        self.assertNotIn("TotActivePartcpCnt", review.update_xml_5500 or "")

    def test_proposed_xml_uses_real_schedule_a_and_5500_tags(self):
        fields = [
            ExtractedField(
                filing_id="filing",
                source_field_name="1a. Name of Insurance Company",
                normalized_field_name="carrier",
                mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                mapped_label="1a. Name of Insurance Company",
                form_type=FormType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="ABC Insurance",
                proposed_value="ABC Insurance",
            ),
            ExtractedField(
                filing_id="filing",
                source_field_name="1a. Plan Name",
                normalized_field_name="plan_name",
                mapped_rule_key="form_5500_part_i_1a_plan_name",
                mapped_label="1a. Plan Name",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="ABC Plan",
                proposed_value="ABC Plan",
            ),
            ExtractedField(
                filing_id="filing",
                source_field_name="11. Total Participants",
                normalized_field_name="participants",
                mapped_rule_key="form_5500_part_ii_11_total_participants_at_beginning_of_year",
                mapped_label="11. Total Participants",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="100",
                proposed_value="100",
            ),
        ]

        xml = build_proposed_ftw_xml(fields)

        self.assertIn("<DOL5500Data>", xml)
        self.assertNotIn("PLAN_NAME0", xml)
        self.assertIn("<TotPartcpBoyCnt>100</TotPartcpBoyCnt>", xml)
        self.assertIn("<DOLScheduleAData>", xml)
        self.assertIn("<InsCarrierName>ABC Insurance</InsCarrierName>", xml)
        self.assertNotIn("field_1a_name_of_insurance_company", xml)

    def test_update_xml_does_not_copy_current_ftw_fields(self):
        fields = [
            ExtractedField(
                filing_id="filing",
                source_field_name="11. Total Participants",
                normalized_field_name="participants",
                mapped_rule_key="form_5500_part_ii_11_total_participants_at_beginning_of_year",
                mapped_label="11. Total Participants",
                form_type=FormType.FORM_5500,
                priority=FieldPriority.HIGH,
                value="279",
                proposed_value="279",
            ),
        ]

        xml = build_single_document_update_xml(
            "DOL5500Data",
            fields,
            FormType.FORM_5500,
            transaction_type="1",
            customer_id="26-3189470",
            plan_id="26-3189470501",
            year="2024",
            current_values={"PlanName": "Existing Plan"},
        )

        self.assertIn("<TotPartcpBoyCnt>279</TotPartcpBoyCnt>", xml)
        self.assertNotIn("<PlanName>Existing Plan</PlanName>", xml)

    def test_prepare_review_persists_side_by_side_preview(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1a. Name of Insurance Company",
                        normalized_field_name="carrier",
                        mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                        mapped_label="1a. Name of Insurance Company",
                        form_type=FormType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="ABC Insurance",
                        proposed_value="ABC Insurance",
                    )
                ]
            )
        )

        review = run_async(FTWilliamsReviewService().prepare_review(filing.id, send_queries=False))
        stored = run_async(repo.get_ftwilliams_review(filing.id))
        updated_filing = run_async(repo.get_filing(filing.id))

        self.assertIsNotNone(stored)
        self.assertEqual(review.fields[0].ftw_tag, "InsCarrierName")
        self.assertEqual(review.fields[0].proposed_value, "ABC Insurance")
        self.assertEqual(review.current_query_sent, False)
        self.assertNotIn("<DOLScheduleAData>", updated_filing.proposed_xml)
        self.assertIn("No approved FT Williams fields are available yet", updated_filing.proposed_xml)

    def test_prepare_review_uses_ftw_query_tags_and_exposes_schedule_candidates(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="sponsor_ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="73-1185740",
                        proposed_value="73-1185740",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Plan Number (PN)",
                        normalized_field_name="plan_number",
                        mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                        mapped_label="1b. Plan Number (PN)",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="501",
                        proposed_value="501",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="7. Plan Year Ending Date",
                        normalized_field_name="plan_year_end",
                        mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                        mapped_label="7. Plan Year Ending Date",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="09/30/2025",
                        proposed_value="09/30/2025",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="13. Active participants at beginning",
                        normalized_field_name="active_participants_beginning",
                        mapped_rule_key="form_5500_part_ii_13_active_participants_at_beginning",
                        mapped_label="13. Active participants at beginning",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="249",
                        proposed_value="249",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="14. Active participants at end",
                        normalized_field_name="active_participants_end",
                        mapped_rule_key="form_5500_part_ii_14_active_participants_at_end",
                        mapped_label="14. Active participants at end",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="279",
                        proposed_value="279",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="12. Total participants at end of year",
                        normalized_field_name="total_participants_end",
                        mapped_rule_key="form_5500_part_ii_12_total_participants_at_end_of_year",
                        mapped_label="12. Total participants at end of year",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="298",
                        proposed_value="298",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1a. Name of Insurance Company",
                        normalized_field_name="carrier",
                        mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                        mapped_label="1a. Name of Insurance Company",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="UnitedHealthcare",
                        proposed_value="UnitedHealthcare",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="QA Carrier Registry Number",
                        normalized_field_name="qa carrier registry number",
                        mapped_rule_key="schedule_a_part_i_1c_naic_code",
                        mapped_label="1c. NAIC Code",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="98765",
                        proposed_value="98765",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="3a. Name of Agent/Broker/Person",
                        normalized_field_name="broker_name",
                        mapped_rule_key="schedule_a_part_i_3a_name_of_agent_broker_person",
                        mapped_label="3a. Name of Agent/Broker/Person",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="NFP LLC",
                        proposed_value="NFP LLC",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="3b. Amount of Commissions",
                        normalized_field_name="broker_commission",
                        mapped_rule_key="schedule_a_part_i_3b_amount_of_commissions",
                        mapped_label="3b. Amount of Commissions",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="111893",
                        proposed_value="111893",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1d. Contract/Policy Number",
                        normalized_field_name="contract",
                        mapped_rule_key="schedule_a_part_i_1d_contract_policy_number",
                        mapped_label="1d. Contract/Policy Number",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="1246876",
                        proposed_value="1246876",
                    ),
                ]
            )
        )

        fake_ftw = FakeFTWilliamsCurrentTagService()
        review = run_async(FTWilliamsReviewService(fake_ftw).prepare_review(filing.id, send_queries=True))
        by_label = {field.label: field for field in review.fields}
        selected_schedule_call = next(
            call
            for call in fake_ftw.calls
            if call.operation == "query_schedule_a" and call.ftw_seq_no == "1"
        )

        self.assertTrue(review.current_year_exists)
        self.assertTrue(review.query_access_verified)
        self.assertEqual(review.update_access_status, "NOT_ATTEMPTED")
        self.assertFalse(review.bring_forward_required)
        self.assertEqual(review.status, FTWilliamsReviewStatus.CURRENT_QUERIED)
        self.assertEqual(by_label["13. Active participants at beginning"].current_value, "249")
        self.assertEqual(by_label["14. Active participants at end"].current_value, "279")
        self.assertEqual(by_label["12. Total participants at end of year"].current_value, "298")
        self.assertEqual(by_label["1e. Plan Sponsor EIN"].current_value, "73-1185740")
        self.assertEqual(by_label["1c. NAIC Code"].current_value, "98765")
        self.assertEqual(by_label["3a. Name of Agent/Broker/Person"].current_value, "NFP LLC")
        self.assertEqual(by_label["3b. Amount of Commissions"].current_value, "111893")
        self.assertEqual(len(review.schedule_a_candidates), 2)
        self.assertEqual(review.schedule_a_candidates[0]["ftw_seq_no"], "1")
        self.assertEqual({record["ftw_seq_no"] for record in review.schedule_a_records}, {"1", "3"})
        self.assertEqual(selected_schedule_call.year, "2025")
        self.assertEqual(selected_schedule_call.ftw_customer_id, "571475632")
        self.assertEqual(selected_schedule_call.ftw_plan_id, "682436783")

    def test_prepare_review_preserves_loaded_ftw_current_values_after_local_edit(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))

        def field(rule_key: str, label: str, value: str, form_type: FormType, document_type: DocumentType) -> ExtractedField:
            return ExtractedField(
                filing_id=filing.id,
                source_field_name=label,
                normalized_field_name=label.lower(),
                mapped_rule_key=rule_key,
                mapped_label=label,
                form_type=form_type,
                source_document_type=document_type,
                priority=FieldPriority.HIGH,
                value=value,
                proposed_value=value,
            )

        fields = run_async(
            repo.add_fields(
                [
                    field("form_5500_part_i_1e_plan_sponsor_ein", "1e. Plan Sponsor EIN", "73-1185740", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_1b_plan_number_pn", "1b. Plan Number (PN)", "501", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_7_plan_year_ending_date", "7. Plan Year Ending Date", "09/30/2025", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_1f_plan_sponsor_address", "1f. Plan Sponsor Address", "750 E MAIN ST STAMFORD CT 069023831", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_ii_9_plan_funding_arrangement", "9. Plan funding arrangement", "Insurance", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_ii_10a_plan_benefit_arrangement", "10a. Plan benefit arrangement", "Insurance", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_ii_10b_schedules_attached", "10b. Schedules attached", "A", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("schedule_a_part_i_1a_name_of_insurance_company", "1a. Name of Insurance Company", "UnitedHealthcare", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1d_contract_policy_number", "1d. Contract/Policy Number", "1246876", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_iii_11_did_the_insurance_company_fail_to_provide_any_information_necessary_to_complete_schedule_a", "11. Insurance company failed to provide information", "No", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_3b_amount_of_commissions", "3b. Amount of Commissions", "111893", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_3d_purpose", "3d. Purpose", "COMMISSIONS & FEES", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                ]
            )
        )
        commission_field = next(field for field in fields if field.mapped_rule_key == "schedule_a_part_i_3b_amount_of_commissions")
        fake_ftw = FakeFTWilliamsCurrentTagService()
        service = FTWilliamsReviewService(fake_ftw)

        queried = run_async(service.prepare_review(filing.id, send_queries=True))
        self.assertTrue(queried.current_query_success)
        self.assertEqual(queried.form_5500_current_values["SDAddressLine1"], "750 E MAIN ST")
        self.assertEqual(queried.schedule_a_match["ftw_seq_no"], "1")
        self.assertEqual({record["ftw_seq_no"] for record in queried.schedule_a_records}, {"1", "3"})

        # Reviews created before raw current-data snapshots were introduced must
        # still keep their displayed FTW values after a local field decision.
        queried.form_5500_current_values = {}
        queried.query_access_verified = True
        queried.update_access_status = "GRANTED"
        queried.edit_check_baseline_success = True
        queried.edit_check_final_success = True
        queried.audit_pdf_status = "AVAILABLE"
        queried.audit_pdf_key = "ftw-audit/filing/schedule-a.pdf"
        queried.audit_pdf_sha256 = "abc123"
        run_async(repo.upsert_ftwilliams_review(queried))

        call_count = len(fake_ftw.calls)
        run_async(repo.update_field(filing.id, commission_field.id, "112000"))
        refreshed = run_async(service.prepare_review(filing.id, send_queries=False))
        by_label = {field.label: field for field in refreshed.fields}

        self.assertEqual(len(fake_ftw.calls), call_count)
        self.assertTrue(refreshed.current_query_sent)
        self.assertTrue(refreshed.current_query_success)
        self.assertTrue(refreshed.query_access_verified)
        self.assertEqual(refreshed.update_access_status, "GRANTED")
        self.assertTrue(refreshed.edit_check_baseline_success)
        self.assertTrue(refreshed.edit_check_final_success)
        self.assertEqual(refreshed.audit_pdf_status, "AVAILABLE")
        self.assertEqual(refreshed.audit_pdf_key, "ftw-audit/filing/schedule-a.pdf")
        self.assertEqual(refreshed.audit_pdf_sha256, "abc123")
        self.assertEqual(refreshed.schedule_a_match["ftw_seq_no"], "1")
        self.assertEqual(refreshed.ftw_seq_no, "1")
        self.assertEqual({record["ftw_seq_no"] for record in refreshed.schedule_a_records}, {"1", "3"})
        self.assertEqual(by_label["1e. Plan Sponsor EIN"].current_value, "73-1185740")
        self.assertEqual(by_label["1f. Plan Sponsor Address"].current_value, "750 E MAIN ST, STAMFORD CT 06902-3831")
        self.assertEqual(by_label["9. Plan funding arrangement"].current_value, "Insurance")
        self.assertEqual(by_label["10a. Plan benefit arrangement"].current_value, "Insurance")
        self.assertEqual(by_label["10b. Schedules attached"].current_value, "A")
        self.assertFalse(by_label["9. Plan funding arrangement"].changed)
        self.assertFalse(by_label["10a. Plan benefit arrangement"].changed)
        self.assertFalse(by_label["10b. Schedules attached"].changed)
        self.assertEqual(by_label["1d. Contract/Policy Number"].current_value, "1246876")
        self.assertEqual(by_label["11. Insurance company failed to provide information"].current_value, "2")
        self.assertFalse(by_label["11. Insurance company failed to provide information"].changed)
        self.assertEqual(by_label["3b. Amount of Commissions"].current_value, "111893")
        self.assertEqual(by_label["3b. Amount of Commissions"].proposed_value, "112000")
        self.assertEqual(by_label["3d. Purpose"].current_value, "COMMISSIONS AND FEES")
        self.assertFalse(by_label["3d. Purpose"].changed)

    def test_blank_extraction_displays_retained_ftw_value_without_sending_it(self):
        field = ExtractedField(
            filing_id="filing-1",
            source_field_name="13. Active participants at beginning",
            normalized_field_name="active participants beginning",
            mapped_rule_key="form_5500_part_ii_13_active_participants_at_beginning",
            mapped_label="13. Active participants at beginning",
            form_type=FormType.FORM_5500,
            source_document_type=DocumentType.PLAN_WORKSHEET,
            priority=FieldPriority.HIGH,
            value="",
            proposed_value="",
        )

        comparison = FTWilliamsReviewService()._comparison_fields(
            [field],
            {"TotActPartcpBoyCnt": "249"},
            {},
            update_fields=[field],
        )[0]

        self.assertEqual(comparison.current_value, "249")
        self.assertEqual(comparison.extracted_value, "")
        self.assertEqual(comparison.proposed_value, "249")
        self.assertFalse(comparison.changed)
        self.assertFalse(comparison.update_included)

    def test_prepare_review_builds_schedule_a_payload_for_all_records_and_updates_selected(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))

        def field(rule_key: str, label: str, value: str, form_type: FormType, document_type: DocumentType) -> ExtractedField:
            return ExtractedField(
                filing_id=filing.id,
                source_field_name=label,
                normalized_field_name=label.lower(),
                mapped_rule_key=rule_key,
                mapped_label=label,
                form_type=form_type,
                source_document_type=document_type,
                priority=FieldPriority.HIGH,
                value=value,
                proposed_value=value,
            )

        fields = [
            field("form_5500_part_i_1e_plan_sponsor_ein", "1e. Plan Sponsor EIN", "73-1185740", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
            field("form_5500_part_i_1b_plan_number_pn", "1b. Plan Number (PN)", "501", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
            field("form_5500_part_i_7_plan_year_ending_date", "7. Plan Year Ending Date", "09/30/2025", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
            field("schedule_a_part_i_1a_name_of_insurance_company", "1a. Name of Insurance Company", "UnitedHealthcare", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
            field("schedule_a_part_i_1d_contract_policy_number", "1d. Contract/Policy Number", "1246876", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
            field("schedule_a_part_i_3a_name_of_agent_broker_person", "3a. Name of Agent/Broker/Person", "New Broker Name", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
        ]
        run_async(repo.add_fields(fields))

        review = run_async(FTWilliamsReviewService(FakeFTWilliamsCurrentTagService()).prepare_review(filing.id, send_queries=True))

        self.assertEqual(review.schedule_a_match["ftw_seq_no"], "1")
        self.assertEqual({record["ftw_seq_no"] for record in review.schedule_a_records}, {"1", "3"})
        self.assertEqual(review.update_xml_schedule_a.count("<DOLScheduleAData>"), 2)
        self.assertNotIn("<FTWSeqNo>", review.update_xml_schedule_a)
        self.assertIn("<NameXX>New Broker Name</NameXX>", review.update_xml_schedule_a)
        self.assertIn("<InsCarrierName>Other Carrier</InsCarrierName>", review.update_xml_schedule_a)
        self.assertIn("<InsContractNum>OTHER-3</InsContractNum>", review.update_xml_schedule_a)

    def test_structured_single_broker_row_wins_over_unreviewed_flat_broker_extraction(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.update_filing(
                filing.id,
                {
                    "schedule_a_broker_rows": [
                        ScheduleABrokerRow(name="NFP LLC", organization_code="3", commission_total="111893")
                    ]
                },
            )
        )

        def field(rule_key: str, label: str, value: str, form_type: FormType, document_type: DocumentType) -> ExtractedField:
            return ExtractedField(
                filing_id=filing.id,
                source_field_name=label,
                normalized_field_name=label.lower(),
                mapped_rule_key=rule_key,
                mapped_label=label,
                form_type=form_type,
                source_document_type=document_type,
                priority=FieldPriority.HIGH,
                value=value,
                proposed_value=value,
            )

        fields = [
            field("form_5500_part_i_1e_plan_sponsor_ein", "1e. Plan Sponsor EIN", "73-1185740", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
            field("form_5500_part_i_1b_plan_number_pn", "1b. Plan Number (PN)", "501", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
            field("form_5500_part_i_7_plan_year_ending_date", "7. Plan Year Ending Date", "09/30/2025", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
            field("schedule_a_part_i_1a_name_of_insurance_company", "1a. Name of Insurance Company", "UnitedHealthcare", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
            field("schedule_a_part_i_1d_contract_policy_number", "1d. Contract/Policy Number", "1246876", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
            field("schedule_a_part_i_3a_name_of_agent_broker_person", "3a. Name of Agent/Broker/Person", "March", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
        ]
        run_async(repo.add_fields(fields))

        review = run_async(FTWilliamsReviewService(FakeFTWilliamsCurrentTagService()).prepare_review(filing.id, send_queries=True))
        broker_name = next(item for item in review.fields if item.rule_key == "schedule_a_part_i_3a_name_of_agent_broker_person")

        self.assertTrue(review.schedule_a_broker_match_complete)
        self.assertFalse(broker_name.update_included)
        self.assertIn("<NameXX>NFP LLC</NameXX>", review.update_xml_schedule_a)
        self.assertNotIn("<NameXX>March</NameXX>", review.update_xml_schedule_a)

    def test_explicit_broker_field_edit_remains_authoritative_with_structured_rows(self):
        edited = ExtractedField(
            filing_id="filing-1",
            source_field_name="3a. Name of Agent/Broker/Person",
            normalized_field_name="broker_name",
            mapped_rule_key="schedule_a_part_i_3a_name_of_agent_broker_person",
            mapped_label="3a. Name of Agent/Broker/Person",
            form_type=FormType.SCHEDULE_A,
            source_document_type=DocumentType.SCHEDULE_A,
            priority=FieldPriority.HIGH,
            value="Reviewer Broker Name",
            proposed_value="Reviewer Broker Name",
            status=ExtractedFieldStatus.EDITED,
        )

        safe = FTWilliamsReviewService()._safe_update_fields(
            [edited],
            FormType.SCHEDULE_A,
            {"Name1": "Current Broker Name"},
            has_structured_schedule_a_brokers=True,
        )

        self.assertEqual(safe, [edited])

    def test_prepare_review_uses_indexed_broker_rows_when_multiple_broker_rows_exist(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.update_filing(
                filing.id,
                {
                    "schedule_a_broker_rows": [
                        ScheduleABrokerRow(name="NFP CORPORATE SERVICES NY LLC", organization_code="03", commission_total="1,576", fee_total="44"),
                        ScheduleABrokerRow(name="NFP INS SERVICES INC", organization_code="03", commission_total="422", fee_total="0"),
                    ]
                },
            )
        )

        def field(rule_key: str, label: str, value: str, form_type: FormType, document_type: DocumentType) -> ExtractedField:
            return ExtractedField(
                filing_id=filing.id,
                source_field_name=label,
                normalized_field_name=label.lower(),
                mapped_rule_key=rule_key,
                mapped_label=label,
                form_type=form_type,
                source_document_type=document_type,
                priority=FieldPriority.HIGH,
                value=value,
                proposed_value=value,
            )

        fields = [
            field("form_5500_part_i_1e_plan_sponsor_ein", "1e. Plan Sponsor EIN", "73-1185740", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
            field("form_5500_part_i_1b_plan_number_pn", "1b. Plan Number (PN)", "501", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
            field("form_5500_part_i_7_plan_year_ending_date", "7. Plan Year Ending Date", "09/30/2025", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
            field("schedule_a_part_i_1a_name_of_insurance_company", "1a. Name of Insurance Company", "UnitedHealthcare", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
            field("schedule_a_part_i_1d_contract_policy_number", "1d. Contract/Policy Number", "1246876", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
            field("schedule_a_part_i_3a_name_of_agent_broker_person", "3a. Name of Agent/Broker/Person", "NFP CORPORATE SERVICES NY LLC", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
            field("schedule_a_part_i_3b_amount_of_commissions", "3b. Amount of Commissions", "1,998", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
            field("schedule_a_part_i_3c_amount_of_fees", "3c. Amount of Fees", "345", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
            field("schedule_a_part_i_3d_purpose", "3d. Purpose", "COMMISSIONS & FEES", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
            field("schedule_a_part_i_3e_organizational_code", "3e. Organizational Code", "03", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
        ]
        run_async(repo.add_fields(fields))

        service = FTWilliamsReviewService(FakeFTWilliamsCurrentTagService())
        review = run_async(service.prepare_review(filing.id, send_queries=True))
        by_label = {field.label: field for field in review.fields}

        self.assertEqual(len(review.schedule_a_broker_rows), 2)
        self.assertFalse(by_label["3a. Name of Agent/Broker/Person"].update_included)
        self.assertFalse(by_label["3b. Amount of Commissions"].update_included)
        self.assertFalse(review.schedule_a_broker_match_complete)
        self.assertEqual([match.status for match in review.schedule_a_broker_matches], ["NEEDS_CONFIRMATION", "NEEDS_CONFIRMATION"])
        self.assertEqual(review.update_xml_schedule_a, "")
        self.assertIn("broker rows need confirmation", review.error_message)

        confirmed = run_async(
            service.set_schedule_a_broker_matches(
                filing.id,
                FTWilliamsBrokerMatchesRequest(
                    decisions=[
                        FTWilliamsBrokerMatchDecision(extracted_index=0, ftw_index=0),
                        FTWilliamsBrokerMatchDecision(extracted_index=1, create_new=True),
                    ]
                ),
            )
        )

        self.assertTrue(confirmed.schedule_a_broker_match_complete)
        self.assertIn("<NameXX>NFP LLC</NameXX>", confirmed.update_xml_schedule_a)
        self.assertIn("<CommPdAmtXX>1576</CommPdAmtXX>", confirmed.update_xml_schedule_a)
        self.assertIn("<NameXX>NFP INS SERVICES INC</NameXX>", confirmed.update_xml_schedule_a)
        self.assertEqual(confirmed.update_xml_schedule_a.count("<Broker>"), 2)

    def test_reviewer_can_remove_parser_fragment_and_rebuild_broker_preview(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        rows = [
            ScheduleABrokerRow(
                name="HUB INTERNATIONAL TEXAS INC",
                address_line_1="3221 COLLINSWORTH ST",
                city="FORT WORTH",
                state="TX",
                zip_code="76107-5739",
                organization_code="3",
                fee_total="44",
            ),
            ScheduleABrokerRow(
                name="HUB INTERNATIONAL TEXAS INC",
                city="FORT WORTH ST: TX ZIP: 76107-5739",
                fee_total="3",
            ),
        ]

        review = run_async(
            FTWilliamsReviewService().update_schedule_a_broker_rows(
                filing.id,
                FTWilliamsScheduleABrokerRowsRequest(rows=rows),
            )
        )
        stored = run_async(repo.get_filing(filing.id))

        self.assertEqual(len(stored.schedule_a_broker_rows), 1)
        self.assertEqual(len(review.schedule_a_broker_rows), 1)
        self.assertEqual(review.schedule_a_broker_rows[0].city, "FORT WORTH")
        self.assertEqual(review.schedule_a_broker_rows[0].fee_total, "44")

    def test_reviewer_can_save_one_broker_while_other_rows_are_incomplete(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        rows = [
            ScheduleABrokerRow(
                name="EOI SERVICE COMPANY INC",
                city="ANAHEIM",
                state="CA",
                organization_code="3",
                commission_total="8567",
            ),
            ScheduleABrokerRow(
                name="GIS BENEFITS INC",
                city="MORRIS",
                state="IL",
                organization_code="",
                commission_total="4661",
            ),
        ]

        review = run_async(
            FTWilliamsReviewService().update_schedule_a_broker_rows(
                filing.id,
                FTWilliamsScheduleABrokerRowsRequest(rows=rows),
            )
        )
        stored = run_async(repo.get_filing(filing.id))

        self.assertEqual(stored.schedule_a_broker_rows[0].organization_code, "3")
        self.assertEqual(stored.schedule_a_broker_rows[1].organization_code, "")
        self.assertEqual(len(review.schedule_a_broker_rows), 2)
        self.assertEqual(review.update_xml_schedule_a, "")

    def test_reviewer_broker_edit_returns_exact_invalid_field(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))

        with self.assertRaisesRegex(
            ValueError,
            r"Broker row 1 - City: maximum length is 30 characters.*FORT WORTH ST: TX ZIP: 76107-5739",
        ):
            run_async(
                FTWilliamsReviewService().update_schedule_a_broker_rows(
                    filing.id,
                    FTWilliamsScheduleABrokerRowsRequest(
                        rows=[
                            ScheduleABrokerRow(
                                name="HUB INTERNATIONAL TEXAS INC",
                                city="FORT WORTH ST: TX ZIP: 76107-5739",
                                organization_code="3",
                            )
                        ]
                    ),
                )
            )

    def test_prepare_review_excludes_broker_name_when_it_contains_address_text(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))

        def field(rule_key: str, label: str, value: str, form_type: FormType, document_type: DocumentType) -> ExtractedField:
            return ExtractedField(
                filing_id=filing.id,
                source_field_name=label,
                normalized_field_name=label.lower(),
                mapped_rule_key=rule_key,
                mapped_label=label,
                form_type=form_type,
                source_document_type=document_type,
                priority=FieldPriority.HIGH,
                value=value,
                proposed_value=value,
            )

        fields = [
            field("form_5500_part_i_1e_plan_sponsor_ein", "1e. Plan Sponsor EIN", "73-1185740", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
            field("form_5500_part_i_1b_plan_number_pn", "1b. Plan Number (PN)", "501", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
            field("form_5500_part_i_7_plan_year_ending_date", "7. Plan Year Ending Date", "09/30/2025", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
            field("schedule_a_part_i_1a_name_of_insurance_company", "1a. Name of Insurance Company", "UnitedHealthcare", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
            field("schedule_a_part_i_1d_contract_policy_number", "1d. Contract/Policy Number", "1246876", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
            field("schedule_a_part_i_3b_amount_of_commissions", "3b. Amount of Commissions", "222000", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
            field(
                "schedule_a_part_i_3a_name_of_agent_broker_person",
                "3a. Name of Agent/Broker/Person",
                "RSC INSURANCE BROKERAGE INC, ATTN: AMS - LEGACY DIRECT BILL, PO BOX 736061, CHICAGO IL 60673-6061",
                FormType.SCHEDULE_A,
                DocumentType.SCHEDULE_A,
            ),
        ]
        run_async(repo.add_fields(fields))

        review = run_async(FTWilliamsReviewService(FakeFTWilliamsCurrentTagService()).prepare_review(filing.id, send_queries=True))
        by_label = {field.label: field for field in review.fields}

        self.assertFalse(by_label["3a. Name of Agent/Broker/Person"].update_included)
        self.assertNotIn("ATTN: AMS", review.update_xml_schedule_a)
        self.assertIn("<NameXX>NFP LLC</NameXX>", review.update_xml_schedule_a)

    def test_prepare_review_preserves_active_failed_update_state(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(repo.update_filing(filing.id, {"status": FilingStatus.FAILED, "error_message": "DOL5500Data error 60: invalid field reg: PLAN_NAME0"}))
        run_async(
            repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    status=FTWilliamsReviewStatus.UPDATE_FAILED,
                    configured=True,
                    current_query_sent=True,
                    current_query_success=True,
                    error_message="DOL5500Data error 60: invalid field reg: PLAN_NAME0",
                )
            )
        )

        def field(rule_key: str, label: str, value: str, form_type: FormType, document_type: DocumentType) -> ExtractedField:
            return ExtractedField(
                filing_id=filing.id,
                source_field_name=label,
                normalized_field_name=label.lower(),
                mapped_rule_key=rule_key,
                mapped_label=label,
                form_type=form_type,
                source_document_type=document_type,
                priority=FieldPriority.HIGH,
                value=value,
                proposed_value=value,
            )

        fields = [
            field("form_5500_part_i_1e_plan_sponsor_ein", "1e. Plan Sponsor EIN", "73-1185740", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
            field("form_5500_part_i_1b_plan_number_pn", "1b. Plan Number (PN)", "501", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
            field("form_5500_part_i_7_plan_year_ending_date", "7. Plan Year Ending Date", "09/30/2025", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
            field("schedule_a_part_i_1a_name_of_insurance_company", "1a. Name of Insurance Company", "UnitedHealthcare", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
            field("schedule_a_part_i_1d_contract_policy_number", "1d. Contract/Policy Number", "1246876", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
            field("schedule_a_part_i_3a_name_of_agent_broker_person", "3a. Name of Agent/Broker/Person", "New Broker Name", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
        ]
        run_async(repo.add_fields(fields))

        review = run_async(FTWilliamsReviewService(FakeFTWilliamsCurrentTagService()).prepare_review(filing.id, send_queries=True))
        updated_filing = run_async(repo.get_filing(filing.id))
        events = [audit.event for audit in run_async(repo.list_audit_logs(filing.id))]

        self.assertEqual(review.status, FTWilliamsReviewStatus.CURRENT_QUERIED)
        self.assertTrue(review.active_failure)
        self.assertIn("invalid field", review.active_failure_reason)
        self.assertIsNotNone(review.active_failure_client_error)
        self.assertEqual(updated_filing.status, FilingStatus.FAILED)
        self.assertIn("invalid field", updated_filing.error_message)
        self.assertNotIn("FTWILLIAMS_UPDATE_FAILURE_CLEARED", events)

    def test_prepare_review_does_not_clear_failed_update_just_because_filing_is_approved(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(repo.update_filing(filing.id, {"status": FilingStatus.APPROVED, "error_message": "Old FTW locked error"}))
        run_async(
            repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    status=FTWilliamsReviewStatus.UPDATE_FAILED,
                    configured=True,
                    current_query_sent=True,
                    current_query_success=True,
                    error_message="Old FTW locked error",
                )
            )
        )

        def field(rule_key: str, label: str, value: str, form_type: FormType, document_type: DocumentType) -> ExtractedField:
            return ExtractedField(
                filing_id=filing.id,
                source_field_name=label,
                normalized_field_name=label.lower(),
                mapped_rule_key=rule_key,
                mapped_label=label,
                form_type=form_type,
                source_document_type=document_type,
                priority=FieldPriority.HIGH,
                value=value,
                proposed_value=value,
            )

        run_async(
            repo.add_fields(
                [
                    field("form_5500_part_i_1e_plan_sponsor_ein", "1e. Plan Sponsor EIN", "73-1185740", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_1b_plan_number_pn", "1b. Plan Number (PN)", "501", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_7_plan_year_ending_date", "7. Plan Year Ending Date", "09/30/2025", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("schedule_a_part_i_1a_name_of_insurance_company", "1a. Name of Insurance Company", "UnitedHealthcare", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1d_contract_policy_number", "1d. Contract/Policy Number", "1246876", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_3a_name_of_agent_broker_person", "3a. Name of Agent/Broker/Person", "New Broker Name", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                ]
            )
        )

        review = run_async(FTWilliamsReviewService(FakeFTWilliamsCurrentTagService()).prepare_review(filing.id, send_queries=True))
        updated_filing = run_async(repo.get_filing(filing.id))

        self.assertEqual(review.status, FTWilliamsReviewStatus.CURRENT_QUERIED)
        self.assertIsNone(review.client_error)
        self.assertTrue(review.active_failure)
        self.assertEqual(updated_filing.status, FilingStatus.APPROVED)
        self.assertEqual(updated_filing.error_message, "Old FTW locked error")

    def test_schedule_candidates_merge_weak_sequence_with_rich_fallback_data(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))

        def field(rule_key: str, label: str, value: str, form_type: FormType, document_type: DocumentType) -> ExtractedField:
            return ExtractedField(
                filing_id=filing.id,
                source_field_name=label,
                normalized_field_name=label.lower(),
                mapped_rule_key=rule_key,
                mapped_label=label,
                form_type=form_type,
                source_document_type=document_type,
                priority=FieldPriority.HIGH,
                value=value,
                proposed_value=value,
            )

        run_async(
            repo.add_fields(
                [
                    field("form_5500_part_i_1e_plan_sponsor_ein", "1e. Plan Sponsor EIN", "95-1994337", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_1b_plan_number_pn", "1b. Plan Number (PN)", "501", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_7_plan_year_ending_date", "7. Plan Year Ending Date", "11/30/2025", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("schedule_a_part_i_1a_name_of_insurance_company", "1a. Name of Insurance Company", "METROPOLITAN LIFE INSURANCE COMPANY", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1b_insurance_carrier_ein", "1b. EIN", "13-5581829", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1d_contract_policy_number", "1d. Contract/Policy Number", "5393054", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                ]
            )
        )

        service = FakeFTWilliamsWeakScheduleService()
        review = run_async(FTWilliamsReviewService(service).prepare_review(filing.id, send_queries=True))
        seq4 = next(candidate for candidate in review.schedule_a_candidates if candidate["ftw_seq_no"] == "4")

        self.assertEqual(review.schedule_a_match["ftw_seq_no"], "4")
        self.assertEqual(seq4["carrier"], "Metropolitan Life Insurance Company")
        self.assertEqual(seq4["contract"], "5393054")
        self.assertTrue(seq4["has_current_data"])
        self.assertGreater(seq4["score"], 0)

    def test_schedule_candidates_do_not_overwrite_explicit_sequence_with_conflicting_fallback(self):
        class ConflictingFallbackFTWilliamsService(FakeFTWilliamsService):
            async def run_query(self, payload):
                if payload.operation != "query_schedule_a":
                    return await super().run_query(payload)

                self.calls.append(payload)
                request_xml = self.mask_key_id(self.build_request_xml(payload))

                def success(sequence: str, values: dict[str, str]) -> FTWilliamsQueryResponse:
                    return FTWilliamsQueryResponse(
                        operation=payload.operation,
                        configured=True,
                        sent=True,
                        request_xml=request_xml,
                        success=True,
                        raw_response="<ftwLinkResponse />",
                        statuses=[
                            FTWilliamsStatusItem(
                                type="ScheduleA",
                                error_code="0",
                                ftw_seq_no=sequence,
                                query_results=values,
                            )
                        ],
                    )

                if payload.ftw_seq_no == "1":
                    return success(
                        "1",
                        {
                            "ScheduleDesc": "CIGNA",
                            "InsCarrierName": "CIGNA HEALTH AND LIFE INSURANCE COMPANY",
                            "InsCarrierEIN": "59-1031071",
                            "InsCarrierNAICCode": "67369",
                            "InsContractNum": "00626686",
                            "InsPolicyFromDate": "01/01/2025",
                            "InsPolicyToDate": "12/31/2025",
                        },
                    )
                if payload.ftw_seq_no == "2":
                    return success(
                        "2",
                        {
                            "ScheduleDesc": "GUARDIAN",
                            "InsCarrierName": "GUARDIAN",
                            "InsCarrierEIN": "13-5123390",
                            "InsCarrierNAICCode": "64246",
                            "InsContractNum": "00564017",
                            "InsPolicyFromDate": "01/01/2025",
                            "InsPolicyToDate": "12/31/2025",
                        },
                    )
                if not payload.ftw_seq_no:
                    return success(
                        "2",
                        {
                            "ScheduleDesc": "CIGNA",
                            "InsCarrierName": "CIGNA HEALTH AND LIFE INSURANCE COMPANY",
                            "InsCarrierEIN": "59-1031071",
                            "InsCarrierNAICCode": "67369",
                            "InsContractNum": "00626686",
                            "InsPolicyFromDate": "01/01/2025",
                            "InsPolicyToDate": "12/31/2025",
                            "Name1": "PREFERRED BENEFITS INC",
                            "FeesPdAmt1": "116838",
                            "FeesPdText1": "FEES",
                        },
                    )
                return FTWilliamsQueryResponse(
                    operation=payload.operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    success=False,
                    raw_response="<ftwLinkResponse />",
                    statuses=[FTWilliamsStatusItem(type="DOLScheduleAData", error_code="59")],
                )

        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))

        def field(rule_key: str, label: str, value: str, form_type: FormType, document_type: DocumentType) -> ExtractedField:
            return ExtractedField(
                filing_id=filing.id,
                source_field_name=label,
                normalized_field_name=label.lower(),
                mapped_rule_key=rule_key,
                mapped_label=label,
                form_type=form_type,
                source_document_type=document_type,
                priority=FieldPriority.HIGH,
                value=value,
                proposed_value=value,
            )

        run_async(
            repo.add_fields(
                [
                    field("form_5500_part_i_1e_plan_sponsor_ein", "1e. Plan Sponsor EIN", "22-1629238", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_1b_plan_number_pn", "1b. Plan Number (PN)", "501", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_7_plan_year_ending_date", "7. Plan Year Ending Date", "12/31/2025", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("schedule_a_part_i_1a_name_of_insurance_company", "1a. Name of Insurance Company", "CIGNA HEALTH AND LIFE INSURANCE COMPANY", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1b_insurance_carrier_ein", "1b. EIN", "59-1031071", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1c_naic_code", "1c. NAIC", "67369", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1d_contract_policy_number", "1d. Contract/Policy Number", "00626686", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                ]
            )
        )

        review = run_async(FTWilliamsReviewService(ConflictingFallbackFTWilliamsService()).prepare_review(filing.id, send_queries=True))
        candidates = {candidate["ftw_seq_no"]: candidate for candidate in review.schedule_a_candidates}

        self.assertEqual(candidates["1"]["carrier"], "CIGNA HEALTH AND LIFE INSURANCE COMPANY")
        self.assertEqual(candidates["2"]["carrier"], "GUARDIAN")
        self.assertEqual(review.schedule_a_match["ftw_seq_no"], "1")

    def test_combined_fallback_never_overwrites_an_explicit_schedule_sequence(self):
        service = FTWilliamsReviewService()
        explicit = FTWilliamsStatusItem(
            type="ScheduleA",
            error_code="0",
            ftw_seq_no="2",
            query_results={
                "ScheduleDesc": "GUARDIAN",
                "InsCarrierName": "GUARDIAN",
                "InsContractNum": "00564017",
            },
        )
        combined = FTWilliamsStatusItem(
            type="ScheduleA",
            error_code="0",
            ftw_seq_no="2",
            query_result_record_count=2,
            query_results={
                "ScheduleDesc": "CIGNA",
                "InsCarrierName": "CIGNA HEALTH AND LIFE INSURANCE COMPANY",
                "InsContractNum": "00626686",
                "Name1": "PREFERRED BENEFITS INC",
                "FeesPdAmt1": "116838",
            },
        )

        merged = service._merge_schedule_statuses([explicit], [combined])

        self.assertEqual(merged, [explicit])

    def test_manual_schedule_selection_survives_repeated_current_data_refreshes(self):
        class TiedScheduleFTWilliamsService(FakeFTWilliamsService):
            async def run_query(self, payload):
                if payload.operation != "query_schedule_a":
                    return await super().run_query(payload)

                self.calls.append(payload)
                request_xml = self.mask_key_id(self.build_request_xml(payload))
                if payload.ftw_seq_no in {"1", "2"}:
                    return FTWilliamsQueryResponse(
                        operation=payload.operation,
                        configured=True,
                        sent=True,
                        request_xml=request_xml,
                        success=True,
                        raw_response="<ftwLinkResponse />",
                        statuses=[
                            FTWilliamsStatusItem(
                                type="ScheduleA",
                                error_code="0",
                                ftw_seq_no=payload.ftw_seq_no,
                                query_results={
                                    "ScheduleDesc": f"CIGNA-{payload.ftw_seq_no}",
                                    "InsCarrierName": "CIGNA HEALTH AND LIFE INSURANCE COMPANY",
                                    "InsCarrierEIN": "59-1031071",
                                    "InsCarrierNAICCode": "99999",
                                    "InsContractNum": "00999999",
                                },
                            )
                        ],
                    )
                return FTWilliamsQueryResponse(
                    operation=payload.operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    success=False,
                    raw_response="<ftwLinkResponse />",
                    statuses=[FTWilliamsStatusItem(type="DOLScheduleAData", error_code="59")],
                )

        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))

        def field(rule_key: str, label: str, value: str, form_type: FormType, document_type: DocumentType) -> ExtractedField:
            return ExtractedField(
                filing_id=filing.id,
                source_field_name=label,
                normalized_field_name=label.lower(),
                mapped_rule_key=rule_key,
                mapped_label=label,
                form_type=form_type,
                source_document_type=document_type,
                priority=FieldPriority.HIGH,
                value=value,
                proposed_value=value,
            )

        run_async(
            repo.add_fields(
                [
                    field("form_5500_part_i_1e_plan_sponsor_ein", "1e. Plan Sponsor EIN", "22-1629238", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_1b_plan_number_pn", "1b. Plan Number (PN)", "501", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_7_plan_year_ending_date", "7. Plan Year Ending Date", "12/31/2025", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("schedule_a_part_i_1a_name_of_insurance_company", "1a. Name of Insurance Company", "CIGNA HEALTH AND LIFE INSURANCE COMPANY", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1b_insurance_carrier_ein", "1b. EIN", "59-1031071", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1c_naic_code", "1c. NAIC", "67369", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1d_contract_policy_number", "1d. Contract/Policy Number", "00626686", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                ]
            )
        )

        service = FTWilliamsReviewService(TiedScheduleFTWilliamsService())
        initial = run_async(service.prepare_review(filing.id, send_queries=True))
        selected = run_async(
            service.select_schedule_a_match(
                filing.id,
                FTWilliamsScheduleAMatchRequest(ftw_seq_no="2"),
            )
        )
        first_refresh = run_async(service.prepare_review(filing.id, send_queries=True))
        second_refresh = run_async(service.prepare_review(filing.id, send_queries=True))

        self.assertIsNone(initial.schedule_a_match)
        self.assertEqual(selected.schedule_a_match["source"], "MANUAL")
        self.assertEqual(first_refresh.schedule_a_match["ftw_seq_no"], "2")
        self.assertEqual(first_refresh.schedule_a_match["source"], "MANUAL")
        self.assertEqual(first_refresh.schedule_a_current_values["InsCarrierNAICCode"], "99999")
        self.assertEqual(first_refresh.schedule_a_current_values["InsContractNum"], "00999999")
        self.assertEqual(second_refresh.schedule_a_match["ftw_seq_no"], "2")
        self.assertEqual(second_refresh.schedule_a_match["source"], "MANUAL")
        self.assertEqual(second_refresh.schedule_a_current_values["InsCarrierNAICCode"], "99999")
        self.assertEqual(second_refresh.schedule_a_current_values["InsContractNum"], "00999999")

    def test_single_schedule_candidate_with_conflicting_identity_requires_manual_selection(self):
        service = FTWilliamsReviewService()
        fields = [
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1a. Name of Insurance Company",
                normalized_field_name="carrier_name",
                mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                mapped_label="1a. Name of Insurance Company",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="Kaiser Foundation Health Plan",
                proposed_value="Kaiser Foundation Health Plan",
            ),
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1b. EIN",
                normalized_field_name="carrier_ein",
                mapped_rule_key="schedule_a_part_i_1b_insurance_carrier_ein",
                mapped_label="1b. EIN",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="94-1340523",
                proposed_value="94-1340523",
            ),
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1d. Contract/Policy Number",
                normalized_field_name="contract",
                mapped_rule_key="schedule_a_part_i_1d_contract_policy_number",
                mapped_label="1d. Contract/Policy Number",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="607863",
                proposed_value="607863",
            ),
        ]
        wrong_current_schedule = FTWilliamsStatusItem(
            type="ScheduleA",
            error_code="0",
            ftw_seq_no="1",
            query_results={
                "InsCarrierName": "Principal Life Insurance Company",
                "InsCarrierEIN": "42-0127290",
                "InsContractNum": "1149477",
                "InsPolicyFromDate": "01/01/2024",
                "InsPolicyToDate": "12/31/2024",
            },
        )

        matched = service._match_schedule_a_status(fields, [wrong_current_schedule])

        self.assertIsNone(matched)

    def test_schedule_match_uses_carrier_ein_and_naic_when_contract_is_missing(self):
        service = FTWilliamsReviewService()
        fields = [
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1a. Name of Insurance Company",
                normalized_field_name="carrier_name",
                mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                mapped_label="1a. Name of Insurance Company",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="UnitedHealthcare Insurance Company",
                proposed_value="UnitedHealthcare Insurance Company",
            ),
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1b. EIN",
                normalized_field_name="carrier_ein",
                mapped_rule_key="schedule_a_part_i_1b_insurance_carrier_ein",
                mapped_label="1b. EIN",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="36-2739571",
                proposed_value="36-2739571",
            ),
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1c. NAIC Code",
                normalized_field_name="naic",
                mapped_rule_key="schedule_a_part_i_1c_naic_code",
                mapped_label="1c. NAIC Code",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="000-79413",
                proposed_value="000-79413",
            ),
        ]
        statuses = [
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no="1",
                query_results={
                    "InsCarrierName": "UnitedHealthcare Insurance Company",
                    "InsContractNum": "926002",
                },
            ),
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no="2",
                query_results={
                    "InsCarrierName": "UnitedHealthcare Insurance Company",
                    "InsCarrierEIN": "36-2739571",
                    "InsCarrierNAICCode": "79413",
                },
            ),
        ]

        match = service._match_schedule_a_status(fields, statuses)
        candidates = service._schedule_candidate_payloads(statuses, fields)

        self.assertIsNotNone(match)
        self.assertEqual(match.ftw_seq_no, "2")
        self.assertEqual(candidates[0]["ftw_seq_no"], "2")
        self.assertIn("Carrier EIN", candidates[0]["match_reasons"])
        self.assertIn("NAIC", candidates[0]["match_reasons"])

    def test_schedule_match_tolerates_ftw_dropping_internal_contract_leading_zero(self):
        service = FTWilliamsReviewService()
        fields = [
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1b. EIN",
                normalized_field_name="carrier_ein",
                mapped_rule_key="schedule_a_part_i_1b_insurance_carrier_ein",
                mapped_label="1b. EIN",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="23-1503749",
                proposed_value="23-1503749",
            ),
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1c. NAIC Code",
                normalized_field_name="naic",
                mapped_rule_key="schedule_a_part_i_1c_naic_code",
                mapped_label="1c. NAIC Code",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="65498",
                proposed_value="65498",
            ),
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1d. Contract/Policy Number",
                normalized_field_name="contract",
                mapped_rule_key="schedule_a_part_i_1d_contract_policy_number",
                mapped_label="1d. Contract/Policy Number",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="LK 0751856",
                proposed_value="LK 0751856",
            ),
        ]
        statuses = [
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no=seq,
                query_results={
                    "InsCarrierEIN": "23-1503749",
                    "InsCarrierNAICCode": "65498",
                    "InsContractNum": contract,
                },
            )
            for seq, contract in [
                ("2", "FLX966853"),
                ("3", "OK 968358"),
                ("4", "OK 968359"),
                ("5", "LK 751856"),
            ]
        ]

        match = service._match_schedule_a_status(fields, statuses)
        candidates = service._schedule_candidate_payloads(statuses, fields)

        self.assertIsNotNone(match)
        self.assertEqual(match.ftw_seq_no, "5")
        self.assertEqual(candidates[0]["ftw_seq_no"], "5")
        self.assertIn("Contract", candidates[0]["match_reasons"])

    def test_fresh_query_replaces_stale_preferred_schedule_a_with_stronger_identity_match(self):
        service = FTWilliamsReviewService()
        fields = [
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1b. EIN",
                normalized_field_name="carrier_ein",
                mapped_rule_key="schedule_a_part_i_1b_insurance_carrier_ein",
                mapped_label="1b. EIN",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="13-3550228",
                proposed_value="13-3550228",
            ),
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1d. Contract/Policy Number",
                normalized_field_name="contract",
                mapped_rule_key="schedule_a_part_i_1d_contract_policy_number",
                mapped_label="1d. Contract/Policy Number",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="0230010",
                proposed_value="0230010",
            ),
        ]
        statuses = [
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no="4",
                query_results={"InsCarrierEIN": "13-3550228", "InsContractNum": "0229588"},
            ),
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no="7",
                query_results={"InsCarrierEIN": "13-3550228", "InsContractNum": "0230010"},
            ),
        ]

        match = service._match_schedule_a_status(fields, statuses, preferred_ftw_seq_no="4")

        self.assertIsNotNone(match)
        self.assertEqual(match.ftw_seq_no, "7")

    def test_exact_contract_beats_a_leading_zero_normalization_collision(self):
        service = FTWilliamsReviewService()
        fields = [
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1a. Name of Insurance Company",
                normalized_field_name="carrier_name",
                mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                mapped_label="1a. Name of Insurance Company",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="CIGNA HEALTH AND LIFE INSURANCE COMPANY",
                proposed_value="CIGNA HEALTH AND LIFE INSURANCE COMPANY",
            ),
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1b. EIN",
                normalized_field_name="carrier_ein",
                mapped_rule_key="schedule_a_part_i_1b_insurance_carrier_ein",
                mapped_label="1b. EIN",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="59-1031071",
                proposed_value="59-1031071",
            ),
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1c. NAIC",
                normalized_field_name="naic",
                mapped_rule_key="schedule_a_part_i_1c_naic_code",
                mapped_label="1c. NAIC",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="67369",
                proposed_value="67369",
            ),
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1d. Contract/Policy Number",
                normalized_field_name="contract",
                mapped_rule_key="schedule_a_part_i_1d_contract_policy_number",
                mapped_label="1d. Contract/Policy Number",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="0656053",
                proposed_value="0656053",
            ),
        ]
        statuses = [
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no="7",
                query_results={
                    "ScheduleDesc": "CIGNA",
                    "InsCarrierName": "CIGNA HEALTH AND LIFE INSURANCE COMPANY",
                    "InsCarrierEIN": "59-1031071",
                    "InsCarrierNAICCode": "67369",
                    "InsContractNum": "00656053",
                },
            ),
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no="8",
                query_results={
                    "ScheduleDesc": "DENTAL",
                    "InsCarrierName": "CIGNA",
                    "InsCarrierEIN": "59-1031071",
                    "InsCarrierNAICCode": "67369",
                    "InsContractNum": "0656053",
                },
            ),
        ]

        match = service._match_schedule_a_status(fields, statuses)

        self.assertIsNotNone(match)
        self.assertEqual(match.ftw_seq_no, "8")

    def test_locked_ftw_filing_is_reported_and_blocked_before_update_send(self):
        class LockedFTWilliamsService(FakeFTWilliamsService):
            def __init__(self):
                super().__init__()
                self.send_calls = 0
                self.locked = True

            async def run_query(self, payload):
                if payload.operation != "query_5500":
                    return await super().run_query(payload)
                self.calls.append(payload)
                return FTWilliamsQueryResponse(
                    operation=payload.operation,
                    configured=True,
                    sent=True,
                    request_xml=self.mask_key_id(self.build_request_xml(payload)),
                    success=True,
                    raw_response="<ftwLinkResponse />",
                    statuses=[
                        FTWilliamsStatusItem(
                            type="5500",
                            error_code="0",
                            ftw_customer_id=payload.ftw_customer_id,
                            ftw_plan_id=payload.ftw_plan_id,
                            query_results={
                                "PlanName": "Locked Test Plan",
                                "LockedStatus": "Locked" if self.locked else "Unlocked",
                                "SignedStatus": "Signed" if self.locked else "Not Signed",
                                "FilingStatus": "Accepted" if self.locked else "Draft",
                            },
                        )
                    ],
                )

            async def send_xml(self, operation, request_xml):
                self.send_calls += 1
                return FTWilliamsQueryResponse(
                    operation=operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    success=True,
                    raw_response="<ftwLinkResponse />",
                    statuses=[FTWilliamsStatusItem(type=operation, error_code="0")],
                )

        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))

        def field(rule_key: str, label: str, value: str, form_type: FormType, document_type: DocumentType) -> ExtractedField:
            return ExtractedField(
                filing_id=filing.id,
                source_field_name=label,
                normalized_field_name=label.lower(),
                mapped_rule_key=rule_key,
                mapped_label=label,
                form_type=form_type,
                source_document_type=document_type,
                priority=FieldPriority.HIGH,
                value=value,
                proposed_value=value,
            )

        run_async(
            repo.add_fields(
                [
                    field("form_5500_part_i_1e_plan_sponsor_ein", "1e. Plan Sponsor EIN", "73-0759701", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_1b_plan_number_pn", "1b. Plan Number", "501", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_7_plan_year_ending_date", "7. Plan Year Ending Date", "12/31/2025", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_ii_14_active_participants_at_end", "14. Active Participants at End", "101", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("schedule_a_part_i_1a_name_of_insurance_company", "1a. Name of Insurance Company", "BlueCross BlueShield of Oklahoma", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1b_insurance_carrier_ein", "1b. Insurance Carrier EIN", "36-1236610", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1d_contract_policy_number", "1d. Contract", "Y00979", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                ]
            )
        )
        fake_ftw = LockedFTWilliamsService()
        service = FTWilliamsReviewService(fake_ftw)

        review = run_async(service.prepare_review(filing.id, send_queries=True))

        self.assertFalse(review.ftw_editable)
        self.assertEqual(review.ftw_locked_status, "Locked")
        self.assertEqual(review.client_error.code, "FTW_LOCKED")

        with self.assertRaisesRegex(ValueError, "locked"):
            run_async(service.approve_and_update(filing.id, send_to_ftw=True, refresh_current_before_update=True))
        self.assertEqual(fake_ftw.send_calls, 0)

        fake_ftw.locked = False
        refreshed = run_async(service.prepare_review(filing.id, send_queries=True))
        self.assertTrue(refreshed.ftw_editable)
        self.assertEqual(refreshed.ftw_locked_status, "Unlocked")
        self.assertNotEqual(refreshed.client_error.code if refreshed.client_error else None, "FTW_LOCKED")

    def test_successful_ftw_update_is_read_back_and_verified(self):
        class VerifyingFTWilliamsService(FakeFTWilliamsService):
            def __init__(
                self,
                *,
                reflect_updates: bool = True,
                baseline_edit_checks_success: bool = True,
                final_edit_checks_success: bool = True,
            ):
                super().__init__()
                self.updated = False
                self.reflect_updates = reflect_updates
                self.baseline_edit_checks_success = baseline_edit_checks_success
                self.final_edit_checks_success = final_edit_checks_success
                self.edit_check_calls = 0

            async def run_query(self, payload):
                if payload.operation == "edit_checks_5500":
                    self.calls.append(payload)
                    self.edit_check_calls += 1
                    success = (
                        self.baseline_edit_checks_success
                        if self.edit_check_calls == 1
                        else self.final_edit_checks_success
                    )
                    return FTWilliamsQueryResponse(
                        operation=payload.operation,
                        configured=True,
                        sent=True,
                        request_xml="<ftwLink><KeyID>***</KeyID><EditChecks5500 /></ftwLink>",
                        http_status=200,
                        success=success,
                        raw_response="<ftwLinkResponse><Status><ErrorCode>0</ErrorCode></Status></ftwLinkResponse>",
                        statuses=[
                            FTWilliamsStatusItem(
                                type="EditChecks5500",
                                error_code="0",
                                error_desc=None,
                                query_results=(
                                    {}
                                    if success
                                    else {
                                        "Status": "NOT-OK",
                                        "SeqNo": "2",
                                        "ScheduleDesc": "Y00979",
                                        "FW-410": "Warning:::Part III, Line 10a Total premiums may not be blank.",
                                    }
                                ),
                            )
                        ],
                    )
                response = await super().run_query(payload)
                if payload.operation == "query_5500" and response.statuses:
                    response.statuses[0].query_results.update(
                        {
                            "TotActivePartcpCnt": "101" if self.updated and self.reflect_updates else "100",
                            "SponsDfePlanNum": "501",
                            "PlanYearEndDate": "12/31/2025",
                            "LockedStatus": "Unlocked",
                        }
                    )
                if payload.operation == "query_schedule_a" and payload.ftw_seq_no == "2" and response.statuses:
                    response.statuses[0].query_results["WlfrTotChargesPaidAmt"] = "100" if self.updated and self.reflect_updates else "90"
                return response

            async def send_xml(self, operation, request_xml):
                self.updated = True
                return FTWilliamsQueryResponse(
                    operation=operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    success=True,
                    raw_response="<ftwLinkResponse><Status><ErrorCode>0</ErrorCode></Status></ftwLinkResponse>",
                    statuses=[FTWilliamsStatusItem(type=operation, error_code="0")],
                )

            async def generate_dol_document(self, **_kwargs):
                return (
                    FTWilliamsQueryResponse(
                        operation="generate_dol_document",
                        configured=True,
                        sent=True,
                        request_xml="<ftwLink><KeyID>***</KeyID><GenerateDocument /></ftwLink>",
                        http_status=200,
                        success=True,
                        raw_response="<ftwLinkResponse><Status><ErrorCode>0</ErrorCode></Status></ftwLinkResponse>",
                        statuses=[FTWilliamsStatusItem(type="Document", error_code="0")],
                    ),
                    b"%PDF-1.4 verified schedule a",
                )

        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))

        def field(rule_key: str, label: str, value: str, form_type: FormType, document_type: DocumentType) -> ExtractedField:
            return ExtractedField(
                filing_id=filing.id,
                source_field_name=label,
                normalized_field_name=label.lower(),
                mapped_rule_key=rule_key,
                mapped_label=label,
                form_type=form_type,
                source_document_type=document_type,
                priority=FieldPriority.HIGH,
                value=value,
                proposed_value=value,
            )

        run_async(
            repo.add_fields(
                [
                    field("form_5500_part_i_1e_plan_sponsor_ein", "1e. Plan Sponsor EIN", "73-0759701", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_1b_plan_number_pn", "1b. Plan Number", "501", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_7_plan_year_ending_date", "7. Plan Year Ending Date", "12/31/2025", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_ii_14_active_participants_at_end", "14. Active Participants at End", "101", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("schedule_a_part_i_1a_name_of_insurance_company", "1a. Name of Insurance Company", "BlueCross BlueShield of Oklahoma", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1b_insurance_carrier_ein", "1b. Insurance Carrier EIN", "36-1236610", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1d_contract_policy_number", "1d. Contract", "Y00979", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier", "10a. Total premiums", "100", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                ]
            )
        )

        verifying_ftw = VerifyingFTWilliamsService()
        settings = get_settings()
        with (
            patch.object(settings, "ftw_pdf_audit_enabled", True),
            patch(
                "app.services.ftwilliams_review.StorageService.save_pdf",
                return_value={"key": "audit/test.pdf", "bucket": "audit-bucket", "uploaded": True},
            ),
        ):
            review = run_async(
                FTWilliamsReviewService(verifying_ftw).approve_and_update(
                    filing.id,
                    send_to_ftw=True,
                    refresh_current_before_update=True,
                    run_edit_checks=True,
                )
            )

        self.assertEqual(review.status, FTWilliamsReviewStatus.UPDATE_SENT, review.update_verification_mismatches)
        self.assertTrue(review.update_verification_attempted)
        self.assertTrue(review.update_verification_success)
        self.assertEqual(review.update_verification_mismatches, [])
        self.assertEqual(review.update_attempted_count, 2)
        self.assertEqual(review.update_confirmed_count, 2)
        self.assertEqual(review.update_remaining_count, 0)
        self.assertEqual(len(review.update_results), 2)
        self.assertTrue(all(item["status"] == "VERIFIED" for item in review.update_results))
        self.assertTrue(all(item.get("label") and item.get("sent_value") for item in review.update_results))
        self.assertTrue(review.query_access_verified)
        self.assertEqual(review.update_access_status, "GRANTED")
        self.assertTrue(review.schema_validation_results)
        self.assertTrue(all(result.valid for result in review.schema_validation_results))
        self.assertTrue(review.edit_check_baseline_success)
        self.assertTrue(review.edit_check_final_success)
        self.assertEqual(review.audit_pdf_status, "AVAILABLE")
        self.assertEqual(review.audit_pdf_key, "audit/test.pdf")
        self.assertEqual(len(review.audit_pdf_sha256 or ""), 64)

        clear_ftw_current_snapshot_cache()
        baseline_warning_ftw = VerifyingFTWilliamsService(
            baseline_edit_checks_success=False,
        )
        baseline_warning = run_async(
            FTWilliamsReviewService(baseline_warning_ftw).approve_and_update(
                filing.id,
                send_to_ftw=True,
                refresh_current_before_update=True,
                run_edit_checks=True,
            )
        )

        self.assertTrue(baseline_warning_ftw.updated)
        self.assertEqual(baseline_warning.status, FTWilliamsReviewStatus.UPDATE_SENT)
        self.assertFalse(baseline_warning.edit_check_baseline_success)
        self.assertTrue(baseline_warning.edit_check_final_success)
        self.assertEqual(baseline_warning.edit_check_validation_status, "RESOLVED")
        self.assertTrue(baseline_warning.update_verification_success)
        self.assertFalse(baseline_warning.active_failure)

        clear_ftw_current_snapshot_cache()
        existing_warning_ftw = VerifyingFTWilliamsService(
            baseline_edit_checks_success=False,
            final_edit_checks_success=False,
        )
        existing_warning = run_async(
            FTWilliamsReviewService(existing_warning_ftw).approve_and_update(
                filing.id,
                send_to_ftw=True,
                refresh_current_before_update=True,
                run_edit_checks=True,
            )
        )

        self.assertTrue(existing_warning_ftw.updated)
        self.assertEqual(existing_warning.status, FTWilliamsReviewStatus.UPDATE_SENT)
        self.assertEqual(existing_warning.edit_check_validation_status, "EXISTING_ISSUES")
        self.assertEqual(existing_warning.edit_check_new_issues, [])
        self.assertEqual(existing_warning.edit_check_resolved_issues, [])
        self.assertTrue(existing_warning.update_verification_success)
        self.assertFalse(existing_warning.active_failure)

        clear_ftw_current_snapshot_cache()
        mismatched = run_async(
            FTWilliamsReviewService(VerifyingFTWilliamsService(reflect_updates=False)).approve_and_update(
                filing.id,
                send_to_ftw=True,
                refresh_current_before_update=True,
            )
        )

        self.assertEqual(mismatched.status, FTWilliamsReviewStatus.UPDATE_FAILED)
        self.assertTrue(mismatched.update_verification_attempted)
        self.assertFalse(mismatched.update_verification_success)
        self.assertGreater(len(mismatched.update_verification_mismatches), 0)
        self.assertTrue(
            any(item["status"] == "NEEDS_CORRECTION" for item in mismatched.update_results),
            mismatched.update_results,
        )
        self.assertIn("read-back verification", mismatched.error_message or "")

        clear_ftw_current_snapshot_cache()
        final_checks_failed = run_async(
            FTWilliamsReviewService(
                VerifyingFTWilliamsService(final_edit_checks_success=False)
            ).approve_and_update(
                filing.id,
                send_to_ftw=True,
                refresh_current_before_update=True,
                run_edit_checks=True,
            )
        )

        self.assertEqual(final_checks_failed.status, FTWilliamsReviewStatus.UPDATE_SENT)
        self.assertTrue(final_checks_failed.update_verification_success)
        self.assertFalse(final_checks_failed.edit_check_final_success)
        self.assertEqual(len(final_checks_failed.edit_check_final_issues), 1)
        self.assertEqual(final_checks_failed.edit_check_final_issues[0].code, "FW-410")
        self.assertEqual(final_checks_failed.edit_check_final_issues[0].schedule_seq_no, "2")
        self.assertEqual(final_checks_failed.edit_check_validation_status, "NEW_ISSUES")
        self.assertEqual(len(final_checks_failed.edit_check_new_issues), 1)
        self.assertIsNone(final_checks_failed.client_error)
        self.assertFalse(final_checks_failed.active_failure)
        self.assertIsNone(final_checks_failed.error_message)

    def test_ambiguous_ftw_update_preserves_last_valid_schedule_snapshot(self):
        class AmbiguousUpdateFTWilliamsService(FakeFTWilliamsService):
            def __init__(self):
                super().__init__()
                self.events = []
                self.update_started = False

            async def run_query(self, payload):
                self.events.append(f"query:{payload.operation}")
                if not self.update_started:
                    response = await super().run_query(payload)
                    if payload.operation == "query_5500" and response.statuses:
                        response.statuses[0].query_results.update(
                            {
                                "TotActivePartcpCnt": "100",
                                "SponsDfePlanNum": "501",
                                "PlanYearEndDate": "12/31/2025",
                                "LockedStatus": "Unlocked",
                            }
                        )
                    if payload.operation == "query_schedule_a" and payload.ftw_seq_no == "2" and response.statuses:
                        response.statuses[0].query_results["WlfrTotChargesPaidAmt"] = "90"
                    return response
                if payload.operation == "query_plan":
                    return await super().run_query(payload)
                request_xml = self.mask_key_id(self.build_request_xml(payload))
                return FTWilliamsQueryResponse(
                    operation=payload.operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    http_status=200,
                    success=False,
                    raw_response="",
                    statuses=[FTWilliamsStatusItem(type=payload.operation, error_code="PARSE_ERROR", error_desc="no element found")],
                )

            async def send_xml(self, operation, request_xml):
                self.events.append(f"send:{operation}")
                self.update_started = True
                return FTWilliamsQueryResponse(
                    operation=operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    http_status=200,
                    success=False,
                    raw_response="",
                    statuses=[FTWilliamsStatusItem(type=operation, error_code="PARSE_ERROR", error_desc="no element found")],
                )

        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))

        def field(rule_key: str, label: str, value: str, form_type: FormType, document_type: DocumentType) -> ExtractedField:
            return ExtractedField(
                filing_id=filing.id,
                source_field_name=label,
                normalized_field_name=label.lower(),
                mapped_rule_key=rule_key,
                mapped_label=label,
                form_type=form_type,
                source_document_type=document_type,
                priority=FieldPriority.HIGH,
                value=value,
                proposed_value=value,
            )

        run_async(
            repo.add_fields(
                [
                    field("form_5500_part_i_1e_plan_sponsor_ein", "1e. Plan Sponsor EIN", "73-0759701", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_1b_plan_number_pn", "1b. Plan Number", "501", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_7_plan_year_ending_date", "7. Plan Year Ending Date", "12/31/2025", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_ii_14_active_participants_at_end", "14. Active Participants at End", "101", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("schedule_a_part_i_1a_name_of_insurance_company", "1a. Name of Insurance Company", "BlueCross BlueShield of Oklahoma", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1b_insurance_carrier_ein", "1b. Insurance Carrier EIN", "36-1236610", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1d_contract_policy_number", "1d. Contract", "Y00979", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier", "10a. Total premiums", "100", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                ]
            )
        )

        fake_ftw = AmbiguousUpdateFTWilliamsService()
        service = FTWilliamsReviewService(fake_ftw)
        baseline = run_async(service.prepare_review(filing.id, send_queries=True))
        baseline_records = baseline.schedule_a_records
        baseline_candidates = baseline.schedule_a_candidates
        baseline_current = baseline.schedule_a_current_values
        fake_ftw.events.clear()

        result = run_async(
            service.approve_and_update(
                filing.id,
                send_to_ftw=True,
                refresh_current_before_update=True,
            )
        )

        first_send = next(index for index, event in enumerate(fake_ftw.events) if event.startswith("send:"))
        self.assertTrue(any(event == "query:query_5500" for event in fake_ftw.events[:first_send]))
        self.assertTrue(any(event == "query:query_schedule_a" for event in fake_ftw.events[:first_send]))
        self.assertFalse(any(event.startswith("query:") for event in fake_ftw.events[first_send + 1 :]))
        self.assertEqual(
            [event for event in fake_ftw.events if event.startswith("send:")],
            ["send:update_5500"],
        )
        self.assertEqual(result.status, FTWilliamsReviewStatus.UPDATE_UNKNOWN)
        self.assertFalse(result.current_query_success)
        self.assertTrue(result.current_year_exists)
        self.assertFalse(result.bring_forward_required)
        self.assertEqual(result.schedule_a_records, baseline_records)
        self.assertEqual(result.schedule_a_candidates, baseline_candidates)
        self.assertEqual(result.schedule_a_current_values, baseline_current)
        self.assertEqual(result.update_attempted_count, 1)
        self.assertEqual(result.update_confirmed_count, 0)
        self.assertEqual(result.update_remaining_count, 1)
        self.assertEqual(result.update_retry_count, 0)
        self.assertIn("could not be confirmed", result.error_message or "")
        self.assertTrue(result.active_failure)
        self.assertEqual(result.active_failure_client_error.code, "FTW_EMPTY_OR_MALFORMED_RESPONSE")
        self.assertEqual(result.update_diagnostics[0].operation, "update_5500")
        self.assertEqual(result.update_diagnostics[0].http_status, 200)
        self.assertEqual(result.update_diagnostics[0].outcome_code, "EMPTY_RESPONSE")
        self.assertFalse(result.update_diagnostics[0].response_received)

    def test_failed_current_refresh_preserves_last_valid_snapshot_without_bring_forward(self):
        class FailingRefreshFTWilliamsService(FakeFTWilliamsService):
            def __init__(self):
                super().__init__()
                self.fail_refresh = False
                self.send_count = 0

            async def run_query(self, payload):
                if not self.fail_refresh or payload.operation == "query_plan":
                    return await super().run_query(payload)
                request_xml = self.mask_key_id(self.build_request_xml(payload))
                return FTWilliamsQueryResponse(
                    operation=payload.operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    http_status=200,
                    success=False,
                    raw_response="",
                    statuses=[FTWilliamsStatusItem(type=payload.operation, error_code="PARSE_ERROR", error_desc="no element found")],
                )

            async def send_xml(self, operation, request_xml):
                self.send_count += 1
                return await super().send_xml(operation, request_xml)

        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="sponsor_ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="73-0759701",
                        proposed_value="73-0759701",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Plan Number",
                        normalized_field_name="plan_number",
                        mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                        mapped_label="1b. Plan Number",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="501",
                        proposed_value="501",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="7. Plan Year Ending Date",
                        normalized_field_name="plan_year_end",
                        mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                        mapped_label="7. Plan Year Ending Date",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="12/31/2025",
                        proposed_value="12/31/2025",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1a. Name of Insurance Company",
                        normalized_field_name="carrier",
                        mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                        mapped_label="1a. Name of Insurance Company",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="BlueCross BlueShield of Oklahoma",
                        proposed_value="BlueCross BlueShield of Oklahoma",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Insurance Carrier EIN",
                        normalized_field_name="carrier_ein",
                        mapped_rule_key="schedule_a_part_i_1b_insurance_carrier_ein",
                        mapped_label="1b. Insurance Carrier EIN",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="36-1236610",
                        proposed_value="36-1236610",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1d. Contract",
                        normalized_field_name="contract",
                        mapped_rule_key="schedule_a_part_i_1d_contract_policy_number",
                        mapped_label="1d. Contract",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="Y00979",
                        proposed_value="Y00979",
                    ),
                ]
            )
        )

        fake_ftw = FailingRefreshFTWilliamsService()
        service = FTWilliamsReviewService(fake_ftw)
        baseline = run_async(service.prepare_review(filing.id, send_queries=True))
        fake_ftw.fail_refresh = True

        refreshed = run_async(service.prepare_review(filing.id, send_queries=True))

        self.assertFalse(refreshed.current_query_success)
        self.assertFalse(refreshed.current_query_complete)
        self.assertTrue(refreshed.current_year_exists)
        self.assertFalse(refreshed.bring_forward_required)
        self.assertEqual(refreshed.schedule_a_match, baseline.schedule_a_match)
        self.assertEqual(refreshed.schedule_a_candidates, baseline.schedule_a_candidates)
        self.assertEqual(refreshed.schedule_a_records, baseline.schedule_a_records)
        self.assertEqual(refreshed.form_5500_current_values, baseline.form_5500_current_values)
        self.assertEqual(refreshed.schedule_a_current_values, baseline.schedule_a_current_values)
        self.assertIn("last valid", refreshed.error_message or "")
        with self.assertRaisesRegex(ValueError, "last valid|queried successfully"):
            run_async(service.approve_and_update(filing.id, send_to_ftw=True, refresh_current_before_update=True))
        self.assertEqual(fake_ftw.send_count, 0)

    def test_partial_form_success_is_reported_without_automatic_write_retry(self):
        class PartialUpdateFTWilliamsService(FakeFTWilliamsService):
            def __init__(self):
                super().__init__()
                self.form_updated = False
                self.schedule_updated = False
                self.schedule_send_count = 0
                self.send_operations = []

            async def run_query(self, payload):
                response = await super().run_query(payload)
                if payload.operation == "query_5500" and response.statuses:
                    response.statuses[0].query_results.update(
                        {
                            "TotActivePartcpCnt": "101" if self.form_updated else "100",
                            "SponsDfePlanNum": "501",
                            "PlanYearEndDate": "12/31/2025",
                            "LockedStatus": "Unlocked",
                        }
                    )
                if payload.operation == "query_schedule_a" and payload.ftw_seq_no == "2" and response.statuses:
                    response.statuses[0].query_results["WlfrTotChargesPaidAmt"] = "100" if self.schedule_updated else "90"
                return response

            async def send_xml(self, operation, request_xml):
                self.send_operations.append(operation)
                if operation == "update_5500":
                    self.form_updated = True
                    return FTWilliamsQueryResponse(
                        operation=operation,
                        configured=True,
                        sent=True,
                        request_xml=request_xml,
                        success=True,
                        raw_response="<ftwLinkResponse><Status><ErrorCode>0</ErrorCode></Status></ftwLinkResponse>",
                        statuses=[FTWilliamsStatusItem(type=operation, error_code="0")],
                    )
                self.schedule_send_count += 1
                if self.schedule_send_count == 1:
                    return FTWilliamsQueryResponse(
                        operation=operation,
                        configured=True,
                        sent=True,
                        request_xml=request_xml,
                        success=False,
                        error="DOLScheduleAData error 60: invalid field req: LegacyUnsupportedTag",
                        raw_response="<ftwLinkResponse><Status><ErrorCode>60</ErrorCode></Status></ftwLinkResponse>",
                        statuses=[FTWilliamsStatusItem(type=operation, error_code="60", error_desc="Invalid field")],
                    )
                self.schedule_updated = True
                return FTWilliamsQueryResponse(
                    operation=operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    success=True,
                    raw_response="<ftwLinkResponse><Status><ErrorCode>0</ErrorCode></Status></ftwLinkResponse>",
                    statuses=[FTWilliamsStatusItem(type=operation, error_code="0")],
                )

        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))

        def field(rule_key: str, label: str, value: str, form_type: FormType, document_type: DocumentType) -> ExtractedField:
            return ExtractedField(
                filing_id=filing.id,
                source_field_name=label,
                normalized_field_name=label.lower(),
                mapped_rule_key=rule_key,
                mapped_label=label,
                form_type=form_type,
                source_document_type=document_type,
                priority=FieldPriority.HIGH,
                value=value,
                proposed_value=value,
            )

        run_async(
            repo.add_fields(
                [
                    field("form_5500_part_i_1e_plan_sponsor_ein", "1e. Plan Sponsor EIN", "73-0759701", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_1b_plan_number_pn", "1b. Plan Number", "501", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_7_plan_year_ending_date", "7. Plan Year Ending Date", "12/31/2025", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_ii_14_active_participants_at_end", "14. Active Participants at End", "101", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("schedule_a_part_i_1a_name_of_insurance_company", "1a. Name of Insurance Company", "BlueCross BlueShield of Oklahoma", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1b_insurance_carrier_ein", "1b. Insurance Carrier EIN", "36-1236610", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1d_contract_policy_number", "1d. Contract", "Y00979", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier", "10a. Total premiums", "100", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                ]
            )
        )

        fake_ftw = PartialUpdateFTWilliamsService()
        review = run_async(
            FTWilliamsReviewService(fake_ftw).approve_and_update(
                filing.id,
                send_to_ftw=True,
                refresh_current_before_update=True,
            )
        )

        self.assertEqual(review.status, FTWilliamsReviewStatus.UPDATE_FAILED)
        self.assertEqual(fake_ftw.send_operations, ["update_5500", "update_schedule_a"])
        self.assertEqual(review.update_retry_count, 0)
        self.assertEqual(review.update_attempted_count, 2)
        self.assertEqual(review.update_confirmed_count, 1)
        self.assertEqual(review.update_remaining_count, 1)
        self.assertEqual(len([field for field in review.fields if field.changed and field.update_included]), 1)

    def test_rejected_internal_tag_is_mapped_to_a_client_facing_field_label(self):
        error = FTWilliamsReviewService()._normalize_review_error(
            "DOL5500Data error 60: invalid field req: SPONS_DFE_NAME0",
            [
                FTWilliamsComparisonField(
                    rule_key="form_5500_part_i_1d_plan_sponsor_name",
                    label="1d. Plan Sponsor Name",
                    form_type=FormType.FORM_5500,
                    ftw_tag="SDName",
                    proposed_value="Example Sponsor",
                    changed=True,
                )
            ],
        )

        self.assertIsNotNone(error)
        self.assertEqual(error.rejected_fields[0].label, "1d. Plan Sponsor Name")

    def test_schedule_match_stays_pending_when_identifier_match_is_ambiguous(self):
        service = FTWilliamsReviewService()
        fields = [
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1b. EIN",
                normalized_field_name="carrier_ein",
                mapped_rule_key="schedule_a_part_i_1b_insurance_carrier_ein",
                mapped_label="1b. EIN",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="36-2739571",
                proposed_value="36-2739571",
            ),
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1c. NAIC Code",
                normalized_field_name="naic",
                mapped_rule_key="schedule_a_part_i_1c_naic_code",
                mapped_label="1c. NAIC Code",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="79413",
                proposed_value="79413",
            ),
        ]
        statuses = [
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no="1",
                query_results={"InsCarrierEIN": "36-2739571", "InsCarrierNAICCode": "79413"},
            ),
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no="7",
                query_results={"InsCarrierEIN": "36-2739571", "InsCarrierNAICCode": "79413"},
            ),
        ]

        self.assertIsNone(service._match_schedule_a_status(fields, statuses))

    def test_schedule_match_stays_pending_when_best_score_margin_is_too_small(self):
        service = FTWilliamsReviewService()
        fields = [
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1a. Carrier",
                normalized_field_name="carrier",
                mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                mapped_label="1a. Carrier",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="Example Insurance Company",
                proposed_value="Example Insurance Company",
            ),
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1b. EIN",
                normalized_field_name="carrier_ein",
                mapped_rule_key="schedule_a_part_i_1b_insurance_carrier_ein",
                mapped_label="1b. EIN",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="36-2739571",
                proposed_value="36-2739571",
            ),
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1c. NAIC",
                normalized_field_name="naic",
                mapped_rule_key="schedule_a_part_i_1c_naic_code",
                mapped_label="1c. NAIC",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="79413",
                proposed_value="79413",
            ),
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1d. Contract",
                normalized_field_name="contract",
                mapped_rule_key="schedule_a_part_i_1d_contract_policy_number",
                mapped_label="1d. Contract",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="ABC-123",
                proposed_value="ABC-123",
            ),
        ]
        statuses = [
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no="1",
                query_results={"InsContractNum": "ABC-123", "InsCarrierEIN": "36-2739571"},
            ),
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no="2",
                query_results={
                    "InsCarrierName": "Example Insurance Company",
                    "InsCarrierEIN": "36-2739571",
                    "InsCarrierNAICCode": "79413",
                },
            ),
        ]

        self.assertIsNone(service._match_schedule_a_status(fields, statuses))

    def test_schedule_match_stays_pending_for_carrier_name_only(self):
        service = FTWilliamsReviewService()
        fields = [
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1a. Carrier",
                normalized_field_name="carrier",
                mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                mapped_label="1a. Carrier",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="Example Insurance Company",
                proposed_value="Example Insurance Company",
            )
        ]
        statuses = [
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no="4",
                query_results={"InsCarrierName": "Example Insurance Company"},
            )
        ]

        self.assertIsNone(service._match_schedule_a_status(fields, statuses))

    def test_schedule_match_selects_a_clearly_higher_safe_score(self):
        service = FTWilliamsReviewService()
        fields = [
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1a. Carrier",
                normalized_field_name="carrier",
                mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                mapped_label="1a. Carrier",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="Example Insurance Company",
                proposed_value="Example Insurance Company",
            ),
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1b. EIN",
                normalized_field_name="carrier_ein",
                mapped_rule_key="schedule_a_part_i_1b_insurance_carrier_ein",
                mapped_label="1b. EIN",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="36-2739571",
                proposed_value="36-2739571",
            ),
            ExtractedField(
                filing_id="filing-1",
                source_field_name="1d. Contract",
                normalized_field_name="contract",
                mapped_rule_key="schedule_a_part_i_1d_contract_policy_number",
                mapped_label="1d. Contract",
                form_type=FormType.SCHEDULE_A,
                source_document_type=DocumentType.SCHEDULE_A,
                priority=FieldPriority.HIGH,
                value="ABC-123",
                proposed_value="ABC-123",
            ),
        ]
        statuses = [
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no="1",
                query_results={
                    "InsCarrierName": "Example Insurance Company",
                    "InsCarrierEIN": "36-2739571",
                    "InsContractNum": "ABC-123",
                },
            ),
            FTWilliamsStatusItem(
                type="ScheduleA",
                error_code="0",
                ftw_seq_no="2",
                query_results={"InsCarrierEIN": "36-2739571"},
            ),
        ]

        matched = service._match_schedule_a_status(fields, statuses)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.ftw_seq_no, "1")

    def test_approve_blocks_high_priority_missing_fields(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="1e plan sponsor ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="",
                        proposed_value="",
                        status=ExtractedFieldStatus.MISSING,
                    )
                ]
            )
        )

        with self.assertRaisesRegex(ValueError, "Resolve 1 high-priority missing field before approving"):
            run_async(FTWilliamsReviewService(FakeFTWilliamsService()).approve_and_update(filing.id))

        updated_filing = run_async(repo.get_filing(filing.id))
        self.assertNotEqual(updated_filing.status, FilingStatus.APPROVED)

    def test_approve_blocks_when_current_year_ftw_record_is_missing(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.upsert_ftwilliams_review(
                FTWilliamsReview(
                    filing_id=filing.id,
                    status=FTWilliamsReviewStatus.BRING_FORWARD_REQUIRED,
                    configured=True,
                    current_query_sent=True,
                    current_query_success=True,
                    current_year_exists=False,
                    bring_forward_required=True,
                    ftw_plan_url="https://www.ftwilliam.com/",
                )
            )
        )

        with self.assertRaisesRegex(ValueError, "native Bring Forward action"):
            run_async(
                FTWilliamsReviewService(FakeFTWilliamsService()).approve_and_update(
                    filing.id,
                    override_blockers=True,
                )
            )

        updated_filing = run_async(repo.get_filing(filing.id))
        self.assertNotEqual(updated_filing.status, FilingStatus.APPROVED)

    def test_approve_can_override_high_priority_missing_fields(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="1e plan sponsor ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="",
                        proposed_value="",
                        status=ExtractedFieldStatus.MISSING,
                    )
                ]
            )
        )

        run_async(FTWilliamsReviewService(FakeFTWilliamsService()).approve_and_update(filing.id, override_blockers=True))

        updated_filing = run_async(repo.get_filing(filing.id))
        audits = run_async(repo.list_audit_logs(filing.id))
        approved_audit = next(audit for audit in audits if audit.event == "APPROVED")
        self.assertEqual(updated_filing.status, FilingStatus.APPROVED)
        self.assertTrue(approved_audit.details["override_blockers"])
        self.assertIn("high-priority missing field", approved_audit.details["approval_blockers"])

    def test_send_update_blocks_selected_schedule_a_when_other_records_are_not_fetched(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(repo.update_filing(filing.id, {"status": FilingStatus.APPROVED}))
        review = FTWilliamsReview(
            filing_id=filing.id,
            status=FTWilliamsReviewStatus.CURRENT_QUERIED,
            configured=True,
            current_query_sent=True,
            current_query_success=True,
            schedule_a_match={"ftw_seq_no": "1"},
            schedule_a_candidates=[{"ftw_seq_no": "1"}, {"ftw_seq_no": "2"}],
            schedule_a_records=[
                {
                    "ftw_seq_no": "1",
                    "query_results": {
                        "InsCarrierName": "Kaiser",
                        "InsContractNum": "236163",
                    },
                }
            ],
            update_xml_schedule_a="""<?xml version="1.0" encoding="utf-8"?>
<ftwLink>
  <DataBatch>
    <DOLScheduleAData><TransactionType>2</TransactionType></DOLScheduleAData>
  </DataBatch>
</ftwLink>""",
            ftw_customer_id="1852103620",
            ftw_plan_id="2239036729",
            year="2025",
        )
        run_async(repo.upsert_ftwilliams_review(review))

        service = FTWilliamsReviewService(FakeFTWilliamsCurrentTagService())
        error = service._missing_schedule_a_records_for_safe_send(review)
        self.assertIsNotNone(error)
        self.assertIn("not fully fetched: 2", error)

    def test_send_update_blocks_schedule_a_xml_that_does_not_preserve_all_fetched_records(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(repo.update_filing(filing.id, {"status": FilingStatus.APPROVED}))
        review = FTWilliamsReview(
            filing_id=filing.id,
            status=FTWilliamsReviewStatus.CURRENT_QUERIED,
            configured=True,
            current_query_sent=True,
            current_query_success=True,
            schedule_a_match={"ftw_seq_no": "1"},
            schedule_a_records=[
                {"ftw_seq_no": "1", "query_results": {"InsContractNum": "236163"}},
                {"ftw_seq_no": "2", "query_results": {"InsContractNum": "OTHER-2"}},
            ],
            update_xml_schedule_a="""<?xml version="1.0" encoding="utf-8"?>
<ftwLink>
  <DataBatch>
    <DOLScheduleAData><TransactionType>2</TransactionType></DOLScheduleAData>
  </DataBatch>
</ftwLink>""",
            ftw_customer_id="1852103620",
            ftw_plan_id="2239036729",
            year="2025",
        )
        run_async(repo.upsert_ftwilliams_review(review))

        service = FTWilliamsReviewService(FakeFTWilliamsCurrentTagService())
        error = service._missing_schedule_a_records_for_safe_send(review)
        self.assertIsNotNone(error)
        self.assertIn("XML contains 1 Schedule A record(s) but exactly 2 record(s) are expected", error)

    def test_live_schedule_a_update_uses_fresh_snapshot_and_restores_after_readback_failure(self):
        class RecordingFTWilliamsService:
            def __init__(self):
                self.sent = []

            @staticmethod
            def mask_key_id(value):
                return value

            async def send_xml(self, operation, request_xml):
                self.sent.append((operation, request_xml))
                return FTWilliamsQueryResponse(
                    operation=operation,
                    configured=True,
                    sent=True,
                    request_xml=request_xml,
                    http_status=200,
                    success=True,
                    raw_response="<ftwLinkResponse><Status><ErrorCode>0</ErrorCode></Status></ftwLinkResponse>",
                    statuses=[FTWilliamsStatusItem(type="DOLScheduleAData", error_code="0")],
                )

        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        changed_field = FTWilliamsComparisonField(
            field_id="field-1",
            label="1e. Persons Covered",
            form_type=FormType.SCHEDULE_A,
            ftw_tag="InsPrsnCoveredEoyCnt",
            current_value="10",
            proposed_value="12",
            changed=True,
            update_included=True,
        )
        baseline_records = [
            {
                "ftw_seq_no": "1",
                "query_results": {
                    "ScheduleDesc": "TARGET",
                    "InsCarrierName": "Target Carrier",
                    "InsContractNum": "TARGET-1",
                    "InsPrsnCoveredEoyCnt": "10",
                },
            },
            {
                "ftw_seq_no": "2",
                "query_results": {
                    "ScheduleDesc": "MANUAL",
                    "InsCarrierName": "Manually Created Carrier",
                    "InsContractNum": "MANUAL-2",
                    "InsPrsnCoveredEoyCnt": "25",
                },
            },
        ]
        update_xml = """<?xml version="1.0" encoding="utf-8"?>
<ftwLink><DataBatch>
<DOLScheduleAData><TransactionType>2</TransactionType><ScheduleDesc>TARGET</ScheduleDesc><InsCarrierName>Target Carrier</InsCarrierName><InsContractNum>TARGET-1</InsContractNum><InsPrsnCoveredEoyCnt>12</InsPrsnCoveredEoyCnt></DOLScheduleAData>
<DOLScheduleAData><TransactionType>2</TransactionType><ScheduleDesc>MANUAL</ScheduleDesc><InsCarrierName>Manually Created Carrier</InsCarrierName><InsContractNum>MANUAL-2</InsContractNum><InsPrsnCoveredEoyCnt>25</InsPrsnCoveredEoyCnt></DOLScheduleAData>
</DataBatch></ftwLink>"""
        review = FTWilliamsReview(
            filing_id=filing.id,
            status=FTWilliamsReviewStatus.CURRENT_QUERIED,
            configured=True,
            current_query_sent=True,
            current_query_success=True,
            current_query_complete=True,
            current_year_exists=True,
            ftw_editable=True,
            ftw_customer_id="customer",
            ftw_plan_id="plan",
            year="2025",
            schedule_a_match={"ftw_seq_no": "1"},
            schedule_a_candidates=[{"ftw_seq_no": "1"}, {"ftw_seq_no": "2"}],
            schedule_a_records=baseline_records,
            schedule_a_contract_type=ScheduleAContractType.NONEXPERIENCE_RATED,
            schedule_a_contract_type_confirmed=True,
            fields=[changed_field],
            update_xml_schedule_a=update_xml,
        )
        reconciled = review.model_copy(deep=True)
        run_async(repo.upsert_ftwilliams_review(review))

        fake_ftw = RecordingFTWilliamsService()
        service = FTWilliamsReviewService(fake_ftw)
        failed_verification = {
            "success": False,
            "mismatches": [
                {
                    "form": "DOLScheduleAData",
                    "tag": "DOLScheduleAData",
                    "reason": "The manually created sibling record disappeared.",
                }
            ],
            "request_xml": "<query />",
            "response_xml": "<missing-sibling />",
        }
        restored_verification = {
            "success": True,
            "mismatches": [],
            "request_xml": "<restore-query />",
            "response_xml": "<restored />",
        }
        settings = SimpleNamespace(
            ftwlink_schedule_a_updates_enabled=True,
            ftw_schema_validation_enabled=False,
            ftw_schema_enforcement_enabled=False,
            ftw_auto_edit_checks_enabled=False,
            ftw_pdf_audit_enabled=False,
        )

        with (
            patch.object(service, "prepare_review", AsyncMock(side_effect=[review, reconciled])) as prepare,
            patch.object(
                service,
                "_verify_update_readback",
                AsyncMock(side_effect=[failed_verification, restored_verification]),
            ),
            patch("app.services.ftwilliams_review.get_settings", return_value=settings),
        ):
            result = run_async(service.approve_and_update(filing.id, send_to_ftw=True))

        self.assertFalse(prepare.await_args_list[0].kwargs["reuse_current_snapshot"])
        self.assertEqual([operation for operation, _ in fake_ftw.sent], ["update_schedule_a", "update_schedule_a"])
        restore_xml = fake_ftw.sent[1][1]
        self.assertEqual(restore_xml.count("<DOLScheduleAData>"), 2)
        self.assertIn("<InsCarrierName>Manually Created Carrier</InsCarrierName>", restore_xml)
        self.assertIn("<InsPrsnCoveredEoyCnt>10</InsPrsnCoveredEoyCnt>", restore_xml)
        self.assertNotIn("<InsPrsnCoveredEoyCnt>12</InsPrsnCoveredEoyCnt>", restore_xml)
        self.assertTrue(result.schedule_a_restore_attempted)
        self.assertTrue(result.schedule_a_restore_success)
        self.assertEqual(result.status, FTWilliamsReviewStatus.UPDATE_FAILED)
        self.assertIn("restored", (result.error_message or "").lower())

    def test_live_updates_for_the_same_ft_plan_are_serialized(self):
        repo = repositories.get_repository()
        filing_one = run_async(repo.create_filing(sample_filing()))
        filing_two = run_async(repo.create_filing(sample_filing()))
        for filing in (filing_one, filing_two):
            run_async(
                repo.upsert_ftwilliams_review(
                    FTWilliamsReview(
                        filing_id=filing.id,
                        ftw_customer_id="customer",
                        ftw_plan_id="plan",
                        year="2025",
                    )
                )
            )

        service = FTWilliamsReviewService(FakeFTWilliamsService())
        active = 0
        max_active = 0

        async def simulated_update(filing_id, **_kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return await repo.get_ftwilliams_review(filing_id)

        async def run_concurrently():
            with patch.object(service, "_approve_and_update_unlocked", side_effect=simulated_update):
                await asyncio.gather(
                    service.approve_and_update(filing_one.id, send_to_ftw=True),
                    service.approve_and_update(filing_two.id, send_to_ftw=True),
                )

        run_async(run_concurrently())

        self.assertEqual(max_active, 1)

    def test_send_update_stops_before_ft_when_production_schedule_a_writes_are_disabled(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(repo.update_filing(filing.id, {"status": FilingStatus.APPROVED}))
        review = FTWilliamsReview(
            filing_id=filing.id,
            status=FTWilliamsReviewStatus.CURRENT_QUERIED,
            configured=True,
            current_query_sent=True,
            current_query_success=True,
            current_query_complete=True,
            current_year_exists=True,
            ftw_editable=True,
            schedule_a_match={"ftw_seq_no": "1"},
            schedule_a_candidates=[{"ftw_seq_no": "1"}],
            schedule_a_records=[
                {
                    "ftw_seq_no": "1",
                    "query_results": {
                        "ScheduleDesc": "CIGNA",
                        "InsCarrierName": "CIGNA",
                        "InsContractNum": "00626686",
                    },
                }
            ],
            schedule_a_contract_type=ScheduleAContractType.NONEXPERIENCE_RATED,
            schedule_a_contract_type_confirmed=True,
            fields=[
                FTWilliamsComparisonField(
                    label="3b. Amount of Commissions",
                    form_type=FormType.SCHEDULE_A,
                    ftw_tag="CommPdAmt1",
                    proposed_value="96",
                    changed=True,
                    update_included=True,
                )
            ],
            update_xml_schedule_a="""<?xml version="1.0" encoding="utf-8"?>
<ftwLink><DataBatch><DOLScheduleAData><TransactionType>2</TransactionType><ScheduleDesc>CIGNA</ScheduleDesc></DOLScheduleAData></DataBatch></ftwLink>""",
            ftw_customer_id="1852103620",
            ftw_plan_id="2239036729",
            year="2025",
        )
        run_async(repo.upsert_ftwilliams_review(review))
        fake_ftw = FakeFTWilliamsService()
        service = FTWilliamsReviewService(fake_ftw)

        with patch.object(service, "prepare_review", AsyncMock(return_value=review)), patch(
            "app.services.ftwilliams_review.get_settings",
            return_value=SimpleNamespace(ftwlink_schedule_a_updates_enabled=False),
        ):
            with self.assertRaisesRegex(ValueError, "Schedule A sending is temporarily disabled"):
                run_async(service.approve_and_update(filing.id, send_to_ftw=True))

        updated_review = run_async(repo.get_ftwilliams_review(filing.id))
        updated_filing = run_async(repo.get_filing(filing.id))
        self.assertEqual(updated_review.update_attempted_count, 0)
        self.assertEqual(updated_filing.status, FilingStatus.FAILED)
        self.assertEqual(fake_ftw.calls, [])

    def test_prepare_review_treats_indicator_and_date_format_differences_as_same(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="sponsor_ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="73-1185740",
                        proposed_value="73-1185740",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Plan Number (PN)",
                        normalized_field_name="plan_number",
                        mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                        mapped_label="1b. Plan Number (PN)",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="501",
                        proposed_value="501",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="7. Plan Year Ending Date",
                        normalized_field_name="plan_year_end",
                        mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                        mapped_label="7. Plan Year Ending Date",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="09-30-2025",
                        proposed_value="09-30-2025",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1c. Plan Effective Date",
                        normalized_field_name="plan_effective_date",
                        mapped_rule_key="form_5500_part_i_1c_plan_effective_date",
                        mapped_label="1c. Plan Effective Date",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="10-01-2011",
                        proposed_value="10-01-2011",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="9. Plan funding arrangement",
                        normalized_field_name="funding_arrangement",
                        mapped_rule_key="form_5500_part_ii_9_plan_funding_arrangement",
                        mapped_label="9. Plan funding arrangement",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="Insurance",
                        proposed_value="Insurance",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="10a. Plan benefit arrangement",
                        normalized_field_name="benefit_arrangement",
                        mapped_rule_key="form_5500_part_ii_10a_plan_benefit_arrangement",
                        mapped_label="10a. Plan benefit arrangement",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="Insurance",
                        proposed_value="Insurance",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="10b. Schedules attached",
                        normalized_field_name="schedules_attached",
                        mapped_rule_key="form_5500_part_ii_10b_schedules_attached",
                        mapped_label="10b. Schedules attached",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="A",
                        proposed_value="A",
                    ),
                ]
            )
        )

        review = run_async(FTWilliamsReviewService(FakeFTWilliamsCurrentTagService()).prepare_review(filing.id, send_queries=True))
        by_label = {field.label: field for field in review.fields}

        self.assertEqual(by_label["9. Plan funding arrangement"].current_value, "Insurance")
        self.assertFalse(by_label["9. Plan funding arrangement"].changed)
        self.assertEqual(by_label["10a. Plan benefit arrangement"].current_value, "Insurance")
        self.assertFalse(by_label["10a. Plan benefit arrangement"].changed)
        self.assertEqual(by_label["10b. Schedules attached"].current_value, "A")
        self.assertFalse(by_label["10b. Schedules attached"].changed)
        self.assertFalse(by_label["1c. Plan Effective Date"].changed)

    def test_prepare_review_builds_plan_lookup_from_extracted_identifiers(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="sponsor_ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="730759701",
                        proposed_value="730759701",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Plan Number (PN)",
                        normalized_field_name="plan_number",
                        mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                        mapped_label="1b. Plan Number (PN)",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="501",
                        proposed_value="501",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="7. Plan Year Ending Date",
                        normalized_field_name="plan_year_end",
                        mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                        mapped_label="7. Plan Year Ending Date",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.LOW,
                        value="2024-12-31",
                        proposed_value="2024-12-31",
                    ),
                ]
            )
        )

        review = run_async(FTWilliamsReviewService().prepare_review(filing.id, send_queries=False))

        self.assertIsNotNone(review.plan_lookup)
        self.assertEqual(review.plan_lookup.status, FTWilliamsPlanLookupStatus.REQUEST_READY)
        self.assertEqual(review.plan_lookup.company_employer_id, "73-0759701")
        self.assertEqual(review.plan_lookup.plan_number, "501")
        self.assertEqual(review.plan_lookup.year, "2024")
        self.assertEqual(review.customer_id, "73-0759701")
        self.assertEqual(review.plan_id, "73-0759701501")
        self.assertEqual(review.plan_lookup.matched_identity["customer_id"], "73-0759701")
        self.assertEqual(review.plan_lookup.matched_identity["plan_id"], "73-0759701501")
        self.assertIn("<PlanData>", review.plan_lookup.request_xml)
        self.assertIn("<CustomerID>73-0759701</CustomerID>", review.plan_lookup.request_xml)
        self.assertIn("<PlanID>73-0759701501</PlanID>", review.plan_lookup.request_xml)

    def test_prepare_review_queries_dynamic_plan_and_matches_schedule_sequence(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="sponsor_ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="73-0759701",
                        proposed_value="73-0759701",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Plan Number (PN)",
                        normalized_field_name="plan_number",
                        mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                        mapped_label="1b. Plan Number (PN)",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="501",
                        proposed_value="501",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="7. Plan Year Ending Date",
                        normalized_field_name="plan_year_end",
                        mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                        mapped_label="7. Plan Year Ending Date",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.LOW,
                        value="2024-12-31",
                        proposed_value="2024-12-31",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1a. Name of Insurance Company",
                        normalized_field_name="carrier",
                        mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                        mapped_label="1a. Name of Insurance Company",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="BlueCross BlueShield of Oklahoma",
                        proposed_value="BlueCross BlueShield of Oklahoma",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Insurance Carrier EIN",
                        normalized_field_name="carrier_ein",
                        mapped_rule_key="schedule_a_part_i_1b_insurance_carrier_ein",
                        mapped_label="1b. Insurance Carrier EIN",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="36-1236610",
                        proposed_value="36-1236610",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1d. Contract / Policy Number",
                        normalized_field_name="contract",
                        mapped_rule_key="schedule_a_part_i_1d_contract_policy_number",
                        mapped_label="1d. Contract / Policy Number",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="Y00979",
                        proposed_value="Y00979",
                    ),
                ]
            )
        )

        fake_ftw = FakeFTWilliamsService()
        review = run_async(FTWilliamsReviewService(fake_ftw).prepare_review(filing.id, send_queries=True))

        query_plan_call = next(call for call in fake_ftw.calls if call.operation == "query_plan")
        self.assertEqual(query_plan_call.customer_id, "73-0759701")
        self.assertEqual(query_plan_call.plan_id, "73-0759701501")
        self.assertEqual(review.plan_lookup.status, FTWilliamsPlanLookupStatus.MATCHED)
        self.assertEqual(review.ftw_customer_id, "782023768")
        self.assertEqual(review.ftw_plan_id, "959357188")
        self.assertTrue(review.current_query_success)
        self.assertEqual(review.schedule_a_match["ftw_seq_no"], "2")
        self.assertEqual(review.schedule_a_match["contract"], "Y00979")
        self.assertIn("<DOLScheduleAData>", review.update_xml_schedule_a)
        self.assertIn("<InsCarrierName>BlueCross BlueShield of Oklahoma</InsCarrierName>", review.update_xml_schedule_a)

    def test_prepare_review_stops_current_query_when_plan_lookup_cannot_match(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="sponsor_ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="33-0574214",
                        proposed_value="33-0574214",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Plan Number (PN)",
                        normalized_field_name="plan_number",
                        mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                        mapped_label="1b. Plan Number (PN)",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="501",
                        proposed_value="501",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="7. Plan Year Ending Date",
                        normalized_field_name="plan_year_end",
                        mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                        mapped_label="7. Plan Year Ending Date",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.LOW,
                        value="2024-11-30",
                        proposed_value="2024-11-30",
                    ),
                ]
            )
        )

        fake_ftw = FakeFTWilliamsPlanNotFoundService()
        review = run_async(FTWilliamsReviewService(fake_ftw).prepare_review(filing.id, send_queries=True))

        self.assertEqual(
            [call.operation for call in fake_ftw.calls],
            ["query_plan", "query_plan", "archive_5500_get_data", "plan_ids_batch"],
        )
        self.assertEqual(review.plan_lookup.status, FTWilliamsPlanLookupStatus.NOT_FOUND)
        self.assertFalse(review.current_query_success)
        self.assertIn("Could not locate existing plan", review.error_message or "")
        self.assertEqual(review.customer_id, "33-0574214")
        self.assertEqual(review.plan_id, "33-0574214501")

        with self.assertRaisesRegex(ValueError, "Could not locate existing plan"):
            run_async(
                FTWilliamsReviewService(fake_ftw).approve_and_update(
                    filing.id,
                    send_to_ftw=True,
                    refresh_current_before_update=True,
                )
            )

        failed_review = run_async(repo.get_ftwilliams_review(filing.id))
        self.assertEqual(failed_review.status, FTWilliamsReviewStatus.UPDATE_FAILED)
        self.assertIn("Could not locate existing plan", failed_review.error_message or "")

        preview_only_refresh = run_async(
            FTWilliamsReviewService(fake_ftw).prepare_review(filing.id, send_queries=False)
        )
        filing_after_preview = run_async(repo.get_filing(filing.id))

        self.assertEqual(preview_only_refresh.plan_lookup.status, FTWilliamsPlanLookupStatus.NOT_FOUND)
        self.assertIn("Could not locate existing plan", preview_only_refresh.plan_lookup.error_message or "")
        self.assertEqual(filing_after_preview.status, FilingStatus.FAILED)

    def test_prepare_review_falls_back_to_archive_lookup_and_saves_ftw_ids(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="sponsor_ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="33-0574214",
                        proposed_value="33-0574214",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Plan Number (PN)",
                        normalized_field_name="plan_number",
                        mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                        mapped_label="1b. Plan Number (PN)",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="501",
                        proposed_value="501",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="7. Plan Year Ending Date",
                        normalized_field_name="plan_year_end",
                        mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                        mapped_label="7. Plan Year Ending Date",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.LOW,
                        value="2024-11-30",
                        proposed_value="2024-11-30",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1a. Plan Name",
                        normalized_field_name="plan_name",
                        mapped_rule_key="form_5500_part_i_1a_plan_name",
                        mapped_label="1a. Plan Name",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.LOW,
                        value="CAMINO HEALTH CENTER WRAP BENEFIT PLAN",
                        proposed_value="CAMINO HEALTH CENTER WRAP BENEFIT PLAN",
                    ),
                ]
            )
        )

        fake_ftw = FakeFTWilliamsArchivePlanLookupService()
        review = run_async(FTWilliamsReviewService(fake_ftw).prepare_review(filing.id, send_queries=True))
        mapping = run_async(repo.get_ftwilliams_plan_mapping("33-0574214", "501"))

        self.assertEqual([call.operation for call in fake_ftw.calls[:4]], ["query_plan", "query_plan", "archive_5500_get_data", "query_5500"])
        self.assertEqual(review.plan_lookup.status, FTWilliamsPlanLookupStatus.MATCHED)
        self.assertEqual(review.ftw_customer_id, "111222333")
        self.assertEqual(review.ftw_plan_id, "444555666")
        self.assertIsNotNone(mapping)
        self.assertEqual(mapping.ftw_customer_id, "111222333")
        self.assertEqual(mapping.ftw_plan_id, "444555666")
        self.assertIn("<FTWCustomerID>111222333</FTWCustomerID>", review.update_xml_5500)
        self.assertNotIn("<PlanID>33-0574214501</PlanID>", review.update_xml_5500)

    def test_prepare_review_falls_back_to_archive_name_lookup_when_ein_id_is_invalid(self):
        repo = repositories.get_repository()
        filing_payload = sample_filing()
        filing_payload.package_documents = [
            {
                "client_name": "Worcester Community Action Council, Inc. (Test)",
                "sharefile_path": "Shared Folders / Worcester Community Action Council, Inc. (Test) / 5500 Filing / 2025 Filing",
            }
        ]
        filing = run_async(repo.create_filing(filing_payload))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="sponsor_ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="04-2382160",
                        proposed_value="04-2382160",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Plan Number (PN)",
                        normalized_field_name="plan_number",
                        mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                        mapped_label="1b. Plan Number (PN)",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="501",
                        proposed_value="501",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="7. Plan Year Ending Date",
                        normalized_field_name="plan_year_end",
                        mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                        mapped_label="7. Plan Year Ending Date",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.LOW,
                        value="2025-12-31",
                        proposed_value="2025-12-31",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1a. Plan Name",
                        normalized_field_name="plan_name",
                        mapped_rule_key="form_5500_part_i_1a_plan_name",
                        mapped_label="1a. Plan Name",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.LOW,
                        value="WORCESTER COMMUNITY ACTION HEALTH & WELFARE BENEFIT PLAN",
                        proposed_value="WORCESTER COMMUNITY ACTION HEALTH & WELFARE BENEFIT PLAN",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1d. Plan Sponsor Name",
                        normalized_field_name="sponsor_name",
                        mapped_rule_key="form_5500_part_i_1d_plan_sponsor_name",
                        mapped_label="1d. Plan Sponsor Name",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.LOW,
                        value="Worcester Community Action",
                        proposed_value="Worcester Community Action",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1f. Plan Sponsor Address",
                        normalized_field_name="sponsor_address",
                        mapped_rule_key="form_5500_part_i_1f_plan_sponsor_address",
                        mapped_label="1f. Plan Sponsor Address",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.LOW,
                        value="18 CHESTNUT ST. SUITE 500 WORCESTER MA 01608",
                        proposed_value="18 CHESTNUT ST. SUITE 500 WORCESTER MA 01608",
                    ),
                ]
            )
        )

        fake_ftw = FakeFTWilliamsArchiveNameLookupService()
        review = run_async(FTWilliamsReviewService(fake_ftw).prepare_review(filing.id, send_queries=True))
        mapping = run_async(repo.get_ftwilliams_plan_mapping("04-2382160", "501"))

        self.assertEqual(
            [call.operation for call in fake_ftw.calls[:6]],
            [
                "query_plan",
                "query_plan",
                "archive_5500_get_data",
                "archive_5500_ein_lookup",
                "archive_5500_ein_lookup",
                "query_5500",
            ],
        )
        name_lookup_calls = [call for call in fake_ftw.calls if call.operation == "archive_5500_ein_lookup"]
        self.assertEqual([call.company_name for call in name_lookup_calls], ["Worcester Community Action", "Worcester Community Action Council, Inc."])
        self.assertEqual([call.company_state for call in name_lookup_calls], ["MA", "MA"])
        self.assertEqual(review.plan_lookup.status, FTWilliamsPlanLookupStatus.MATCHED)
        self.assertEqual(review.ftw_customer_id, "222333444")
        self.assertEqual(review.ftw_plan_id, "555666777")
        self.assertIsNotNone(mapping)
        self.assertEqual(mapping.source, "ARCHIVE_NAME_LOOKUP")
        self.assertEqual(mapping.ftw_customer_id, "222333444")
        self.assertEqual(mapping.ftw_plan_id, "555666777")
        self.assertIn("<FTWCustomerID>222333444</FTWCustomerID>", review.update_xml_5500)
        self.assertNotIn("<CustomerID>04-2382160</CustomerID>", review.update_xml_5500)

    def test_prepare_review_uses_plan_ids_batch_when_user_defined_ids_do_not_match_ein(self):
        class PlanIdsBatchFTWilliamsService(FTWilliamsService):
            def __init__(self):
                self.calls = []

            def status(self) -> dict:
                return {"configured": True}

            async def run_query(self, payload):
                self.calls.append(payload)
                request_xml = self.mask_key_id(self.build_request_xml(payload))
                if payload.operation == "query_plan" and payload.ftw_plan_id == "987654321":
                    return FTWilliamsQueryResponse(
                        operation=payload.operation,
                        configured=True,
                        sent=True,
                        request_xml=request_xml,
                        success=True,
                        raw_response="<ftwLinkResponse />",
                        statuses=[
                            FTWilliamsStatusItem(
                                type="PlanData",
                                error_code="0",
                                customer_id="OHIO-COMPANY",
                                plan_id="OHIO-PLAN",
                                ftw_customer_id="387302687",
                                ftw_plan_id="987654321",
                                query_results={
                                    "CompanyEmployerID": "34-1655024",
                                    "PlanNumber": "501",
                                    "CompanyName": "OHIO VALLEY STAMPING & ASSOCIATES INC.",
                                    "PlanLine1": "OHIO VALLEY STAMPING & ASSOCIATES INC. HARTFORD PLAN",
                                },
                            )
                        ],
                    )
                if payload.operation == "query_plan":
                    return FTWilliamsQueryResponse(
                        operation=payload.operation,
                        configured=True,
                        sent=True,
                        request_xml=request_xml,
                        success=False,
                        raw_response="<ftwLinkResponse />",
                        statuses=[FTWilliamsStatusItem(type="PlanData", error_code="18", error_desc="Company ID is not valid")],
                    )
                if payload.operation in {"archive_5500_get_data", "archive_5500_ein_lookup"}:
                    return FTWilliamsQueryResponse(
                        operation=payload.operation,
                        configured=True,
                        sent=True,
                        request_xml=request_xml,
                        success=False,
                        raw_response="<ftwLinkResponse />",
                        statuses=[FTWilliamsStatusItem(type="Archive5500", error_code="18", error_desc="No matching plan")],
                    )
                if payload.operation == "plan_ids_batch":
                    raw_response = """<ftwLinkResponse>
  <Status><Type>PlanIDs_Batch</Type><ErrorCode>0</ErrorCode><CustomerID>OHIO-COMPANY</CustomerID><PlanID>OHIO-PLAN</PlanID><FTWCustomerID>387302687</FTWCustomerID><FTWPlanID>987654321</FTWPlanID></Status>
  <Status><Type>PlanIDs_Batch</Type><ErrorCode>0</ErrorCode><CustomerID>OTHER</CustomerID><PlanID>OTHER-PLAN</PlanID><FTWCustomerID>111</FTWCustomerID><FTWPlanID>222</FTWPlanID></Status>
</ftwLinkResponse>"""
                    return FTWilliamsQueryResponse(
                        operation=payload.operation,
                        configured=True,
                        sent=True,
                        request_xml=request_xml,
                        success=True,
                        raw_response=raw_response,
                        statuses=self.parse_response(raw_response),
                    )
                if payload.operation == "query_5500":
                    return FTWilliamsQueryResponse(
                        operation=payload.operation,
                        configured=True,
                        sent=True,
                        request_xml=request_xml,
                        success=True,
                        raw_response="<ftwLinkResponse />",
                        statuses=[
                            FTWilliamsStatusItem(
                                type="DOL5500Data",
                                error_code="0",
                                ftw_customer_id=payload.ftw_customer_id,
                                ftw_plan_id=payload.ftw_plan_id,
                                query_results={
                                    "PLAN_NAME0": "OHIO VALLEY STAMPING & ASSOCIATES INC. HARTFORD PLAN",
                                    "SDEIN": "34-1655024",
                                    "SponsDfePlanNum": "501",
                                    "LockedStatus": "Unlocked",
                                    "SignedStatus": "Not Signed",
                                    "FilingStatus": "Draft",
                                },
                            )
                        ],
                    )
                if payload.operation == "query_schedule_a":
                    return FTWilliamsQueryResponse(
                        operation=payload.operation,
                        configured=True,
                        sent=True,
                        request_xml=request_xml,
                        success=False,
                        raw_response="<ftwLinkResponse />",
                        statuses=[FTWilliamsStatusItem(type="DOLScheduleAData", error_code="59", error_desc="Could not locate form")],
                    )
                raise AssertionError(f"Unexpected FT Williams operation: {payload.operation}")

        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="sponsor_ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        value="34-1655024",
                        proposed_value="34-1655024",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Plan Number (PN)",
                        normalized_field_name="plan_number",
                        mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                        mapped_label="1b. Plan Number (PN)",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        value="501",
                        proposed_value="501",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="7. Plan Year Ending Date",
                        normalized_field_name="plan_year_end",
                        mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                        mapped_label="7. Plan Year Ending Date",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        value="12/31/2025",
                        proposed_value="12/31/2025",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1a. Plan Name",
                        normalized_field_name="plan_name",
                        mapped_rule_key="form_5500_part_i_1a_plan_name",
                        mapped_label="1a. Plan Name",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        value="OHIO VALLEY STAMPING & ASSOCIATES INC. HARTFORD PLAN",
                        proposed_value="OHIO VALLEY STAMPING & ASSOCIATES INC. HARTFORD PLAN",
                    ),
                ]
            )
        )

        fake_ftw = PlanIdsBatchFTWilliamsService()
        review = run_async(FTWilliamsReviewService(fake_ftw).prepare_review(filing.id, send_queries=True))
        mapping = run_async(repo.get_ftwilliams_plan_mapping("34-1655024", "501"))

        self.assertEqual(review.plan_lookup.status, FTWilliamsPlanLookupStatus.MATCHED)
        self.assertEqual(review.ftw_customer_id, "387302687")
        self.assertEqual(review.ftw_plan_id, "987654321")
        self.assertTrue(review.ftw_editable)
        self.assertEqual(review.ftw_locked_status, "Unlocked")
        self.assertIn("<ResultCount>2</ResultCount>", review.plan_lookup.response_xml)
        self.assertNotIn("OTHER-PLAN", review.plan_lookup.response_xml)
        self.assertIsNotNone(mapping)
        self.assertEqual(mapping.source, "PLAN_IDS_BATCH")
        self.assertEqual(mapping.customer_id, "OHIO-COMPANY")
        self.assertEqual(mapping.plan_id, "OHIO-PLAN")
        self.assertEqual(mapping.ftw_customer_id, "387302687")
        self.assertEqual(mapping.ftw_plan_id, "987654321")

    def test_prepare_review_falls_back_to_customer_id_as_plan_id(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="sponsor_ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="33-0574214",
                        proposed_value="33-0574214",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Plan Number (PN)",
                        normalized_field_name="plan_number",
                        mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                        mapped_label="1b. Plan Number (PN)",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="501",
                        proposed_value="501",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="7. Plan Year Ending Date",
                        normalized_field_name="plan_year_end",
                        mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                        mapped_label="7. Plan Year Ending Date",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.LOW,
                        value="2024-11-30",
                        proposed_value="2024-11-30",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1a. Name of Insurance Company",
                        normalized_field_name="carrier",
                        mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                        mapped_label="1a. Name of Insurance Company",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="Anthem Blue Cross",
                        proposed_value="Anthem Blue Cross",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1d. Contract / Policy Number",
                        normalized_field_name="contract",
                        mapped_rule_key="schedule_a_part_i_1d_contract_policy_number",
                        mapped_label="1d. Contract / Policy Number",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="ANTHEM-1",
                        proposed_value="ANTHEM-1",
                    ),
                ]
            )
        )

        fake_ftw = FakeFTWilliamsSameCustomerPlanLookupService()
        review = run_async(FTWilliamsReviewService(fake_ftw).prepare_review(filing.id, send_queries=True))
        mapping = run_async(repo.get_ftwilliams_plan_mapping("33-0574214", "501"))
        query_plan_calls = [call for call in fake_ftw.calls if call.operation == "query_plan"]

        self.assertEqual([call.plan_id for call in query_plan_calls], ["33-0574214501", "33-0574214"])
        self.assertEqual(review.plan_lookup.status, FTWilliamsPlanLookupStatus.MATCHED)
        self.assertTrue(review.current_query_success)
        self.assertEqual(review.plan_id, "33-0574214")
        self.assertEqual(review.ftw_customer_id, "1280119512")
        self.assertEqual(review.ftw_plan_id, "1559780665")
        self.assertEqual(review.schedule_a_match["ftw_seq_no"], "3")
        self.assertEqual(mapping.source, "PLAN_ID_FALLBACK")
        self.assertEqual(mapping.plan_id, "33-0574214")
        self.assertEqual(mapping.ftw_customer_id, "1280119512")
        self.assertEqual(mapping.ftw_plan_id, "1559780665")

    def test_manual_plan_match_is_saved_and_reused_for_same_ein_plan(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="sponsor_ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="73-0759701",
                        proposed_value="73-0759701",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Plan Number (PN)",
                        normalized_field_name="plan_number",
                        mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                        mapped_label="1b. Plan Number (PN)",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="501",
                        proposed_value="501",
                    ),
                ]
            )
        )

        review = run_async(
            FTWilliamsReviewService().apply_manual_plan_match(
                filing.id,
                FTWilliamsManualMatchRequest(
                    ftw_customer_id="782023768",
                    ftw_plan_id="959357188",
                    year="2024",
                ),
            )
        )

        stored = run_async(repo.get_ftwilliams_plan_mapping("73-0759701", "501"))
        self.assertIsNotNone(stored)
        self.assertEqual(stored.ftw_customer_id, "782023768")
        self.assertEqual(review.plan_lookup.status, FTWilliamsPlanLookupStatus.MATCHED)
        self.assertEqual(review.ftw_customer_id, "782023768")
        self.assertEqual(review.ftw_plan_id, "959357188")

    def test_manual_schedule_a_selection_sets_sequence_and_rebuilds_update_xml(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="sponsor_ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="73-0759701",
                        proposed_value="73-0759701",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Plan Number (PN)",
                        normalized_field_name="plan_number",
                        mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                        mapped_label="1b. Plan Number (PN)",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="501",
                        proposed_value="501",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="7. Plan Year Ending Date",
                        normalized_field_name="plan_year_end",
                        mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                        mapped_label="7. Plan Year Ending Date",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.LOW,
                        value="2024-12-31",
                        proposed_value="2024-12-31",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1a. Name of Insurance Company",
                        normalized_field_name="carrier",
                        mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                        mapped_label="1a. Name of Insurance Company",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="BlueCross BlueShield of Oklahoma",
                        proposed_value="BlueCross BlueShield of Oklahoma",
                    ),
                ]
            )
        )
        fake_ftw = FakeFTWilliamsService()
        service = FTWilliamsReviewService(fake_ftw)
        run_async(service.prepare_review(filing.id, send_queries=False))

        review = run_async(
            service.select_schedule_a_match(
                filing.id,
                FTWilliamsScheduleAMatchRequest(ftw_seq_no="2"),
            )
        )

        schedule_call = next(call for call in fake_ftw.calls if call.operation == "query_schedule_a")
        self.assertEqual(schedule_call.ftw_seq_no, "2")
        self.assertEqual(review.schedule_a_match["ftw_seq_no"], "2")
        self.assertEqual(review.schedule_a_match["source"], "MANUAL")
        self.assertIn("<DOLScheduleAData>", review.update_xml_schedule_a)
        self.assertIn("<InsCarrierName>BlueCross BlueShield of Oklahoma</InsCarrierName>", review.update_xml_schedule_a)

    def test_current_query_accepts_explicit_new_schedule_a_without_matching_existing_sequence(self):
        field = ExtractedField(
            filing_id="filing-new-schedule",
            source_field_name="1d. Contract / Policy Number",
            normalized_field_name="contract",
            mapped_rule_key="schedule_a_part_i_1d_contract_policy_number",
            mapped_label="1d. Contract / Policy Number",
            form_type=FormType.SCHEDULE_A,
            source_document_type=DocumentType.SCHEDULE_A,
            priority=FieldPriority.HIGH,
            value="NEW-CONTRACT",
            proposed_value="NEW-CONTRACT",
        )
        existing_review = FTWilliamsReview(
            filing_id=field.filing_id,
            schedule_a_match={
                "create_new": True,
                "source": "MANUAL",
                "schedule_desc": "NEWLIFE",
            },
        )
        existing_status = FTWilliamsStatusItem(
            type="ScheduleA",
            error_code="0",
            ftw_seq_no="1",
            query_results={
                "ScheduleDesc": "EXISTING",
                "InsCarrierName": "Existing Carrier",
                "InsContractNum": "OLD-CONTRACT",
            },
        )
        snapshot = {
            "query_request_xmls": ["<query />"],
            "query_response_xmls": ["<response />"],
            "form_5500_current": {},
            "form_5500_error": None,
            "form_5500_query_failed": False,
            "schedule_statuses": [existing_status],
            "schedule_a_error": None,
            "schedule_a_query_failed": False,
        }
        service = FTWilliamsReviewService(FakeFTWilliamsService())

        with patch.object(service, "_current_data_snapshot", AsyncMock(return_value=snapshot)):
            result = run_async(
                service._run_current_queries_for_year(
                    [field],
                    {},
                    existing_review,
                )
            )

        self.assertTrue(result["current_query_success"])
        self.assertTrue(result["current_query_complete"])
        self.assertIsNone(result["matched_schedule_a"])
        self.assertEqual(result["schedule_a_current"], {})
        self.assertEqual({record["ftw_seq_no"] for record in result["schedule_a_records"]}, {"1"})
        self.assertNotIn("none safely matched", result["error_message"] or "")

    def test_prepare_review_never_loads_prior_year_when_target_year_has_no_ftw_data(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="sponsor_ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="73-1185740",
                        proposed_value="73-1185740",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Plan Number (PN)",
                        normalized_field_name="plan_number",
                        mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                        mapped_label="1b. Plan Number (PN)",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="501",
                        proposed_value="501",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="7. Plan Year Ending Date",
                        normalized_field_name="plan_year_end",
                        mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                        mapped_label="7. Plan Year Ending Date",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.LOW,
                        value="2024-12-31",
                        proposed_value="2024-12-31",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1a. Name of Insurance Company",
                        normalized_field_name="carrier",
                        mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                        mapped_label="1a. Name of Insurance Company",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="Medical Mutual",
                        proposed_value="Medical Mutual",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Insurance Carrier EIN",
                        normalized_field_name="carrier_ein",
                        mapped_rule_key="schedule_a_part_i_1b_insurance_carrier_ein",
                        mapped_label="1b. Insurance Carrier EIN",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="73-0000001",
                        proposed_value="73-0000001",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1d. Contract / Policy Number",
                        normalized_field_name="contract",
                        mapped_rule_key="schedule_a_part_i_1d_contract_policy_number",
                        mapped_label="1d. Contract / Policy Number",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="MED-4455",
                        proposed_value="MED-4455",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="11. Total participants at beginning of year",
                        normalized_field_name="participants_beginning",
                        mapped_rule_key="form_5500_part_ii_11_total_participants_at_beginning_of_year",
                        mapped_label="11. Total participants at beginning of year",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="",
                        proposed_value="",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Persons Covered (End of Policy Year)",
                        normalized_field_name="persons_covered",
                        mapped_rule_key="schedule_a_part_i_1e_persons_covered_end_of_policy_year",
                        mapped_label="1e. Persons Covered (End of Policy Year)",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="",
                        proposed_value="",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="3b. Amount of Commissions",
                        normalized_field_name="commissions",
                        mapped_rule_key="schedule_a_part_i_3b_amount_of_commissions",
                        mapped_label="3b. Amount of Commissions",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="777",
                        proposed_value="777",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="10a. Total premiums or subscription charges paid to carrier",
                        normalized_field_name="total_premiums",
                        mapped_rule_key="schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier",
                        mapped_label="10a. Total premiums or subscription charges paid to carrier",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="",
                        proposed_value="",
                    ),
                ]
            )
        )

        fake_ftw = FakeFTWilliamsFallbackService()
        review = run_async(FTWilliamsReviewService(fake_ftw).prepare_review(filing.id, send_queries=True))

        queried_5500_years = [call.year for call in fake_ftw.calls if call.operation == "query_5500"]
        self.assertEqual(queried_5500_years, ["2024"])
        self.assertFalse(any(call.year == "2023" for call in fake_ftw.calls))
        self.assertFalse(review.current_query_success)
        self.assertFalse(review.current_year_exists)
        self.assertTrue(review.bring_forward_required)
        self.assertEqual(review.status, FTWilliamsReviewStatus.BRING_FORWARD_REQUIRED)
        self.assertEqual(
            review.ftw_plan_url,
            "https://ftwilliam.com/cgi-bin/index.cgi?"
            "#go=iframe&page=/cgi-bin/PlanDoc2.cgi&PerformDoc5500=1&"
            "plan=900000001,900000002&Year=2024",
        )
        self.assertEqual(review.year, "2024")
        self.assertIsNone(review.comparison_year)
        self.assertIsNone(review.comparison_year_source)
        self.assertIsNone(review.schedule_a_match)
        self.assertEqual(review.schedule_a_candidates, [])
        self.assertIn("current-year 2024", review.error_message.lower())
        self.assertIn("no prior-year values were loaded", review.error_message.lower())
        by_rule = {field.rule_key: field for field in review.fields}
        self.assertEqual(
            by_rule["schedule_a_part_i_1a_name_of_insurance_company"].current_value,
            "",
        )
        self.assertEqual(
            by_rule["form_5500_part_ii_11_total_participants_at_beginning_of_year"].current_value,
            "",
        )
        self.assertEqual(
            by_rule["schedule_a_part_i_1e_persons_covered_end_of_policy_year"].proposed_value,
            "",
        )
        self.assertEqual(
            by_rule["schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier"].current_value,
            "",
        )
        self.assertEqual(
            by_rule["schedule_a_part_i_3b_amount_of_commissions"].proposed_value,
            "777",
        )
        self.assertNotIn("<CommPdAmt1>777</CommPdAmt1>", review.update_xml_schedule_a)
        for prior_year_value in (
            "Medical Mutual",
            "73-0000001",
            "MED-4455",
            "01/01/2023",
            "12/31/2023",
            "111111",
            "222222",
            "333333",
            "444444",
            "555555",
            "666666",
        ):
            self.assertNotIn(prior_year_value, review.update_xml_schedule_a or "")

    def test_prepare_review_requires_current_year_for_multiple_documents_without_prior_queries(self):
        async def scenario():
            repo = repositories.get_repository()
            fake_ftw = FakeFTWilliamsFallbackService()
            service = FTWilliamsReviewService(fake_ftw)
            reviews = []
            for index in range(3):
                filing = await repo.create_filing(
                    sample_filing().model_copy(update={"file_name": f"2025 Filing Package {index + 1}"})
                )
                await repo.add_fields(
                    [
                        ExtractedField(
                            filing_id=filing.id,
                            source_field_name="1e. Plan Sponsor EIN",
                            normalized_field_name="sponsor_ein",
                            mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                            mapped_label="1e. Plan Sponsor EIN",
                            form_type=FormType.FORM_5500,
                            source_document_type=DocumentType.PLAN_WORKSHEET,
                            priority=FieldPriority.MEDIUM,
                            value="73-1185740",
                            proposed_value="73-1185740",
                        ),
                        ExtractedField(
                            filing_id=filing.id,
                            source_field_name="1b. Plan Number (PN)",
                            normalized_field_name="plan_number",
                            mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                            mapped_label="1b. Plan Number (PN)",
                            form_type=FormType.FORM_5500,
                            source_document_type=DocumentType.PLAN_WORKSHEET,
                            priority=FieldPriority.MEDIUM,
                            value="501",
                            proposed_value="501",
                        ),
                        ExtractedField(
                            filing_id=filing.id,
                            source_field_name="7. Plan Year Ending Date",
                            normalized_field_name="plan_year_end",
                            mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                            mapped_label="7. Plan Year Ending Date",
                            form_type=FormType.FORM_5500,
                            source_document_type=DocumentType.PLAN_WORKSHEET,
                            priority=FieldPriority.LOW,
                            value="2024-12-31",
                            proposed_value="2024-12-31",
                        ),
                        ExtractedField(
                            filing_id=filing.id,
                            source_field_name="1a. Name of Insurance Company",
                            normalized_field_name="carrier",
                            mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                            mapped_label="1a. Name of Insurance Company",
                            form_type=FormType.SCHEDULE_A,
                            source_document_type=DocumentType.SCHEDULE_A,
                            priority=FieldPriority.HIGH,
                            value="Medical Mutual",
                            proposed_value="Medical Mutual",
                        ),
                        ExtractedField(
                            filing_id=filing.id,
                            source_field_name="1b. Insurance Carrier EIN",
                            normalized_field_name="carrier_ein",
                            mapped_rule_key="schedule_a_part_i_1b_insurance_carrier_ein",
                            mapped_label="1b. Insurance Carrier EIN",
                            form_type=FormType.SCHEDULE_A,
                            source_document_type=DocumentType.SCHEDULE_A,
                            priority=FieldPriority.HIGH,
                            value="73-0000001",
                            proposed_value="73-0000001",
                        ),
                        ExtractedField(
                            filing_id=filing.id,
                            source_field_name="1d. Contract / Policy Number",
                            normalized_field_name="contract",
                            mapped_rule_key="schedule_a_part_i_1d_contract_policy_number",
                            mapped_label="1d. Contract / Policy Number",
                            form_type=FormType.SCHEDULE_A,
                            source_document_type=DocumentType.SCHEDULE_A,
                            priority=FieldPriority.HIGH,
                            value="MED-4455",
                            proposed_value="MED-4455",
                        ),
                    ]
                )
                reviews.append(await service.prepare_review(filing.id, send_queries=True))
            return reviews, fake_ftw.calls

        reviews, calls = run_async(scenario())

        self.assertEqual(len(reviews), 3)
        self.assertEqual(len({review.filing_id for review in reviews}), 3)
        self.assertFalse(any(call.year == "2023" for call in calls))
        for review in reviews:
            self.assertFalse(review.current_query_success)
            self.assertFalse(review.current_year_exists)
            self.assertTrue(review.bring_forward_required)
            self.assertEqual(review.status, FTWilliamsReviewStatus.BRING_FORWARD_REQUIRED)
            self.assertNotIn("<DOL5500Data>", review.update_xml_5500 or "")
            self.assertNotIn("<DOLScheduleAData>", review.update_xml_schedule_a or "")

    def test_refresh_loads_only_current_year_after_native_ftw_bring_forward(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="sponsor_ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="73-1185740",
                        proposed_value="73-1185740",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Plan Number (PN)",
                        normalized_field_name="plan_number",
                        mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                        mapped_label="1b. Plan Number (PN)",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="501",
                        proposed_value="501",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="7. Plan Year Ending Date",
                        normalized_field_name="plan_year_end",
                        mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                        mapped_label="7. Plan Year Ending Date",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.LOW,
                        value="2024-12-31",
                        proposed_value="2024-12-31",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1a. Name of Insurance Company",
                        normalized_field_name="carrier",
                        mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                        mapped_label="1a. Name of Insurance Company",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="Medical Mutual",
                        proposed_value="Medical Mutual",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. EIN",
                        normalized_field_name="carrier_ein",
                        mapped_rule_key="schedule_a_part_i_1b_insurance_carrier_ein",
                        mapped_label="1b. EIN",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="73-0000001",
                        proposed_value="73-0000001",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="4a. Plan Name",
                        normalized_field_name="plan_name",
                        mapped_rule_key="schedule_a_part_iv_4a_plan_name",
                        mapped_label="4a. Plan Name",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="Fallback Test Plan",
                        proposed_value="Fallback Test Plan",
                    ),
                ]
            )
        )

        fake_ftw = FakeFTWilliamsFallbackService()
        service = FTWilliamsReviewService(fake_ftw)
        missing = run_async(service.prepare_review(filing.id, send_queries=True))
        fake_ftw.current_year_available = True
        refreshed = run_async(service.prepare_review(filing.id, send_queries=True))

        schedule_calls = [call for call in fake_ftw.calls if call.operation == "query_schedule_a" and call.ftw_seq_no == "4"]
        self.assertFalse(any(call.year == "2023" for call in fake_ftw.calls))
        self.assertTrue(all(call.year == "2024" for call in schedule_calls))
        self.assertTrue(missing.bring_forward_required)
        self.assertFalse(missing.current_year_exists)
        self.assertTrue(refreshed.current_year_exists)
        self.assertFalse(refreshed.bring_forward_required)
        self.assertEqual(refreshed.comparison_year, "2024")
        self.assertEqual(refreshed.comparison_year_source, "CURRENT")
        self.assertEqual(refreshed.schedule_a_match["ftw_seq_no"], "4")
        self.assertIn("<Year>2024</Year>", refreshed.update_xml_schedule_a)

    def test_manual_schedule_a_selection_keeps_all_candidates_visible(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="sponsor_ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="73-1185740",
                        proposed_value="73-1185740",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Plan Number (PN)",
                        normalized_field_name="plan_number",
                        mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                        mapped_label="1b. Plan Number (PN)",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.MEDIUM,
                        value="501",
                        proposed_value="501",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="7. Plan Year Ending Date",
                        normalized_field_name="plan_year_end",
                        mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                        mapped_label="7. Plan Year Ending Date",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.LOW,
                        value="2024-12-31",
                        proposed_value="2024-12-31",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1a. Name of Insurance Company",
                        normalized_field_name="carrier",
                        mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                        mapped_label="1a. Name of Insurance Company",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="UnitedHealthcare",
                        proposed_value="UnitedHealthcare",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Insurance Carrier EIN",
                        normalized_field_name="carrier_ein",
                        mapped_rule_key="schedule_a_part_i_1b_insurance_carrier_ein",
                        mapped_label="1b. Insurance Carrier EIN",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="36-2739571",
                        proposed_value="36-2739571",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1d. Contract / Policy Number",
                        normalized_field_name="contract",
                        mapped_rule_key="schedule_a_part_i_1d_contract_policy_number",
                        mapped_label="1d. Contract / Policy Number",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="1246876",
                        proposed_value="1246876",
                    ),
                ]
            )
        )

        fake_ftw = FakeFTWilliamsCurrentTagService()
        service = FTWilliamsReviewService(fake_ftw)
        prepared = run_async(service.prepare_review(filing.id, send_queries=True))
        selected = run_async(
            service.select_schedule_a_match(
                filing.id,
                FTWilliamsScheduleAMatchRequest(ftw_seq_no="3"),
            )
        )

        self.assertGreaterEqual(len(prepared.schedule_a_candidates), 2)
        self.assertEqual({candidate["ftw_seq_no"] for candidate in prepared.schedule_a_candidates}, {"1", "3"})
        self.assertEqual({candidate["ftw_seq_no"] for candidate in selected.schedule_a_candidates}, {"1", "3"})
        self.assertEqual(selected.schedule_a_match["ftw_seq_no"], "3")

    def test_prepare_review_omits_5500_update_when_current_5500_is_unavailable(self):
        repo = repositories.get_repository()
        filing = run_async(repo.create_filing(sample_filing()))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="sponsor_ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="73-1185740",
                        proposed_value="73-1185740",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Plan Number (PN)",
                        normalized_field_name="plan_number",
                        mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                        mapped_label="1b. Plan Number (PN)",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="501",
                        proposed_value="501",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="7. Plan Year Ending Date",
                        normalized_field_name="plan_year_end",
                        mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                        mapped_label="7. Plan Year Ending Date",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="09/30/2025",
                        proposed_value="09/30/2025",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1a. Name of Insurance Company",
                        normalized_field_name="carrier",
                        mapped_rule_key="schedule_a_part_i_1a_name_of_insurance_company",
                        mapped_label="1a. Name of Insurance Company",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="UnitedHealthcare Insurance Company",
                        proposed_value="UnitedHealthcare Insurance Company",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1d. Contract / Policy Number",
                        normalized_field_name="contract",
                        mapped_rule_key="schedule_a_part_i_1d_contract_policy_number",
                        mapped_label="1d. Contract / Policy Number",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="1246876",
                        proposed_value="1246876",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="3a. Name of Agent/Broker/Person",
                        normalized_field_name="broker_name",
                        mapped_rule_key="schedule_a_part_i_3a_name_of_agent_broker_person",
                        mapped_label="3a. Name of Agent/Broker/Person",
                        form_type=FormType.SCHEDULE_A,
                        source_document_type=DocumentType.SCHEDULE_A,
                        priority=FieldPriority.HIGH,
                        value="New Broker Name",
                        proposed_value="New Broker Name",
                    ),
                ]
            )
        )

        review = run_async(FTWilliamsReviewService(FakeFTWilliamsScheduleOnlyService()).prepare_review(filing.id, send_queries=True))

        self.assertTrue(review.current_query_success)
        self.assertIn("Could not locate form", review.error_message or "")
        self.assertNotIn("<DOL5500Data>", review.update_xml_5500 or "")
        self.assertIn("<DOLScheduleAData>", review.update_xml_schedule_a or "")
        self.assertNotIn("<FTWSeqNo>", review.update_xml_schedule_a or "")
        self.assertIn("<InsCarrierName>UnitedHealthcare Insurance Company</InsCarrierName>", review.update_xml_schedule_a or "")
        self.assertIn("<InsContractNum>1246876</InsContractNum>", review.update_xml_schedule_a or "")
        self.assertIn("<NameXX>New Broker Name</NameXX>", review.update_xml_schedule_a or "")

    def test_prepare_review_prefers_package_filing_year_for_ftw_queries(self):
        repo = repositories.get_repository()
        filing = sample_filing()
        filing.package_documents = [
            {
                "file_name": "1. Medical_Schedule_A.pdf",
                "filing_year": "2024",
                "sharefile_path": "Folders > ERISA Pros > Midwest > 2024 Filing > Schedule A's > 1. Medical_Schedule_A.pdf",
            }
        ]
        filing = run_async(repo.create_filing(filing))
        run_async(
            repo.add_fields(
                [
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1e. Plan Sponsor EIN",
                        normalized_field_name="sponsor_ein",
                        mapped_rule_key="form_5500_part_i_1e_plan_sponsor_ein",
                        mapped_label="1e. Plan Sponsor EIN",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="73-1185740",
                        proposed_value="73-1185740",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="1b. Plan Number (PN)",
                        normalized_field_name="plan_number",
                        mapped_rule_key="form_5500_part_i_1b_plan_number_pn",
                        mapped_label="1b. Plan Number (PN)",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="501",
                        proposed_value="501",
                    ),
                    ExtractedField(
                        filing_id=filing.id,
                        source_field_name="7. Plan Year Ending Date",
                        normalized_field_name="plan_year_end",
                        mapped_rule_key="form_5500_part_i_7_plan_year_ending_date",
                        mapped_label="7. Plan Year Ending Date",
                        form_type=FormType.FORM_5500,
                        source_document_type=DocumentType.PLAN_WORKSHEET,
                        priority=FieldPriority.HIGH,
                        value="09/30/2025",
                        proposed_value="09/30/2025",
                    ),
                ]
            )
        )

        fake_ftw = FakeFTWilliamsCurrentTagService()
        review = run_async(FTWilliamsReviewService(fake_ftw).prepare_review(filing.id, send_queries=True))

        query_5500_call = next(call for call in fake_ftw.calls if call.operation == "query_5500")
        self.assertEqual(query_5500_call.year, "2024")
        self.assertEqual(review.year, "2024")
        self.assertEqual(review.comparison_year, "2024")
        self.assertTrue(any(field.current_value for field in review.fields if field.form_type == FormType.FORM_5500))

    def test_equitable_schedule_a_mismatched_year_blocks_unsafe_update_fields(self):
        repo = repositories.get_repository()
        filing = sample_filing()
        filing.package_documents = [
            {
                "file_name": "5500 Plan Worksheet - Harold Brothers.docx",
                "filing_year": "2024",
                "sharefile_path": "Folders > ERISA Pros > Harold > 2024 Filing > 5500 Plan Worksheet.docx",
            },
            {
                "file_name": "schedule A.pdf",
                "filing_year": "2024",
                "sharefile_path": "Folders > ERISA Pros > Harold > 2024 Filing > Schedule A's > schedule A.pdf",
            },
        ]
        filing = run_async(repo.create_filing(filing))

        def field(
            rule_key: str,
            label: str,
            value: str,
            form_type: FormType,
            source_document_type: DocumentType,
            priority: FieldPriority = FieldPriority.HIGH,
        ) -> ExtractedField:
            return ExtractedField(
                filing_id=filing.id,
                source_field_name=label,
                normalized_field_name=label.lower(),
                mapped_rule_key=rule_key,
                mapped_label=label,
                form_type=form_type,
                source_document_type=source_document_type,
                priority=priority,
                value=value,
                proposed_value=value,
            )

        run_async(
            repo.add_fields(
                [
                    field("form_5500_part_i_1a_plan_name", "1a. Plan Name", "HAROLD BROTHERS MECHANICAL CONTRACTORS INC. LIFE AND DISABILITY PLAN", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_1b_plan_number_pn", "1b. Plan Number (PN)", "501", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_1d_plan_sponsor_name", "1d. Plan Sponsor Name", "HAROLD BROTHERS MECHANICAL CONTRACTORS INC.", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_1e_plan_sponsor_ein", "1e. Plan Sponsor EIN", "26-3189470", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_6_plan_year_beginning_date", "6. Plan Year Beginning Date", "10-01-2024", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("form_5500_part_i_7_plan_year_ending_date", "7. Plan Year Ending Date", "09-30-2025", FormType.FORM_5500, DocumentType.PLAN_WORKSHEET),
                    field("schedule_a_part_i_1a_name_of_insurance_company", "1a. Name of Insurance Company", "Equitable Financial Life Insurance Company of America", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1c_naic_code", "1c. NAIC Code", "78077", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1d_contract_policy_number", "1d. Contract/Policy Number", "011335", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1e_persons_covered_end_of_policy_year", "1e. Persons Covered (End of Policy Year)", "279", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1f_policy_year_beginning_date", "1f. Policy Year Beginning Date", "2023-10-01", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1g_policy_year_ending_date", "1g. Policy Year Ending Date", "2024-09-30", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_3a_name_of_agent_broker_person", "3a. Name of Agent/Broker/Person", "Harold Brothers Mechanical Contractors, Inc.", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_3d_purpose", "3d. Purpose", "3 Service Fee", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_3e_organizational_code", "3e. Organizational Code", "16", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_iv_4c_sponsor_ein", "4c. Sponsor EIN", "86-0222062", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier", "10a. Total premiums or subscription charges paid to carrier", "9959.14", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                ]
            )
        )

        review = run_async(FTWilliamsReviewService(FakeFTWilliamsEquitableMismatchService()).prepare_review(filing.id, send_queries=True))

        self.assertTrue(review.current_query_success)
        self.assertEqual(review.schedule_a_match["ftw_seq_no"], "1")
        self.assertIn("Schedule A updates blocked", review.error_message or "")
        self.assertNotIn("<DOLScheduleAData>", review.update_xml_schedule_a or "")
        changed_schedule_fields = [field for field in review.fields if field.form_type == FormType.SCHEDULE_A and field.changed]
        self.assertTrue(changed_schedule_fields)
        by_rule = {field.rule_key: field for field in review.fields}
        policy_begin = by_rule["schedule_a_part_i_1f_policy_year_beginning_date"]
        self.assertTrue(policy_begin.changed)
        self.assertTrue(policy_begin.update_included)

        with self.assertRaisesRegex(ValueError, "Resolve the plan year conflict"):
            run_async(
                FTWilliamsReviewService(FakeFTWilliamsEquitableMismatchService()).approve_and_update(
                    filing.id,
                    send_to_ftw=False,
                )
            )

        resolved = run_async(
            FTWilliamsReviewService(FakeFTWilliamsEquitableMismatchService()).resolve_plan_year_conflict(
                filing.id,
                "USE_WORKSHEET",
            )
        )

        self.assertEqual(resolved.plan_year_resolution, "USE_WORKSHEET")
        self.assertIn("<DOL5500Data>", resolved.update_xml_5500 or "")
        self.assertIn("<DOLScheduleAData>", resolved.update_xml_schedule_a or "")
        self.assertIn("<PlanYearBeginDate>10/01/2024</PlanYearBeginDate>", resolved.update_xml_5500 or "")
        self.assertIn("<PlanYearBeginDate>10/01/2024</PlanYearBeginDate>", resolved.update_xml_schedule_a or "")
        self.assertNotIn("plan year does not match", (resolved.error_message or "").lower())

        kept_current = run_async(
            FTWilliamsReviewService(FakeFTWilliamsEquitableMismatchService()).resolve_plan_year_conflict(
                filing.id,
                "KEEP_FTW",
            )
        )

        self.assertEqual(kept_current.plan_year_resolution, "KEEP_FTW")
        self.assertEqual(kept_current.plan_year_resolution_begin, "01/01/2024")
        self.assertEqual(kept_current.plan_year_resolution_end, "09/30/2025")
        self.assertIn("<PlanYearBeginDate>01/01/2024</PlanYearBeginDate>", kept_current.update_xml_schedule_a or "")
        self.assertIn("<PlanYearEndDate>09/30/2025</PlanYearEndDate>", kept_current.update_xml_schedule_a or "")
        self.assertNotIn("plan year does not match", (kept_current.error_message or "").lower())

    def test_update_readback_waits_for_delayed_ftw_schedule_a_convergence(self):
        service = FTWilliamsReviewService(FakeFTWilliamsService())
        mismatch = {
            "success": False,
            "mismatches": [{"form": "DOLScheduleAData", "tag": "Broker[2]/NameXX"}],
            "request_xmls": ["request"],
            "response_xmls": ["response"],
        }
        success = {
            "success": True,
            "mismatches": [],
            "request_xmls": ["request-final"],
            "response_xmls": ["response-final"],
        }
        service._verify_update_readback_once = AsyncMock(
            side_effect=[mismatch, mismatch, mismatch, success]
        )

        with patch("app.services.ftwilliams_review.asyncio.sleep", new=AsyncMock()) as sleep:
            result = run_async(service._verify_update_readback(FTWilliamsReview(filing_id="filing-1")))

        self.assertTrue(result["success"])
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(service._verify_update_readback_once.await_count, 4)
        self.assertEqual(sleep.await_args_list[0].args, (1,))
        self.assertEqual(sleep.await_args_list[1].args, (2,))
        self.assertEqual(sleep.await_args_list[2].args, (4,))

    def test_readback_documents_keep_original_ftw_sequences_for_identity_poor_rows(self):
        review = FTWilliamsReview(
            filing_id="filing-1",
            update_xml_schedule_a=(
                "<ftwLink><DataBatch>"
                "<DOLScheduleAData><InsPrsnCoveredEoyCnt>61</InsPrsnCoveredEoyCnt></DOLScheduleAData>"
                "<DOLScheduleAData><InsContractNum>1246876</InsContractNum></DOLScheduleAData>"
                "</DataBatch></ftwLink>"
            ),
            schedule_a_records=[
                {"ftw_seq_no": "1", "query_results": {"InsPrsnCoveredEoyCnt": "61"}},
                {"ftw_seq_no": "2", "query_results": {"InsContractNum": "1246876"}},
            ],
        )

        documents = FTWilliamsReviewService(FakeFTWilliamsService())._schedule_update_documents_with_sequences(review)

        self.assertEqual(documents[0]["__ftw_seq_no"], "1")
        self.assertEqual(documents[1]["__ftw_seq_no"], "2")

    def test_schedule_a_payload_error_reports_exact_invalid_broker_field(self):
        review = FTWilliamsReview(
            filing_id="filing-1",
            error_message=(
                "FT Williams pre-send validation failed: "
                "City8:FORT WORTH ST: TX ZIP: 76107-5739 (maximum length is 30 characters)"
            ),
            fields=[
                FTWilliamsComparisonField(
                    label="10a. Total premiums",
                    form_type=FormType.SCHEDULE_A,
                    changed=True,
                    update_included=True,
                )
            ],
            update_xml_schedule_a="",
        )

        message = FTWilliamsReviewService(FakeFTWilliamsService())._missing_required_schedule_a_payload(review)

        self.assertEqual(
            message,
            "Broker row 8 - City: maximum length is 30 characters. Current value: FORT WORTH ST: TX ZIP: 76107-5739",
        )


if __name__ == "__main__":
    unittest.main()
