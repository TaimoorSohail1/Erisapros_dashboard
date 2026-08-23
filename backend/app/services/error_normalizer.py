from __future__ import annotations

import re

from app.models import ClientFacingError, ClientRejectedField


def _field_validation_hint(tag: str, value: str | None) -> tuple[str, str | None]:
    clean_tag = tag.strip()
    clean_value = (value or "").strip()
    if clean_tag == "InsCarrierNAICCode":
        digits = re.sub(r"\D", "", clean_value)
        suggested = digits[-5:] if len(digits) > 5 else digits
        return "NAIC must be sent as the valid 5-digit carrier code.", suggested or None
    if clean_tag.lower().endswith("date"):
        return "Date value is not accepted by FT Williams for this field.", None
    return "FT Williams rejected this XML field value.", None


def _rejected_fields_from_text(text: str) -> list[ClientRejectedField]:
    fields: list[ClientRejectedField] = []
    seen: set[tuple[str, str | None]] = set()

    def add(tag: str | None, value: str | None = None, reason: str | None = None) -> None:
        clean_tag = (tag or "").strip()
        if not clean_tag or clean_tag.lower() in {"invalid", "field", "req", "error"}:
            return
        clean_value = (value or "").strip() or None
        key = (clean_tag, clean_value)
        if key in seen:
            return
        seen.add(key)
        hint, suggested_value = _field_validation_hint(clean_tag, clean_value)
        fields.append(
            ClientRejectedField(
                tag=clean_tag,
                value=clean_value,
                reason=reason or hint,
                suggested_value=suggested_value,
            )
        )

    for match in re.finditer(r"invalid field re(?:q|g):\s*([A-Za-z][A-Za-z0-9_]*)", text, flags=re.IGNORECASE):
        add(match.group(1), reason="This XML tag is not accepted by ftwLink for this form/year.")

    for match in re.finditer(
        r"(?:DOL[A-Za-z0-9]+Data\s+)?error\s+\d+:\s*([A-Za-z][A-Za-z0-9_]*)(?::([^;\n]+))?",
        text,
        flags=re.IGNORECASE,
    ):
        add(match.group(1), match.group(2))

    validation_match = re.search(
        r"FT Williams pre-send validation failed:\s*(.+)$",
        text,
        flags=re.IGNORECASE,
    )
    if validation_match:
        for item in re.split(r";\s*(?=[A-Za-z][A-Za-z0-9_]*:)", validation_match.group(1)):
            parsed = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*):(.*?)\s+\(([^()]*)\)", item.strip())
            if parsed:
                add(parsed.group(1), parsed.group(2), parsed.group(3))

    return fields


def normalize_client_error(message: str | None, *, source: str = "FT Williams") -> ClientFacingError | None:
    if not message:
        return None

    text = str(message).strip()
    lowered = text.lower()
    rejected_fields = _rejected_fields_from_text(text)

    def build(
        title: str,
        body: str,
        *,
        reason: str | None = None,
        next_action: str | None = None,
        code: str | None = None,
        severity: str = "error",
        rejected: list[ClientRejectedField] | None = None,
    ) -> ClientFacingError:
        return ClientFacingError(
            title=title,
            message=body,
            reason=reason,
            next_action=next_action,
            severity=severity,
            source=source,
            code=code,
            technical_details=text,
            rejected_fields=rejected or [],
        )

    if "ft williams pre-send validation failed" in lowered:
        return build(
            "FT Williams data needs correction",
            "One or more proposed values do not match FT Williams' required field format.",
            next_action="Correct the highlighted fields, regenerate the preview, then send again.",
            code="FTW_PRE_SEND_VALIDATION",
            rejected=rejected_fields,
        )

    if "read-back verification" in lowered and "did not match" in lowered:
        return build(
            "FT Williams update could not be verified",
            "FT Williams accepted the request, but the values returned afterward did not match the sent update.",
            next_action="Click Query FTW Current and review the returned values before retrying the update.",
            code="FTW_UPDATE_VERIFICATION_FAILED",
        )

    if (
        "response was empty or malformed" in lowered
        or "returned no usable confirmation" in lowered
        or "parse_error" in lowered
        or "no element found" in lowered
    ):
        return build(
            "FT Williams returned no usable response",
            "The request reached FT Williams, but its response was empty or malformed, so the update outcome is unknown.",
            next_action="Click Query FTW Current to verify the values before retrying. If this repeats, share the operation diagnostics with FT Williams support.",
            code="FTW_EMPTY_OR_MALFORMED_RESPONSE",
        )

    if "endpoint and keyid" in lowered or "must be configured" in lowered:
        return build(
            "FT Williams connection is not configured",
            "The app cannot query or update FT Williams until the endpoint and KeyID are configured.",
            next_action="Add the FT Williams endpoint and KeyID in backend settings, then try again.",
            code="FTW_NOT_CONFIGURED",
        )

    if "transaction type 2 is not allowed" in lowered or "filing is locked" in lowered or re.search(r"\block(?:ed)?\b", lowered):
        return build(
            "The filing is locked in FT Williams",
            "FT Williams rejected the update because this filing is not currently editable.",
            next_action="Unlock the filing or use Amend Filing in FT Williams, then query current data again.",
            code="FTW_LOCKED",
        )

    if "error 54" in lowered or "company id" in lowered and "not valid" in lowered:
        return build(
            "FT Williams company ID is not valid",
            "The EIN or company identifier does not belong to the active FT Williams account.",
            next_action="Confirm the plan is under the correct FT Williams company code/account, then save the correct FTW match.",
            code="FTW_COMPANY_NOT_VALID",
        )

    if "error 56" in lowered or "could not locate existing plan" in lowered:
        return build(
            "FT Williams could not locate the existing plan",
            "The CustomerID/PlanID or FTW IDs do not point to a plan available for the selected year.",
            next_action="Check the FTW plan identifiers and filing year, then query current data again.",
            code="FTW_PLAN_NOT_FOUND",
        )

    if "error 59" in lowered or "could not locate form 5500" in lowered:
        return build(
            "Form 5500 was not found for this year",
            "FT Williams found the plan, but not a Form 5500 for the queried filing year.",
            next_action="Confirm the plan year in the worksheet matches the FT Williams filing year.",
            code="FTW_5500_NOT_FOUND",
        )

    if "error 60" in lowered or "invalid field req" in lowered or "invalid field reg" in lowered:
        return build(
            "FT Williams rejected an XML field",
            "One or more generated XML tags are not accepted by ftwLink for this update.",
            next_action="Review the technical details, remove unsupported tags from the XML builder, then regenerate the preview.",
            code="FTW_INVALID_XML_FIELD",
            rejected=rejected_fields,
        )

    if "error 62" in lowered and rejected_fields:
        return build(
            "FT Williams rejected a field value",
            "One or more fields were not accepted by FT Williams.",
            next_action="Fix the highlighted field value, regenerate the XML preview, then retry Send to FT Williams.",
            code="FTW_FIELD_VALUE_REJECTED",
            rejected=rejected_fields,
        )

    if "error 18" in lowered or "archive5500 lookup did not find" in lowered or "name lookup did not find" in lowered:
        return build(
            "FT Williams could not find a matching plan",
            "The extracted company, plan, or year does not match a plan returned by FT Williams.",
            next_action="Verify the CustomerID/PlanID or FTW IDs, save the correct match, then query current data again.",
            code="FTW_LOOKUP_NOT_FOUND",
        )

    if "multiple ft williams schedule a records" in lowered or "none clearly matched" in lowered:
        return build(
            "Schedule A match needs review",
            "FT Williams returned multiple Schedule A records and the app could not safely choose one.",
            next_action="Select the matching Schedule A record or choose Add as New Schedule A before sending.",
            code="FTW_SCHEDULE_A_MATCH_REQUIRED",
            severity="warning",
        )

    if "schedule a records" in lowered and ("not available" in lowered or "safe" in lowered or "replace-style" in lowered):
        return build(
            "Schedule A update was blocked for safety",
            "FT Williams Schedule A updates replace records, so the app stopped before risking removal of existing schedules.",
            next_action="Query current FT Williams data again so all Schedule A records can be preserved, then send.",
            code="FTW_SCHEDULE_A_SAFE_SEND_BLOCKED",
        )

    if "plan year does not match" in lowered:
        return build(
            "Plan year mismatch",
            "The FT Williams Form 5500 year does not match the plan worksheet year.",
            next_action="Confirm the worksheet year and the selected FT Williams filing year before updating.",
            code="FTW_PLAN_YEAR_MISMATCH",
        )

    if "current ft williams data must be queried" in lowered or "query ft williams current" in lowered:
        return build(
            "Current FT Williams data must be queried first",
            "The app needs the latest FT Williams values before it can safely send an update.",
            next_action="Click Query FTW Current, review the differences, then approve/send again.",
            code="FTW_CURRENT_QUERY_REQUIRED",
            severity="warning",
        )

    if "no ft williams changes remain" in lowered or "no changes remain" in lowered:
        return build(
            "No changes are ready to send",
            "The current proposed values do not produce any FT Williams update fields.",
            next_action="Review the decision table and make sure at least one field is marked to update.",
            code="FTW_NO_CHANGES",
            severity="warning",
        )

    if "maximum of 5000000 ingested tokens" in lowered or "subscription limits" in lowered or "status_code: 402" in lowered:
        return build(
            "Extractor token limit reached",
            "GroundX rejected extraction because the monthly ingest token limit has been reached.",
            next_action="Use a different GroundX bucket/key or upgrade/reset the GroundX quota, then retry extraction.",
            code="EXTRACTOR_TOKEN_LIMIT",
        )

    if "getaddrinfo" in lowered or "max retries exceeded" in lowered or "failed to resolve" in lowered or "connection" in lowered:
        return build(
            "External service connection failed",
            "The app could not reach one of the external services needed for this step.",
            next_action="Check network access, service credentials, and retry the action.",
            code="EXTERNAL_CONNECTION_FAILED",
        )

    if "sharefile" in lowered and ("webhook" in lowered or "queued" in lowered or "register" in lowered):
        return build(
            "ShareFile upload event was not received",
            "The folder upload was not delivered to the backend webhook.",
            next_action="Confirm the webhook is registered and online, then re-upload or run folder discovery.",
            code="SHAREFILE_WEBHOOK_MISSED",
        )

    return build(
        "Action failed",
        "The app could not complete this step.",
        next_action="Open the technical details below or retry after checking the selected plan and filing status.",
        code="UNKNOWN_FAILURE",
    )
