import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.repositories as repositories
from app.models import (
    DocumentType,
    ExtractedField,
    ExtractedFieldStatus,
    FieldPriority,
    Filing,
    FilingStatus,
    FormType,
    FTWilliamsManualMatchRequest,
    FTWilliamsPlanLookupStatus,
    FTWilliamsQueryResponse,
    FTWilliamsReview,
    FTWilliamsReviewStatus,
    FTWilliamsScheduleAMatchRequest,
    FTWilliamsStatusItem,
    ScheduleABrokerRow,
)
from app.services.ftwilliams import FTWilliamsService
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.ftwilliams_tags import resolve_ftw_tag
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
                        query_results={"PLAN_NAME0": "Crest Discount Foods, Inc. Flexible Benefits Plan"},
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
            if payload.year == "2024":
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
                        query_results={"PLAN_NAME0": "Midwest Hose & Specialty Health and Welfare Benefits Plan"},
                    )
                ],
            )
        if payload.operation == "query_schedule_a":
            if payload.year == "2024":
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
                                "InsContractNum": "1246876",
                                "Name1": "NFP LLC",
                                "CommPdAmt01": "111893",
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
                            "PlanYearBeginDate": "10/01/2024",
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
    def setUp(self):
        repositories._repository = repositories.MemoryRepository()

    def tearDown(self):
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

        self.assertEqual(resolve_ftw_tag(sponsor_field), "SPONS_DFE_NAME0")

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
        self.assertNotIn("<PLAN_NAME0>ABC Plan</PLAN_NAME0>", xml)
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

        review = run_async(FTWilliamsReviewService(FakeFTWilliamsCurrentTagService()).prepare_review(filing.id, send_queries=True))
        by_label = {field.label: field for field in review.fields}

        self.assertEqual(by_label["13. Active participants at beginning"].current_value, "249")
        self.assertEqual(by_label["14. Active participants at end"].current_value, "279")
        self.assertEqual(by_label["12. Total participants at end of year"].current_value, "298")
        self.assertEqual(by_label["1e. Plan Sponsor EIN"].current_value, "73-1185740")
        self.assertEqual(by_label["3a. Name of Agent/Broker/Person"].current_value, "NFP LLC")
        self.assertEqual(by_label["3b. Amount of Commissions"].current_value, "111893")
        self.assertEqual(len(review.schedule_a_candidates), 2)
        self.assertEqual(review.schedule_a_candidates[0]["ftw_seq_no"], "1")
        self.assertEqual({record["ftw_seq_no"] for record in review.schedule_a_records}, {"1", "3"})

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
                    field("schedule_a_part_i_1a_name_of_insurance_company", "1a. Name of Insurance Company", "UnitedHealthcare", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_1d_contract_policy_number", "1d. Contract/Policy Number", "1246876", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                    field("schedule_a_part_i_3b_amount_of_commissions", "3b. Amount of Commissions", "111893", FormType.SCHEDULE_A, DocumentType.SCHEDULE_A),
                ]
            )
        )
        commission_field = next(field for field in fields if field.mapped_rule_key == "schedule_a_part_i_3b_amount_of_commissions")
        fake_ftw = FakeFTWilliamsCurrentTagService()
        service = FTWilliamsReviewService(fake_ftw)

        queried = run_async(service.prepare_review(filing.id, send_queries=True))
        self.assertTrue(queried.current_query_success)
        self.assertEqual(queried.schedule_a_match["ftw_seq_no"], "1")
        self.assertEqual({record["ftw_seq_no"] for record in queried.schedule_a_records}, {"1", "3"})

        call_count = len(fake_ftw.calls)
        run_async(repo.update_field(filing.id, commission_field.id, "112000"))
        refreshed = run_async(service.prepare_review(filing.id, send_queries=False))
        by_label = {field.label: field for field in refreshed.fields}

        self.assertEqual(len(fake_ftw.calls), call_count)
        self.assertTrue(refreshed.current_query_sent)
        self.assertTrue(refreshed.current_query_success)
        self.assertEqual(refreshed.schedule_a_match["ftw_seq_no"], "1")
        self.assertEqual(refreshed.ftw_seq_no, "1")
        self.assertEqual({record["ftw_seq_no"] for record in refreshed.schedule_a_records}, {"1", "3"})
        self.assertEqual(by_label["1e. Plan Sponsor EIN"].current_value, "73-1185740")
        self.assertEqual(by_label["1d. Contract/Policy Number"].current_value, "1246876")
        self.assertEqual(by_label["3b. Amount of Commissions"].current_value, "111893")
        self.assertEqual(by_label["3b. Amount of Commissions"].proposed_value, "112000")

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
        self.assertIn("<Name1>New Broker Name</Name1>", review.update_xml_schedule_a)
        self.assertIn("<InsCarrierName>Other Carrier</InsCarrierName>", review.update_xml_schedule_a)
        self.assertIn("<InsContractNum>OTHER-3</InsContractNum>", review.update_xml_schedule_a)

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

        review = run_async(FTWilliamsReviewService(FakeFTWilliamsCurrentTagService()).prepare_review(filing.id, send_queries=True))
        by_label = {field.label: field for field in review.fields}

        self.assertEqual(len(review.schedule_a_broker_rows), 2)
        self.assertFalse(by_label["3a. Name of Agent/Broker/Person"].update_included)
        self.assertFalse(by_label["3b. Amount of Commissions"].update_included)
        self.assertIn("<Name1>NFP CORPORATE SERVICES NY LLC</Name1>", review.update_xml_schedule_a)
        self.assertIn("<CommPdAmt1>1,576</CommPdAmt1>", review.update_xml_schedule_a)
        self.assertIn("<FeesPdAmt1>44</FeesPdAmt1>", review.update_xml_schedule_a)
        self.assertIn("<FeesPdText1>COMMISSIONS AND FEES</FeesPdText1>", review.update_xml_schedule_a)
        self.assertIn("<Code1>03</Code1>", review.update_xml_schedule_a)
        self.assertIn("<Name2>NFP INS SERVICES INC</Name2>", review.update_xml_schedule_a)
        self.assertIn("<CommPdAmt2>422</CommPdAmt2>", review.update_xml_schedule_a)
        self.assertIn("<FeesPdAmt2>0</FeesPdAmt2>", review.update_xml_schedule_a)
        self.assertIn("<FeesPdText2>COMMISSIONS</FeesPdText2>", review.update_xml_schedule_a)
        self.assertNotIn("<CommPdAmt1>1,998</CommPdAmt1>", review.update_xml_schedule_a)
        self.assertIn("<InsCarrierName>Other Carrier</InsCarrierName>", review.update_xml_schedule_a)

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
        self.assertIn("<Name1>NFP LLC</Name1>", review.update_xml_schedule_a)

    def test_prepare_review_clears_stale_failed_update_state(self):
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
        self.assertIsNone(review.client_error)
        self.assertEqual(updated_filing.status, FilingStatus.APPROVED)
        self.assertIsNone(updated_filing.error_message)
        self.assertIn("FTWILLIAMS_UPDATE_FAILURE_CLEARED", events)

    def test_prepare_review_clears_stale_update_failure_for_approved_filing(self):
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
        self.assertEqual(updated_filing.status, FilingStatus.APPROVED)
        self.assertIsNone(updated_filing.error_message)

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
        self.assertIn("XML contains 1 Schedule A record(s) but 2 fetched record(s) must be preserved", error)

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

        self.assertEqual([call.operation for call in fake_ftw.calls], ["query_plan", "query_plan", "archive_5500_get_data"])
        self.assertEqual(review.plan_lookup.status, FTWilliamsPlanLookupStatus.NOT_FOUND)
        self.assertFalse(review.current_query_success)
        self.assertIn("Could not locate existing plan", review.error_message or "")
        self.assertEqual(review.customer_id, "33-0574214")
        self.assertEqual(review.plan_id, "33-0574214501")

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

    def test_prepare_review_falls_back_to_prior_year_when_target_year_has_no_ftw_data(self):
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
                ]
            )
        )

        fake_ftw = FakeFTWilliamsFallbackService()
        review = run_async(FTWilliamsReviewService(fake_ftw).prepare_review(filing.id, send_queries=True))

        queried_5500_years = [call.year for call in fake_ftw.calls if call.operation == "query_5500"]
        self.assertEqual(queried_5500_years, ["2024", "2023"])
        self.assertTrue(review.current_query_success)
        self.assertEqual(review.year, "2024")
        self.assertEqual(review.comparison_year, "2023")
        self.assertEqual(review.comparison_year_source, "PRIOR_YEAR_FALLBACK")
        self.assertEqual(review.schedule_a_match["ftw_seq_no"], "4")
        self.assertIn("prior-year 2023", review.error_message)

    def test_manual_schedule_a_selection_uses_fallback_query_year_but_keeps_update_target_year(self):
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
        review = run_async(service.prepare_review(filing.id, send_queries=True))
        selected = run_async(
            service.select_schedule_a_match(
                filing.id,
                FTWilliamsScheduleAMatchRequest(ftw_seq_no="4"),
            )
        )

        schedule_calls = [call for call in fake_ftw.calls if call.operation == "query_schedule_a" and call.ftw_seq_no == "4"]
        self.assertTrue(any(call.year == "2023" for call in schedule_calls))
        self.assertEqual(review.comparison_year, "2023")
        self.assertEqual(selected.year, "2024")
        self.assertIn("<Year>2024</Year>", selected.update_xml_schedule_a)
        self.assertNotIn("<FTWSeqNo>", selected.update_xml_schedule_a)

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
        self.assertIn("<Name1>New Broker Name</Name1>", review.update_xml_schedule_a or "")

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
        self.assertFalse(any(field.update_included for field in changed_schedule_fields))


if __name__ == "__main__":
    unittest.main()
