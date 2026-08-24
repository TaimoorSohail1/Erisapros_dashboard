from __future__ import annotations

import xml.etree.ElementTree as ET
import asyncio
import base64
import binascii
from html import escape
import re
import time

import httpx

from app.config import get_settings
from app.models import FTWilliamsEditCheckIssue, FTWilliamsQueryRequest, FTWilliamsQueryResponse, FTWilliamsStatusItem


FTWILLIAMS_QUERY_OPERATIONS = {
    "archive_5500_ein_lookup",
    "archive_5500_get_data",
    "query_company",
    "query_plan",
    "plan_ids_batch",
    "query_schedule_a",
    "query_5500",
    "edit_checks_5500",
    "run_all_tests",
}


_EDIT_CHECK_GUIDANCE: dict[str, tuple[str, str, str, str]] = {
    "FW-117": (
        "1e",
        "1e. Persons Covered (End of Policy Year)",
        "Blank",
        "Enter the number of people covered at the end of the policy or contract year.",
    ),
    "FW-410": (
        "10a",
        "10a. Total premiums or subscription charges paid to carrier",
        "Blank",
        "Enter the total premiums or subscription charges paid to the carrier.",
    ),
    "FW-999": (
        "3c/3d",
        "3c/3d. Broker fee amount or purpose",
        "Blank or incomplete",
        "Complete the broker fee amount and its purpose for every applicable broker row.",
    ),
    "FW-617": (
        "3",
        "3. Brokers and service providers",
        "Not in descending payment order",
        "Order broker/provider rows in descending order, from the highest amount paid to the lowest, in FT Williams.",
    ),
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

        started_at = time.perf_counter()
        try:
            response = await self._post_xml(settings.ftwlink_endpoint_url, request_xml)
        except httpx.HTTPError as exc:
            return FTWilliamsQueryResponse(
                operation=payload.operation,
                configured=True,
                sent=True,
                request_xml=masked_request_xml,
                error=str(exc),
                elapsed_ms=round((time.perf_counter() - started_at) * 1000),
            )

        parsed = self.parse_response(response.text)
        response_success = self.response_success(parsed)
        if payload.operation.strip().lower() == "edit_checks_5500":
            response_success = self.edit_checks_success(parsed)
        return FTWilliamsQueryResponse(
            operation=payload.operation,
            configured=True,
            sent=True,
            request_xml=masked_request_xml,
            http_status=response.status_code,
            success=response.is_success and response_success,
            statuses=parsed,
            raw_response=response.text,
            error=None if response.is_success else response.text[:500],
            response_headers=self._diagnostic_headers(response),
            elapsed_ms=round((time.perf_counter() - started_at) * 1000),
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
        if operation == "plan_ids_batch":
            values = {"TransactionType": "Q"}
            if payload.ftw_customer_id:
                values["FTWCustomerID"] = payload.ftw_customer_id
            return self._ftwlink_xml("PlanIDs_Batch", values)
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
        # Edit Check results contain a nested leaf named ``Status`` inside
        # QueryResults. Only elements carrying FT's response envelope fields are
        # actual response records.
        status_elements = [
            status
            for status in root.findall(".//Status")
            if status.find("Type") is not None or status.find("ErrorCode") is not None
        ]
        for status in status_elements:
            query_results_element = status.find("QueryResults")
            query_results = self._child_text_map(query_results_element)
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
                    query_subparts=self._child_record_lists(query_results_element),
                    query_result_record_count=self._query_result_record_count(query_results_element),
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

    def parse_plan_ids_batch_response(self, response_xml: str) -> list[dict[str, str]]:
        """Return the identifier tuples exposed by FT Williams PlanIDs_Batch.

        ftwLink installations have returned this operation both as one Status
        per plan and as nested plan rows. Parse both shapes without depending
        on a user-defined CustomerID convention.
        """
        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError:
            return []

        identifier_keys = {"CustomerID", "PlanID", "FTWCustomerID", "FTWPlanID"}
        records: list[dict[str, str]] = []
        record_positions: dict[tuple[str, str, str, str], int] = {}

        def add(values: dict[str, str]) -> None:
            cleaned = {
                key: str(value or "").strip()
                for key, value in values.items()
                if key not in {"Type", "ErrorCode", "ErrorDesc"} and str(value or "").strip()
            }
            present = identifier_keys.intersection(cleaned)
            has_user_pair = {"CustomerID", "PlanID"}.issubset(present)
            has_ftw_pair = {"FTWCustomerID", "FTWPlanID"}.issubset(present)
            if not (has_user_pair or has_ftw_pair):
                return
            signature = tuple(cleaned.get(key, "") for key in ["CustomerID", "PlanID", "FTWCustomerID", "FTWPlanID"])
            existing_position = record_positions.get(signature)
            if existing_position is not None:
                records[existing_position].update(cleaned)
                return
            record_positions[signature] = len(records)
            records.append(cleaned)

        for status in root.findall(".//Status"):
            values = {
                key: self._text(status, key) or ""
                for key in ["CustomerID", "PlanID", "FTWCustomerID", "FTWPlanID"]
            }
            values.update(self._child_text_map(status.find("QueryResults")))
            add(values)

        for element in root.iter():
            direct_leaf_values = {
                child.tag: (child.text or "").strip()
                for child in list(element)
                if not list(child) and (child.text or "").strip()
            }
            add(direct_leaf_values)

        return records

    def response_success(self, statuses: list[FTWilliamsStatusItem]) -> bool:
        return bool(statuses) and all(str(status.error_code or "") == "0" for status in statuses)

    def edit_checks_success(self, statuses: list[FTWilliamsStatusItem]) -> bool:
        return self.response_success(statuses) and all(
            str(status.query_results.get("Status") or "").strip().upper() == "OK"
            for status in statuses
        )

    def parse_edit_check_issues(self, statuses: list[FTWilliamsStatusItem]) -> list[FTWilliamsEditCheckIssue]:
        issues: list[FTWilliamsEditCheckIssue] = []
        for status in statuses:
            status_type = str(status.type or "").strip() or None
            query_results = status.query_results or {}
            form_type = (
                "SCHEDULE_A"
                if str(status_type or "").upper().startswith("DOLSCHEDULEA")
                else "FORM_5500"
                if str(status_type or "").upper().startswith("DOL5500")
                else "FTW"
            )
            sequence_match = re.search(r"DOLScheduleA_(\d+)_Data", str(status_type or ""), re.IGNORECASE)
            schedule_seq_no = str(
                query_results.get("SeqNo")
                or status.ftw_seq_no
                or (sequence_match.group(1) if sequence_match else "")
            ).strip() or None
            schedule_desc = str(query_results.get("ScheduleDesc") or "").strip() or None
            for code, raw_message in query_results.items():
                clean_code = str(code or "").strip().upper()
                if not re.fullmatch(r"FW-\d+", clean_code):
                    continue
                message = re.sub(r"^\s*(?:Warning|Error)\s*:::\s*", "", str(raw_message or ""), flags=re.IGNORECASE)
                message = re.sub(r"<br\s*/?>", " ", message, flags=re.IGNORECASE)
                message = re.sub(r"\s+", " ", message).strip()
                guidance = _EDIT_CHECK_GUIDANCE.get(clean_code)
                line_match = re.search(r"\bLine\s+([0-9]+[A-Za-z]?(?:\([^)]*\))?)", message, re.IGNORECASE)
                field_line = guidance[0] if guidance else (line_match.group(1) if line_match else None)
                issues.append(
                    FTWilliamsEditCheckIssue(
                        code=clean_code,
                        message=message or "FT Williams reported an Edit Check issue.",
                        status_type=status_type,
                        form_type=form_type,
                        schedule_seq_no=schedule_seq_no,
                        schedule_desc=schedule_desc,
                        field_line=field_line,
                        field_label=guidance[1] if guidance else (f"Line {field_line}" if field_line else "FT Williams field"),
                        current_value=guidance[2] if guidance else "Invalid or incomplete",
                        correction=guidance[3] if guidance else (message or "Correct this value in FT Williams, then run Edit Checks again."),
                    )
                )
        return issues

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

        started_at = time.perf_counter()
        try:
            response = await self._post_xml(settings.ftwlink_endpoint_url, request_xml)
        except httpx.HTTPError as exc:
            return FTWilliamsQueryResponse(
                operation=operation,
                configured=True,
                sent=True,
                request_xml=masked_request_xml,
                error=str(exc),
                elapsed_ms=round((time.perf_counter() - started_at) * 1000),
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
            response_headers=self._diagnostic_headers(response),
            elapsed_ms=round((time.perf_counter() - started_at) * 1000),
        )

    def build_generate_dol_request(
        self,
        *,
        ftw_customer_id: str,
        ftw_plan_id: str,
        year: str,
        document: str = "A",
        ftw_seq_no: str | None = None,
    ) -> str:
        document_text = str(document or "ScheduleA").strip()
        document_code = {
            "A": "ScheduleA",
            "SCHEDULEA": "ScheduleA",
            "ALL": "All",
        }.get(document_text.upper(), document_text)
        values = {
            "FTWCustomerID": str(ftw_customer_id or "").strip(),
            "FTWPlanID": str(ftw_plan_id or "").strip(),
            "Type": "DOL",
            "Document": document_code,
            "Year": str(year or "").strip(),
        }
        if document_code == "ScheduleA":
            values["FTWSeqNo"] = str(ftw_seq_no or "").strip()
        missing = [key for key in ("FTWCustomerID", "FTWPlanID", "Year") if not values[key]]
        if document_code == "ScheduleA" and not values["FTWSeqNo"]:
            missing.append("FTWSeqNo")
        if missing:
            raise ValueError("GenerateDocument requires " + ", ".join(missing))
        key_id = get_settings().ftwlink_key_id or ""
        body = "".join(f"<{key}>{escape(value)}</{key}>" for key, value in values.items())
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            f"<ftwLink><KeyID>{escape(key_id)}</KeyID><GenerateDocument>{body}</GenerateDocument></ftwLink>"
        )

    @staticmethod
    def parse_document_data(response_xml: str) -> bytes | None:
        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError:
            return None
        encoded = "".join((root.findtext(".//DocumentData") or "").split())
        if not encoded:
            return None
        try:
            return base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return None

    async def generate_dol_document(
        self,
        *,
        ftw_customer_id: str,
        ftw_plan_id: str,
        year: str,
        document: str = "A",
        ftw_seq_no: str | None = None,
    ) -> tuple[FTWilliamsQueryResponse, bytes | None]:
        request_xml = self.build_generate_dol_request(
            ftw_customer_id=ftw_customer_id,
            ftw_plan_id=ftw_plan_id,
            year=year,
            document=document,
            ftw_seq_no=ftw_seq_no,
        )
        response = await self.send_xml("generate_dol_document", request_xml)
        data = self.parse_document_data(response.raw_response or "") if response.success else None
        if data is not None and not data.startswith(b"%PDF"):
            data = None
        return response, data

    @staticmethod
    def _diagnostic_headers(response: httpx.Response) -> dict[str, str]:
        allowed = {
            "content-type",
            "content-length",
            "x-request-id",
            "x-amzn-requestid",
            "x-amz-request-id",
            "cf-ray",
        }
        return {
            key.lower(): value
            for key, value in response.headers.items()
            if key.lower() in allowed
        }

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

    def _child_record_lists(self, element: ET.Element | None) -> dict[str, list[dict[str, str]]]:
        if element is None:
            return {}
        records: dict[str, list[dict[str, str]]] = {}

        def add_record(record: ET.Element) -> None:
            values = self._child_text_map(record)
            records.setdefault(record.tag, []).append(values)

        for child in list(element):
            if not list(child):
                continue
            if child.tag == "DOLSubPartData":
                for record in list(child):
                    add_record(record)
            else:
                add_record(child)
        return records

    def _query_result_record_count(self, element: ET.Element | None) -> int:
        """Detect FT's combined Schedule A response without flattening records together.

        A no-sequence Schedule A query can return multiple records inside one
        QueryResults element by repeating every direct document field. Nested
        Broker rows are intentionally excluded because a single Schedule A can
        legitimately contain several of them.
        """
        if element is None:
            return 1
        direct_leaf_counts: dict[str, int] = {}
        for child in list(element):
            if list(child):
                continue
            direct_leaf_counts[child.tag] = direct_leaf_counts.get(child.tag, 0) + 1
        return max(direct_leaf_counts.values(), default=1)

    def _field_list(self, value: str | None) -> list[str]:
        if not value:
            return []
        return [field.strip() for field in value.replace("\n", ",").split(",") if field.strip()]

    def _masked_secret(self, value: str) -> str:
        if len(value) <= 10:
            return "***"
        return f"{value[:4]}...{value[-4:]}"
