import asyncio
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_field_rule_admin

from app.models import (
    AuditLog,
    Filing,
    FilingStatus,
    FTWilliamsDismissFailureRequest,
    FTWilliamsFailureQueueItem,
    FTWilliamsFailureQueueResponse,
    FTWilliamsHistoryItem,
    FTWilliamsHistoryResponse,
    FTWilliamsQueryRequest,
    FTWilliamsQueryResponse,
    FTWilliamsReview,
    FTWilliamsReviewStatus,
    FTWilliamsSchemaSnapshot,
)
from app.repositories import get_repository, retry_repository_read
from app.services.ftwilliams import FTWilliamsService
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.ftwilliams_schema import FTWilliamsSchemaService


router = APIRouter(prefix="/ftwilliams", tags=["ftwilliams"])


@router.get("/status")
async def status():
    return FTWilliamsService().status()


@router.post("/query", response_model=FTWilliamsQueryResponse)
async def query(payload: FTWilliamsQueryRequest):
    try:
        return await FTWilliamsService().run_query(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/failure-queue", response_model=FTWilliamsFailureQueueResponse)
async def failure_queue():
    return await retry_repository_read(_build_failure_queue)


@router.post("/failure-queue/{filing_id}/dismiss", response_model=FTWilliamsReview)
async def dismiss_failure(filing_id: str, payload: FTWilliamsDismissFailureRequest):
    try:
        return await FTWilliamsReviewService().dismiss_active_failure(filing_id, payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/schema/refresh", response_model=FTWilliamsSchemaSnapshot)
async def refresh_schema(
    checklist: str,
    plan_type: str,
    checklist_version: str,
    _claims: dict = Depends(require_field_rule_admin),
):
    try:
        return await FTWilliamsSchemaService().get_doc_schema(
            checklist,
            plan_type,
            checklist_version,
            force_refresh=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _build_failure_queue(repo):
    items: list[FTWilliamsFailureQueueItem] = []
    # Persistent failure state is authoritative for new records. Unresolved
    # lifecycle audits recover older failures that Query Current accidentally
    # hid before persistent failure state existed.
    active_reviews, audit_rows = await asyncio.gather(
        repo.list_failed_ftwilliams_reviews(),
        repo.list_unresolved_ftwilliams_failure_audits(),
    )
    failed_audits = {audit.filing_id: audit for audit in audit_rows if audit.filing_id}
    filing_ids = {review.filing_id for review in active_reviews} | set(failed_audits)
    filing_rows, review_rows = await asyncio.gather(
        repo.get_filings_by_ids(filing_ids),
        repo.get_ftwilliams_reviews_by_filing_ids(filing_ids),
    )
    filings = {filing.id: filing for filing in filing_rows if filing.id}
    reviews = {review.filing_id: review for review in review_rows}
    for filing_id in filing_ids:
        review = reviews.get(filing_id)
        filing = filings.get(filing_id)
        if not review:
            continue
        if not filing or not filing.id or filing.status in {FilingStatus.SUPERSEDED, FilingStatus.DELETED}:
            continue
        failed_audit = failed_audits.get(filing.id)
        items.append(_failure_queue_item(filing, review, failed_audit))
    items.sort(key=lambda item: item.failed_at, reverse=True)
    return FTWilliamsFailureQueueResponse(total=len(items), items=items)


@router.get("/history", response_model=FTWilliamsHistoryResponse)
async def history(range_: str = Query("7d", alias="range", pattern="^(1d|7d|30d)$")):
    days = {"1d": 1, "7d": 7, "30d": 30}[range_]
    since = datetime.utcnow() - timedelta(days=days)
    repo = get_repository()
    audits = await repo.list_ftwilliams_audit_logs(since, limit=100)
    filing_ids = {audit.filing_id for audit in audits if audit.filing_id}
    filing_rows, review_rows = await asyncio.gather(
        repo.get_filings_by_ids(filing_ids),
        repo.get_ftwilliams_reviews_by_filing_ids(filing_ids),
    )
    filings = {filing.id: filing for filing in filing_rows if filing.id}
    reviews = {
        review.filing_id: review
        for review in review_rows
    }
    items: list[FTWilliamsHistoryItem] = []
    for audit in audits:
        if not audit.filing_id:
            continue
        filing = filings.get(audit.filing_id)
        if not filing:
            continue
        if filing.status in {FilingStatus.SUPERSEDED, FilingStatus.DELETED}:
            continue
        review = reviews.get(audit.filing_id)
        items.append(_history_item(audit, filing, review))
    return FTWilliamsHistoryResponse(range=range_, days=days, items=items)


def _failure_queue_item(
    filing: Filing,
    review: FTWilliamsReview,
    failed_audit: AuditLog | None,
) -> FTWilliamsFailureQueueItem:
    lookup = review.plan_lookup
    client_error = review.active_failure_client_error or review.client_error
    details = failed_audit.details if failed_audit and failed_audit.details else {}
    error = (
        (client_error.message if client_error else None)
        or _text(details.get("error"))
        or review.active_failure_reason
        or review.error_message
        or ("FT Williams update requires verification." if review.status == FTWilliamsReviewStatus.UPDATE_UNKNOWN else "FT Williams update failed.")
    )
    return FTWilliamsFailureQueueItem(
        filing_id=filing.id or review.filing_id,
        filing_name=filing.file_name,
        filing_status=filing.status,
        review_status=review.status,
        failure_reason=error,
        next_action=(client_error.next_action if client_error else None),
        plan_name=(lookup.plan_name if lookup else None),
        sponsor_name=(lookup.sponsor_name if lookup else None),
        company_employer_id=(lookup.company_employer_id if lookup else None),
        plan_number=(lookup.plan_number if lookup else None),
        customer_id=review.customer_id,
        plan_id=review.plan_id,
        ftw_customer_id=review.ftw_customer_id,
        ftw_plan_id=review.ftw_plan_id,
        year=review.year or review.comparison_year,
        attempted_field_count=review.update_attempted_count,
        failed_at=(review.active_failure_at or (failed_audit.created_at if failed_audit else review.updated_at)),
        last_action_label=(
            "Verification required"
            if review.status == FTWilliamsReviewStatus.UPDATE_UNKNOWN
            or any(item.outcome_code in {"EMPTY_RESPONSE", "MALFORMED_RESPONSE", "NO_RESPONSE"} for item in review.update_diagnostics)
            else "Update failed"
        ),
        error_code=(client_error.code if client_error else _text(details.get("error_code"))),
        technical_details=(client_error.technical_details if client_error else None),
        operation_diagnostics=list(review.update_diagnostics or []),
    )


def _history_item(audit: AuditLog, filing: Filing, review: FTWilliamsReview | None) -> FTWilliamsHistoryItem:
    details = audit.details or {}
    action_label, status = _history_action(audit)
    lookup = review.plan_lookup if review and review.plan_lookup else None
    updated_count = _history_updated_field_count(audit, review)
    error_message = str(details.get("error") or "") or (review.error_message if status in {"failed", "warning"} and review else None)
    return FTWilliamsHistoryItem(
        id=audit.id,
        filing_id=filing.id or audit.filing_id or "",
        filing_name=filing.file_name,
        filing_status=filing.status,
        action=audit.event,
        action_label=action_label,
        status=status,
        message=audit.message,
        company_employer_id=_text(details.get("company_employer_id")) or (lookup.company_employer_id if lookup else None),
        plan_number=_text(details.get("plan_number")) or (lookup.plan_number if lookup else None),
        plan_name=(lookup.plan_name if lookup else None),
        sponsor_name=(lookup.sponsor_name if lookup else None),
        customer_id=review.customer_id if review else _text(details.get("customer_id")),
        plan_id=review.plan_id if review else _text(details.get("plan_id")),
        ftw_customer_id=review.ftw_customer_id if review else _text(details.get("ftw_customer_id")),
        ftw_plan_id=review.ftw_plan_id if review else _text(details.get("ftw_plan_id")),
        year=(review.year or review.comparison_year if review else None) or _text(details.get("year")),
        updated_field_count=updated_count,
        error_message=error_message,
        created_at=audit.created_at,
    )


def _history_action(audit: AuditLog) -> tuple[str, str]:
    details = audit.details or {}
    if audit.event == "FTWILLIAMS_REVIEW_PREPARED":
        if details.get("send_queries"):
            if not details.get("current_query_success"):
                return "Current data queried", "failed"
            if details.get("error"):
                return "Current data queried", "warning"
            return "Current data queried", "success"
        return "Preview prepared", "success"
    if audit.event == "FTWILLIAMS_MANUAL_MATCH_SAVED":
        return "Plan match saved", "success"
    if audit.event == "FTWILLIAMS_SCHEDULE_A_MATCH_SELECTED":
        return "Schedule A matched", "success"
    if audit.event == "FTWILLIAMS_UPDATE_SENT":
        return "Update sent", "success"
    if audit.event == "FTWILLIAMS_UPDATE_FAILED":
        return "Update failed", "failed"
    if audit.event == "FTWILLIAMS_UPDATE_UNKNOWN":
        return "Verification required", "warning"
    if audit.event == "FTWILLIAMS_UPDATE_FAILURE_DISMISSED":
        return "Failure dismissed", "info"
    if audit.event == "FTWILLIAMS_UPDATE_FAILURE_RESOLVED":
        return "Failure resolved", "success"
    return audit.event.replace("_", " ").title(), "info"


def _history_updated_field_count(audit: AuditLog, review: FTWilliamsReview | None) -> int | None:
    details = audit.details or {}
    for key in ("updated_field_count", "field_count"):
        value = details.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    if audit.event in {"FTWILLIAMS_UPDATE_SENT", "FTWILLIAMS_UPDATE_FAILED", "FTWILLIAMS_UPDATE_UNKNOWN"} and review:
        return len([field for field in review.fields if field.changed and field.update_included])
    return None


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
