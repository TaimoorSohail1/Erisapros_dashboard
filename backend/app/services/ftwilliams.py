from __future__ import annotations

import xml.etree.ElementTree as ET
import asyncio
from html import escape

import httpx

from app.config import get_settings
from app.models import FTWilliamsQueryRequest, FTWilliamsQueryResponse, FTWilliamsStatusItem


FTWILLIAMS_QUERY_OPERATIONS = {
    "archive_5500_ein_lookup",
    "archive_5500_get_data",
    "query_company",
    "query_plan",
    "query_schedule_a",
    "query_5500",
    "edit_checks_5500",
    "run_all_tests",
}


class FTWilliamsService:
    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._client_endpoint_url: str | None = None

    def status(self) -> dict:
        settings = get_settings()
        return {
            "configured": bool(settings.ftwlink_key_id and settings.ftwlink_endpoint_url),
            "key_configured": bool(settings.ftwlink_key_id),
            "endpoint_configured": bool(settings.ftwlink_endpoint_url),
            "endpoint_url": settings.ftwlink_endpoint_url,
            "sandbox_customer_plan_configured": bool(
                settings.ftwlink_sandbox_customer_id
                and settings.ftwlink_sandbox_plan_id
                and settings.ftwlink_sandbox_year
            ),
            "sandbox_ftw_ids_configured": bool(
                settings.ftwlink_sandbox_ftw_customer_id
                and settings.ftwlink_sandbox_ftw_plan_id
                and settings.ftwlink_sandbox_year_end
            ),
            "supported_operations": sorted(FTWILLIAMS_QUERY_OPERATIONS),
        }

    async def run_query(self, payload: FTWilliamsQueryRequest) -> FTWilliamsQueryResponse:
        settings = get_settings()
        request_xml = self.build_request_xml(payload)
        configured = bool(settings.ftwlink_key_id and settings.ftwlink_endpoint_url)
        masked_request_xml = self.mask_key_id(request_xml)

        if not payload.send:
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=configured,
                sent=False,
                request_xml=masked_request_xml,
            )

        if not configured:
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=False,
                sent=False,
                request_xml=masked_request_xml,
                error="FTWLINK_KEY_ID and FTWLINK_ENDPOINT_URL must be configured before sending requests.",
            )

        try:
            response = await self._post_xml(settings.ftwlink_endpoint_url, request_xml)
        except httpx.HTTPError as exc:
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=masked_request_xml,
                error=str(exc),
            )

        parsed = self.parse_response(response.text)
        return FTWilliamsQueryResponse(
            operation=payload.operation,
            configured=True,
            sent=True,
            request_xml=masked_request_xml,
            http_status=response.status_code,
            success=response.is_success and self.response_success(parsed),
            statuses=parsed,
            raw_response=response.text,
            error=None if response.is_success else response.text[:500],
        )

    def build_request_xml(self, payload: FTWilliamsQueryRequest) -> str:
        operation = payload.operation.strip().lower()
        if operation not in FTWILLIAMS_QUERY_OPERATIONS:
            raise ValueError(f"Unsupported FT Williams operation: {payload.operation}")

        if operation == "query_company":
            return self._ftwlink_xml(
                "CompanyData",
                {
                    "TransactionType": "Q",
                    **self._identifier_values(payload, require_plan=False),
                },
            )
        if operation == "archive_5500_get_data":
            return self._ftwlink_xml(
                "Archive5500",
                {
                    "TransactionType": "GetData",
                    "CompanyEmployerID": self._company_employer_id(payload),
                    "PlanNumber": self._plan_number(payload),
                },
            )
        if operation == "archive_5500_ein_lookup":
            return self._ftwlink_xml(
                "Archive5500",
                {
                    "TransactionType": "EINLookup",
                    "CompanyState": payload.company_state or "",
                    "CompanyName": payload.company_name or "",
                },
            )
        if operation == "query_plan":
            return self._ftwlink_xml(
                "PlanData",
                {
                    "TransactionType": "Q",
                    **self._identifier_values(payload, require_plan=True),
                },
            )
        if operation == "query_schedule_a":
            values = {
                "TransactionType": "Q",
                **self._identifier_values(payload, require_plan=True),
                "Year": self._year(payload),
            }
            if payload.ftw_seq_no:
                values["FTWSeqNo"] = payload.ftw_seq_no
            return self._ftwlink_xml("DOLScheduleAData", values)
        if operation == "query_5500":
            return self._ftwlink_xml(
                "DOL5500Data",
                {
                    "TransactionType": "Q",
                    **self._identifier_values(payload, require_plan=True),
                    "Year": self._year(payload),
                },
            )
        if operation == "edit_checks_5500":
            return self._ftwlink_xml(
                "EditChecks5500",
                {
                    **self._ftw_identifier_values(payload),
                    "Year": self._year(payload),
                    "TransactionType": "Q",
                    "ShowDetails": "1",
                },
            )
        return self._ftwlink_xml(
            "Compliance_TasksRunAll",
            {
                "FTWCustomerID": self._ftw_customer_id(payload),
                "FTWPlanID": self._ftw_plan_id(payload),
                "TransactionType": "Q",
                "YearEnd": self._year_end(payload),
            },
        )

    def parse_response(self, response_xml: str) -> list[FTWilliamsStatusItem]:
        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError as exc:
            return [
                FTWilliamsStatusItem(
                    error_code="PARSE_ERROR",
                    error_desc=str(exc),
                )
            ]

        statuses: list[FTWilliamsStatusItem] = []
        for status in root.findall(".//Status"):
            query_results = self._child_text_map(status.find("QueryResults"))
            status_success = self._text(status, "StatusSuccess")
            statuses.append(
                FTWilliamsStatusItem(
                    type=self._text(status, "Type"),
                    error_code=self._text(status, "ErrorCode"),
                    error_desc=self._text(status, "ErrorDesc"),
                    customer_id=self._text(status, "CustomerID"),
                    plan_id=self._text(status, "PlanID"),
                    ftw_customer_id=self._text(status, "FTWCustomerID"),
                    ftw_plan_id=self._text(status, "FTWPlanID"),
                    ftw_seq_no=self._text(status, "FTWSeqNo"),
                    plan_name=self._text(status, "PlanName"),
                    plan_year=self._text(status, "PlanYear") or self._text(status, "Year"),
                    status_success=status_success,
                    successful_fields=self._field_list(status_success),
                    query_results=query_results,
                )
            )
        return statuses

    def parse_archive_lookup_response(self, response_xml: str) -> list[dict[str, str]]:
        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError:
            return []

        matches: list[dict[str, str]] = []
        seen: set[tuple[tuple[str, str], ...]] = set()

        def add_match(values: dict[str, str]) -> None:
            cleaned = {key: value for key, value in values.items() if str(value or "").strip()}
            meaningful_keys = set(cleaned) - {"Type", "ErrorCode", "ErrorDesc"}
            if not meaningful_keys:
                return
            if not cleaned:
                return
            signature = tuple(sorted(cleaned.items()))
            if signature in seen:
                return
            seen.add(signature)
            matches.append(cleaned)

        status_elements = root.findall(".//Status")
        for status in status_elements:
            values = {
                "Type": self._text(status, "Type") or "",
                "ErrorCode": self._text(status, "ErrorCode") or "",
                "ErrorDesc": self._text(status, "ErrorDesc") or "",
                "CustomerID": self._text(status, "CustomerID") or "",
                "PlanID": self._text(status, "PlanID") or "",
                "FTWCustomerID": self._text(status, "FTWCustomerID") or "",
                "FTWPlanID": self._text(status, "FTWPlanID") or "",
            }
            values.update(self._child_text_map(status.find("QueryResults")))
            add_match(values)

        if not status_elements:
            for query_results in root.findall(".//QueryResults"):
                add_match(self._child_text_map(query_results))

        for data_batch in root.findall(".//DataBatch"):
            direct_values = {
                child.tag: (child.text or "").strip()
                for child in list(data_batch)
                if child.text and not list(child)
            }
            add_match(direct_values)

        return matches

    def response_success(self, statuses: list[FTWilliamsStatusItem]) -> bool:
        return bool(statuses) and all(str(status.error_code or "") == "0" for status in statuses)

    def mask_key_id(self, xml: str) -> str:
        key_id = get_settings().ftwlink_key_id
        if not key_id:
            return xml
        return xml.replace(key_id, self._masked_secret(key_id))

    async def send_xml(self, operation: str, request_xml: str) -> FTWilliamsQueryResponse:
        settings = get_settings()
        configured = bool(settings.ftwlink_key_id and settings.ftwlink_endpoint_url)
        masked_request_xml = self.mask_key_id(request_xml)
        if not configured:
            return FTWilliamsQueryResponse(
                operation=operation,
                configured=False,
                sent=False,
                request_xml=masked_request_xml,
                error="FTWLINK_KEY_ID and FTWLINK_ENDPOINT_URL must be configured before sending requests.",
            )

        try:
            response = await self._post_xml(settings.ftwlink_endpoint_url, request_xml)
        except httpx.HTTPError as exc:
            return FTWilliamsQueryResponse(
                operation=operation,
                configured=True,
                sent=True,
                request_xml=masked_request_xml,
                error=str(exc),
            )

        parsed = self.parse_response(response.text)
        return FTWilliamsQueryResponse(
            operation=operation,
            configured=True,
            sent=True,
            request_xml=masked_request_xml,
            http_status=response.status_code,
            success=response.is_success and self.response_success(parsed),
            statuses=parsed,
            raw_response=response.text,
            error=None if response.is_success else response.text[:500],
        )

    async def _post_xml(self, endpoint_url: str, request_xml: str) -> httpx.Response:
        last_error: httpx.HTTPError | None = None
        shared_client = await self._get_client(endpoint_url)
        for attempt in range(3):
            retry_client = None
            client = shared_client
            if attempt > 0:
                # A retry must not close the process-wide pooled client: other
                # concurrent FTW slot queries may still be using it. Give only
                # the failed request a fresh, short-lived connection instead.
                retry_client = httpx.AsyncClient(timeout=30)
                client = retry_client
            try:
                return await client.post(
                    endpoint_url,
                    content=request_xml.encode("utf-8"),
                    headers={"Content-Type": "application/xml"},
                )
            except httpx.ConnectError as exc:
                last_error = exc
                if attempt == 2:
                    raise
                await asyncio.sleep(0.2 * (attempt + 1))
            finally:
                if retry_client is not None:
                    await retry_client.aclose()
        if last_error:
            raise last_error
        raise httpx.ConnectError("FT Williams request failed without a response.")

    async def _get_client(self, endpoint_url: str, *, reset: bool = False) -> httpx.AsyncClient:
        if reset:
            await self._close_client()
        if self._client and self._client_endpoint_url == endpoint_url:
            return self._client
        await self._close_client()
        self._client = httpx.AsyncClient(timeout=30)
        self._client_endpoint_url = endpoint_url
        return self._client

    async def _close_client(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        self._client = None
        self._client_endpoint_url = None

    def _ftwlink_xml(self, data_tag: str, values: dict[str, str]) -> str:
        key_id = get_settings().ftwlink_key_id or "[FTWLINK_KEY_ID]"
        fields = "\n".join(f"      <{tag}>{escape(str(value))}</{tag}>" for tag, value in values.items())
        return f"""<?xml version="1.0" encoding="utf-8"?>
<ftwLink>
  <KeyID>{escape(key_id)}</KeyID>
  <DataBatch>
    <{data_tag}>
{fields}
    </{data_tag}>
  </DataBatch>
</ftwLink>"""

    def _customer_id(self, payload: FTWilliamsQueryRequest) -> str:
        value = payload.customer_id or get_settings().ftwlink_sandbox_customer_id
        if not value:
            raise ValueError("customer_id is required for this FT Williams request.")
        return value

    def _identifier_values(self, payload: FTWilliamsQueryRequest, require_plan: bool = True) -> dict[str, str]:
        settings = get_settings()
        payload_has_ftw_ids = bool(payload.ftw_customer_id and (payload.ftw_plan_id or not require_plan))
        if payload_has_ftw_ids:
            return self._ftw_identifier_values(payload, require_plan=require_plan)
        if payload.customer_id or settings.ftwlink_sandbox_customer_id:
            values = {"CustomerID": self._customer_id(payload)}
            if require_plan:
                values["PlanID"] = self._plan_id(payload)
            return values
        return self._ftw_identifier_values(payload, require_plan=require_plan)

    def _ftw_identifier_values(self, payload: FTWilliamsQueryRequest, require_plan: bool = True) -> dict[str, str]:
        values = {"FTWCustomerID": self._ftw_customer_id(payload)}
        if require_plan:
            values["FTWPlanID"] = self._ftw_plan_id(payload)
        return values

    def _plan_id(self, payload: FTWilliamsQueryRequest) -> str:
        value = payload.plan_id or get_settings().ftwlink_sandbox_plan_id
        if not value:
            raise ValueError("plan_id is required for this FT Williams request.")
        return value

    def _year(self, payload: FTWilliamsQueryRequest) -> str:
        value = payload.year or get_settings().ftwlink_sandbox_year
        if not value:
            raise ValueError("year is required for this FT Williams request.")
        return value

    def _ftw_customer_id(self, payload: FTWilliamsQueryRequest) -> str:
        value = payload.ftw_customer_id or get_settings().ftwlink_sandbox_ftw_customer_id
        if not value:
            raise ValueError("ftw_customer_id is required for this FT Williams request.")
        return value

    def _ftw_plan_id(self, payload: FTWilliamsQueryRequest) -> str:
        value = payload.ftw_plan_id or get_settings().ftwlink_sandbox_ftw_plan_id
        if not value:
            raise ValueError("ftw_plan_id is required for this FT Williams request.")
        return value

    def _year_end(self, payload: FTWilliamsQueryRequest) -> str:
        value = payload.year_end or get_settings().ftwlink_sandbox_year_end
        if not value:
            raise ValueError("year_end is required for this FT Williams request.")
        return value

    def _company_employer_id(self, payload: FTWilliamsQueryRequest) -> str:
        value = payload.company_employer_id
        if not value:
            raise ValueError("company_employer_id is required for this FT Williams archive lookup.")
        return value

    def _plan_number(self, payload: FTWilliamsQueryRequest) -> str:
        value = payload.plan_number
        if not value:
            raise ValueError("plan_number is required for this FT Williams archive lookup.")
        return value

    def _text(self, element: ET.Element, tag: str) -> str | None:
        child = element.find(tag)
        if child is None or child.text is None:
            return None
        return child.text.strip()

    def _child_text_map(self, element: ET.Element | None) -> dict[str, str]:
        if element is None:
            return {}
        values: dict[str, str] = {}
        for child in list(element):
            self._collect_child_text(values, child)
        return values

    def _collect_child_text(self, values: dict[str, str], element: ET.Element) -> None:
        children = list(element)
        text = (element.text or "").strip()
        if not children:
            if text and not values.get(element.tag):
                values[element.tag] = text
            return
        if text and not values.get(element.tag):
            values[element.tag] = text
        for child in children:
            self._collect_child_text(values, child)

    def _field_list(self, value: str | None) -> list[str]:
        if not value:
            return []
        return [field.strip() for field in value.replace("\n", ",").split(",") if field.strip()]

    def _masked_secret(self, value: str) -> str:
        if len(value) <= 10:
            return "***"
        return f"{value[:4]}...{value[-4:]}"
