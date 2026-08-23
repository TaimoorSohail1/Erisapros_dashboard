from __future__ import annotations

import base64
import binascii
import csv
from datetime import datetime, timedelta
import io
import re
import xml.etree.ElementTree as ET
from html import escape

from app.config import get_settings
from app.models import (
    FTWilliamsSchemaField,
    FTWilliamsSchemaSnapshot,
    FTWilliamsSchemaValidationIssue,
    FTWilliamsSchemaValidationResult,
    FormType,
)
from app.services.ftwilliams_contract import (
    BUSINESS_CODE_TAGS,
    DATE_TAGS,
    EIN_TAGS,
    FTW_CONTRACT_VERSION,
    FTWPayloadValidationError,
    INTEGER_TAGS,
    NAIC_TAGS,
    ONE_TWO_INDICATOR_TAGS,
    PLAN_NUMBER_TAGS,
    TEXT_LIMITS,
    ZERO_ONE_INDICATOR_TAGS,
    normalize_ftw_update_value,
)


class FTWilliamsSchemaService:
    """Trusted FT schema acquisition and outbound payload validation.

    ftwLink exposes live ``Doc_Schema`` data for checklist/plan schemas. Its
    v2.8 contract publishes DOL schemas separately, so DOL writes remain on
    the versioned, verified contract already used by the XML builder. A live
    checklist refresh can therefore fail without weakening Schedule A safety.
    """

    def __init__(self, *, ftwilliams=None, repository=None, ttl_seconds: int | None = None):
        self.ftwilliams = ftwilliams
        self.repository = repository
        self.ttl_seconds = max(
            60,
            int(ttl_seconds or get_settings().ftw_schema_cache_ttl_seconds),
        )

    async def get_doc_schema(
        self,
        checklist: str,
        plan_type: str,
        checklist_version: str,
        *,
        force_refresh: bool = False,
    ) -> FTWilliamsSchemaSnapshot:
        repository = self.repository
        if repository is None:
            from app.repositories import get_repository

            repository = get_repository()
        ftwilliams = self.ftwilliams
        if ftwilliams is None:
            from app.services.ftwilliams import FTWilliamsService

            ftwilliams = FTWilliamsService()

        cache_key = self._cache_key(checklist, plan_type, checklist_version)
        cached = await repository.get_ftwilliams_schema(cache_key)
        now = datetime.utcnow()
        if cached and cached.expires_at > now and not force_refresh:
            return cached

        request_xml = self._doc_schema_request(checklist, plan_type, checklist_version)
        error: str | None = None
        try:
            response = await ftwilliams.send_xml("doc_schema", request_xml)
            error = response.error or self._status_error(response.statuses)
            if response.success and response.raw_response:
                try:
                    fields = self.parse_doc_schema(response.raw_response)
                except ValueError as exc:
                    fields = []
                    error = str(exc)
                if fields:
                    snapshot = FTWilliamsSchemaSnapshot(
                        cache_key=cache_key,
                        checklist=checklist,
                        plan_type=plan_type,
                        checklist_version=checklist_version,
                        fields=fields,
                        fetched_at=now,
                        expires_at=now + timedelta(seconds=self.ttl_seconds),
                    )
                    return await repository.upsert_ftwilliams_schema(snapshot)
                error = error or "FT Williams returned a successful Doc_Schema response without usable schema fields."
        except Exception as exc:
            error = f"FT Williams schema refresh failed: {exc}"

        if cached and cached.fields:
            stale = cached.model_copy(deep=True)
            stale.status = "STALE_LAST_KNOWN_GOOD"
            stale.last_error = error or "FT Williams schema refresh failed."
            return stale
        raise ValueError(error or "FT Williams schema refresh failed and no last-known-good schema exists.")

    @staticmethod
    def parse_doc_schema(response_xml: str) -> list[FTWilliamsSchemaField]:
        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError as exc:
            raise ValueError(f"FT Williams Doc_Schema returned malformed XML: {exc}") from exc

        rows: list[dict[str, str]] = []
        encoded = "".join((root.findtext(".//DocumentData") or "").split())
        if encoded:
            try:
                csv_text = base64.b64decode(encoded, validate=True).decode("utf-8-sig")
            except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
                raise ValueError(f"FT Williams Doc_Schema returned invalid CSV data: {exc}") from exc
            rows = [
                {str(key): str(value or "").strip() for key, value in row.items() if key}
                for row in csv.DictReader(io.StringIO(csv_text))
            ]
        else:
            rows = [
                {
                    child.tag: (child.text or "").strip()
                    for child in list(row)
                }
                for row in root.findall(".//QueryResults/*")
            ]

        fields: list[FTWilliamsSchemaField] = []
        for values in rows:
            var = values.get("VAR", "").strip()
            field_type = values.get("Field_Type", "").strip()
            if not var or field_type.casefold() in {"header", "heading", "section", "subheading"}:
                continue
            allowed_values = []
            for option in re.split(r"[;\r\n]+", values.get("List_Values", "")):
                option = option.strip()
                if not option:
                    continue
                allowed_values.append(option.split("|", 1)[0].strip())
            max_length_text = values.get("Max_Length", "").strip()
            fields.append(
                FTWilliamsSchemaField(
                    var=var,
                    prompt_text=values.get("PromptText") or None,
                    required=values.get("Required", "").strip().casefold() in {"1", "y", "yes", "true"},
                    field_type=field_type or None,
                    expected_format=values.get("Format") or None,
                    max_length=int(max_length_text) if max_length_text.isdigit() else None,
                    allowed_values=allowed_values,
                    section=values.get("Section") or None,
                    default_value=values.get("Default") or None,
                )
            )
        return fields

    def validate_outgoing_xml(
        self,
        form_type: FormType,
        year: str,
        request_xml: str,
        *,
        mode: str = "OBSERVE",
        trusted_preserved_values: set[tuple[str, str]] | None = None,
    ) -> FTWilliamsSchemaValidationResult:
        issues: list[FTWilliamsSchemaValidationIssue] = []
        trusted_preserved_values = trusted_preserved_values or set()
        try:
            root = ET.fromstring(request_xml)
        except ET.ParseError as exc:
            return FTWilliamsSchemaValidationResult(
                valid=False,
                mode=mode,
                schema_source="DOCUMENTED_STATIC_DOL",
                schema_version=FTW_CONTRACT_VERSION,
                issues=[
                    FTWilliamsSchemaValidationIssue(
                        tag="XML",
                        value="",
                        reason=f"Outgoing XML is malformed: {exc}",
                        expected_format="Well-formed XML",
                        correction="Rebuild the FT Williams payload before sending.",
                    )
                ],
            )

        expected_container = "DOL5500Data" if form_type == FormType.FORM_5500 else "DOLScheduleAData"
        containers = root.findall(f".//{expected_container}")
        if not containers:
            issues.append(
                FTWilliamsSchemaValidationIssue(
                    tag=expected_container,
                    reason="Required FT Williams form container is missing.",
                    expected_format=f"<{expected_container}>…</{expected_container}>",
                    correction="Rebuild the payload for the selected FT Williams form.",
                )
            )

        ignored = {
            "TransactionType",
            "EditCheck",
            "CustomerID",
            "PlanID",
            "FTWCustomerID",
            "FTWPlanID",
            "FTWSeqNo",
            "Year",
            "DOLSubPartData",
            "Broker",
        }
        for container in containers:
            payload_year = (container.findtext("Year") or "").strip()
            if payload_year and year and payload_year != str(year):
                issues.append(
                    FTWilliamsSchemaValidationIssue(
                        tag="Year",
                        value=payload_year,
                        reason="Payload year does not match the reviewed filing year.",
                        expected_format=str(year),
                        correction=f"Use filing year {year}.",
                    )
                )
            for element in container.iter():
                if element is container or element.tag in ignored or list(element):
                    continue
                value = (element.text or "").strip()
                # FT Williams Schedule A updates replace the complete record set.
                # Existing fields returned by FT must be echoed even when they are
                # outside our writable mapping. Exact tag/value pairs from the
                # fresh read-back are trusted for preservation only; changing the
                # value still requires a verified writable contract entry.
                if (element.tag, value) in trusted_preserved_values:
                    continue
                try:
                    normalize_ftw_update_value(form_type, element.tag, value)
                except FTWPayloadValidationError as exc:
                    reason = exc.issues[0].reason if exc.issues else str(exc)
                    correction = self._correction(element.tag)
                    if "not approved by FTW contract" in reason:
                        reason = (
                            f"Field is not verified as writable for the {form_type.value} "
                            f"update operation for {year}."
                        )
                        correction = (
                            "Keep this field read-only until FT Williams accepts it in a sandbox "
                            "update and read-back verification passes."
                        )
                    issues.append(
                        FTWilliamsSchemaValidationIssue(
                            tag=element.tag,
                            value=value,
                            reason=reason,
                            expected_format=self._expected_format(element.tag),
                            correction=correction,
                        )
                    )

        return FTWilliamsSchemaValidationResult(
            valid=not issues,
            mode=mode,
            schema_source="DOCUMENTED_STATIC_DOL",
            schema_version=FTW_CONTRACT_VERSION,
            issues=issues,
        )

    @staticmethod
    def _expected_format(tag: str) -> str:
        if tag in DATE_TAGS:
            return "Valid date in MM/DD/YYYY format"
        if tag in INTEGER_TAGS:
            return "Non-negative whole number"
        if tag in EIN_TAGS:
            return "9-digit EIN (NN-NNNNNNN)"
        if tag in NAIC_TAGS:
            return "Exactly 5 digits"
        if tag in PLAN_NUMBER_TAGS:
            return "Numeric plan number with at most 3 digits"
        if tag in BUSINESS_CODE_TAGS:
            return "Exactly 6 digits"
        if tag in ZERO_ONE_INDICATOR_TAGS:
            return "FT Williams 1/0 indicator"
        if tag in ONE_TWO_INDICATOR_TAGS:
            return "FT Williams 1/2 indicator"
        if tag.endswith("Amt") or re.fullmatch(r"(?:CommPdAmt|FeesPdAmt)\d+", tag):
            return "Numeric amount with at most 2 decimal places"
        return f"Text up to {TEXT_LIMITS.get(tag, 250)} characters"

    @staticmethod
    def _correction(tag: str) -> str:
        if tag in DATE_TAGS:
            return "Enter the date as MM/DD/YYYY."
        if tag in NAIC_TAGS:
            return "Enter the five-digit NAIC code, preserving leading zeroes."
        if tag in EIN_TAGS:
            return "Enter a valid nine-digit EIN."
        return "Correct the value or keep the current FT Williams value."

    @staticmethod
    def _cache_key(checklist: str, plan_type: str, checklist_version: str) -> str:
        return ":".join(part.strip().casefold() for part in (checklist, plan_type, checklist_version))

    @staticmethod
    def _status_error(statuses) -> str | None:
        errors = [
            f"FTW {status.type or 'schema'} error {status.error_code}: {status.error_desc or 'Unknown error'}"
            for status in statuses or []
            if str(status.error_code or "") != "0"
        ]
        return "; ".join(errors) or None

    @staticmethod
    def _doc_schema_request(checklist: str, plan_type: str, checklist_version: str) -> str:
        key_id = get_settings().ftwlink_key_id or ""
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            "<ftwLink>"
            f"<KeyID>{escape(key_id)}</KeyID>"
            "<DataBatch><Doc_Schema><TransactionType>Q</TransactionType>"
            f"<Checklist>{escape(checklist.strip())}</Checklist>"
            f"<PlanType>{escape(plan_type.strip())}</PlanType>"
            f"<ChecklistVersion>{escape(checklist_version.strip())}</ChecklistVersion>"
            "<Format>CSV</Format>"
            "</Doc_Schema></DataBatch></ftwLink>"
        )
