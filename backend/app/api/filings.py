import asyncio
from datetime import datetime
from urllib.parse import urlsplit
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from app.auth import require_field_rule_admin
from app.models import (
    ApproveRequest,
    AuditLog,
    DocumentType,
    ExtractedFieldStatus,
    ExtractionJob,
    FieldEditRequest,
    Filing,
    FilingDetail,
    FilingStatus,
    NormalizedExtractionField,
    FTWilliamsManualMatchRequest,
    FTWilliamsBrokerMatchesRequest,
    FTWilliamsPrepareReviewRequest,
    FTWilliamsScheduleAContractTypeRequest,
    FTWilliamsScheduleAMatchRequest,
    FTWilliamsSendUpdateRequest,
    RejectRequest,
    ReviewEvent,
)
from app.repositories import get_repository, retry_repository_read
from app.services.filing_pipeline import (
    build_safe_proposed_ftw_xml,
    harmonize_schedule_a_business_rule_fields,
    harmonize_schedule_a_reference_fields,
    process_package_extraction_job,
    summarize_mapped_fields,
)
from app.services.field_rule_admin import FieldRuleService
from app.services.ftwilliams_contract import FTWPayloadValidationError
from app.services.mapping import map_extraction_to_rules
from app.services.schedule_a_classification import apply_schedule_a_classification, filter_schedule_a_fields_for_contract_type
from app.services.ftwilliams_review import FTWilliamsReviewService
from app.services.storage import StorageService
from app.services.xml_builder import build_proposed_ftw_xml

router = APIRouter(prefix="/filings", tags=["filings"])


@router.get("")
async def list_filings():
    filings = [
        filing
        for filing in await retry_repository_read(lambda repo: repo.list_dashboard_filings())
        if filing.status not in {FilingStatus.SUPERSEDED, FilingStatus.DELETED}
    ]
    return {"filings": dedupe_active_sharefile_packages(filings)}


def dedupe_active_sharefile_packages(filings: list[Filing]) -> list[Filing]:
    latest_by_package: dict[str, Filing] = {}
    passthrough: list[Filing] = []
    for filing in filings:
        package_key = filing_package_key(filing)
        if not package_key:
            passthrough.append(filing)
            continue
        current = latest_by_package.get(package_key)
        if not current or (filing.updated_at, filing.created_at, filing.id or "") > (current.updated_at, current.created_at, current.id or ""):
            latest_by_package[package_key] = filing
    return sorted([*passthrough, *latest_by_package.values()], key=lambda item: item.created_at, reverse=True)


def filing_package_key(filing: Filing) -> str | None:
    for document in filing.package_documents:
        if document.get("package_key"):
            return str(document["package_key"])
    s3_key = str(filing.s3_key or "")
    if s3_key.startswith("sharefile-package/"):
        return s3_key.removeprefix("sharefile-package/")
    return None


@router.get("/{filing_id}", response_model=FilingDetail)
async def get_filing(filing_id: str):
    repo = get_repository()
    filing = await repo.get_filing(filing_id)
    if not filing:
        raise HTTPException(status_code=404, detail="Filing not found")
    fields, events, jobs, audit_logs, ftw_review = await asyncio.gather(
        repo.list_fields(filing_id),
        repo.list_events(filing_id),
        repo.list_extraction_jobs(filing_id),
        repo.list_audit_logs(filing_id),
        repo.get_ftwilliams_review(filing_id),
    )
    return FilingDetail(**filing.model_dump(), fields=fields, events=events, jobs=jobs, audit_logs=audit_logs, ftw_review=ftw_review)


@router.delete("/{filing_id}")
async def delete_filing_from_dashboard(filing_id: str):
    repo = get_repository()
    filing = await repo.get_filing(filing_id)
    if not filing:
        raise HTTPException(status_code=404, detail="Filing not found")

    primary_item_id = str(filing.sharefile_item_id or "")
    source_items: dict[str, str] = {}
    if primary_item_id:
        source_items[primary_item_id] = filing.file_name
    for document in filing.package_documents:
        item_id = str(document.get("sharefile_item_id") or "")
        document_type = document.get("document_type")
        if isinstance(document_type, DocumentType):
            document_type = document_type.value
        # A Plan Worksheet is shared by every Schedule A in the same client/year.
        # Deleting one filing must not suppress that shared companion document.
        if not item_id or (document_type == DocumentType.PLAN_WORKSHEET.value and item_id != primary_item_id):
            continue
        source_items[item_id] = str(document.get("file_name") or filing.file_name)
    for item_id, file_name in source_items.items():
        await repo.upsert_sharefile_suppression(
            item_id,
            {
                "filing_id": filing_id,
                "file_name": file_name,
                "reason": "DASHBOARD_DELETE",
            },
        )

    updated = await repo.update_filing(
        filing_id,
        {
            "status": FilingStatus.DELETED,
            "error_message": None,
        },
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Filing not found")

    await repo.add_event(ReviewEvent(filing_id=filing_id, type="DELETE_FROM_DASHBOARD"))
    await repo.add_audit(
        AuditLog(
            filing_id=filing_id,
            event="DASHBOARD_DELETE",
            message="Filing removed from ERISAPros dashboard.",
            details={
                "file_name": filing.file_name,
                "intake_source": filing.intake_source,
                "sharefile_item_id": filing.sharefile_item_id,
                "sharefile_parent_id": filing.sharefile_parent_id,
            },
        )
    )
    return {"status": updated.status}


@router.post("/{filing_id}/retry-extraction")
async def retry_extraction(filing_id: str, background_tasks: BackgroundTasks):
    repo = get_repository()
    filing = await repo.get_filing(filing_id)
    if not filing:
        raise HTTPException(status_code=404, detail="Filing not found")
    try:
        stored_documents = filing.package_documents or [
            {
                "file_name": filing.file_name,
                "document_type": filing.document_type,
                "s3_key": filing.s3_key,
                "s3_bucket": filing.s3_bucket,
                "storage_path": filing.storage_path,
            }
        ]
        documents = []
        storage_service = StorageService()
        for document in stored_documents:
            documents.append(
                {
                    "file_name": document["file_name"],
                    "document_type": DocumentType(document.get("document_type") or DocumentType.SCHEDULE_A.value),
                    "file_bytes": storage_service.load_pdf(document["s3_key"], document.get("s3_bucket"), document.get("storage_path")),
                }
            )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    job = await repo.create_extraction_job(ExtractionJob(filing_id=filing.id))
    await repo.update_filing(filing.id, {"status": FilingStatus.QUEUED, "error_message": None})
    await repo.add_audit(AuditLog(filing_id=filing.id, event="RETRY_QUEUED", message="Extraction retry queued from stored package."))
    background_tasks.add_task(process_package_extraction_job, filing.id, job.id, documents)
    return {"id": filing.id, "status": FilingStatus.QUEUED, "job_id": job.id}


@router.post("/{filing_id}/re-evaluate-rules")
async def re_evaluate_filing_rules(
    filing_id: str,
    claims: dict = Depends(require_field_rule_admin),
):
    repo = get_repository()
    filing = await repo.get_filing(filing_id)
    if not filing:
        raise HTTPException(status_code=404, detail="Filing not found")
    existing_fields = await repo.list_fields(filing_id)
    stored_values = [
        NormalizedExtractionField(
            field_name=field.source_field_name,
            value=field.proposed_value or field.value,
            confidence=field.confidence,
            page=field.page,
            source_text=field.source_text,
        )
        for field in existing_fields
        if (field.proposed_value or field.value) and field.status != ExtractedFieldStatus.IGNORED
    ]
    snapshot = await FieldRuleService(repo).published_snapshot()
    mapped = map_extraction_to_rules(filing_id, stored_values, rules=snapshot.rules)
    fields = harmonize_schedule_a_reference_fields(mapped["fields"])
    fields = harmonize_schedule_a_business_rule_fields(fields)
    classification = apply_schedule_a_classification(fields, filing.schedule_a_classification_signals)
    relevant_fields = filter_schedule_a_fields_for_contract_type(fields, classification.contract_type, rules=snapshot.rules)
    summary = summarize_mapped_fields(relevant_fields)
    saved_fields = await repo.replace_fields(filing_id, fields)
    proposed_xml = build_proposed_ftw_xml(
        filter_schedule_a_fields_for_contract_type(saved_fields, classification.contract_type, rules=snapshot.rules)
    )
    await repo.update_filing(
        filing_id,
        {
            "status": summary["status"],
            "field_rule_set_version": snapshot.version,
            "overall_confidence": summary["overall_confidence"],
            "missing_high_priority_count": summary["missing_high_priority_count"],
            "missing_medium_priority_count": summary["missing_medium_priority_count"],
            "missing_low_priority_count": summary["missing_low_priority_count"],
            "low_confidence_count": summary["low_confidence_count"],
            "unmapped_count": summary["unmapped_count"],
            "review_field_count": summary["review_field_count"],
            "found_field_count": summary["found_field_count"],
            "excluded_field_count": max(0, len(fields) - len(relevant_fields)),
            "schedule_a_contract_type": classification.contract_type,
            "schedule_a_contract_type_reason": classification.reason,
            "schedule_a_contract_type_confirmed": True,
            "schedule_a_contract_type_confidence": classification.confidence,
            "schedule_a_contract_type_evidence": list(classification.evidence),
            "proposed_xml": proposed_xml,
        },
    )
    # The filing detail page merges stored extraction fields with the cached
    # FT Williams comparison. Rebuild that comparison from the newly mapped
    # fields so removed/renamed rules cannot remain as stale decision rows.
    await FTWilliamsReviewService().prepare_review(filing_id, send_queries=False)
    await repo.add_audit(
        AuditLog(
            filing_id=filing_id,
            event="FIELD_RULES_RE_EVALUATED",
            message="Stored extraction values were re-evaluated using the latest published field rules.",
            details={
                "field_rule_set_version": snapshot.version,
                "actor": claims.get("email") or claims.get("sub"),
                "eyelevel_rerun": False,
                "schedule_a_contract_type": classification.contract_type,
                "schedule_a_contract_type_reason": classification.reason,
                "schedule_a_contract_type_confidence": classification.confidence,
                "schedule_a_contract_type_evidence": list(classification.evidence),
            },
        )
    )
    return {
        "status": "re-evaluated",
        "field_rule_set_version": snapshot.version,
        "field_count": len(relevant_fields),
    }


@router.patch("/{filing_id}/fields/{field_id}")
async def update_field(filing_id: str, field_id: str, payload: FieldEditRequest):
    repo = get_repository()
    filing = await repo.get_filing(filing_id)
    if not filing:
        raise HTTPException(status_code=404, detail="Filing not found")
    existing_fields, published_rules, existing_review = await asyncio.gather(
        repo.list_fields(filing_id),
        FieldRuleService(repo).published_rules(),
        repo.get_ftwilliams_review(filing_id),
    )
    before = next((field.proposed_value for field in existing_fields if field.id == field_id), None)
    field_status = ExtractedFieldStatus.MISSING if payload.mark_missing else ExtractedFieldStatus.EDITED
    status_reason = "Marked missing by reviewer." if payload.mark_missing else "Value confirmed by reviewer."
    field = await repo.update_field(
        filing_id,
        field_id,
        "" if payload.mark_missing else payload.proposed_value,
        status=field_status,
        status_reason=status_reason,
    )
    if not field:
        raise HTTPException(status_code=404, detail="Field not found")
    fields = [field if item.id == field_id else item for item in existing_fields]
    before_automatic = {
        item.id: (item.proposed_value, item.status, item.status_reason)
        for item in fields
        if item.id
    }
    classification = apply_schedule_a_classification(fields, filing.schedule_a_classification_signals)
    relevant_fields = filter_schedule_a_fields_for_contract_type(
        fields,
        classification.contract_type,
        rules=published_rules,
    )
    proposed_xml, _preview_validation_issues = build_safe_proposed_ftw_xml(relevant_fields)
    for item in fields:
        if not item.id or before_automatic.get(item.id) == (item.proposed_value, item.status, item.status_reason):
            continue
        await repo.update_field(
            filing_id,
            item.id,
            item.proposed_value,
            status=item.status,
            status_reason=item.status_reason,
        )
    summary = summarize_mapped_fields(relevant_fields)
    await repo.update_filing(
        filing_id,
        {
            "status": summary["status"],
            "approved_at": None,
            "error_message": None,
            "proposed_xml": proposed_xml,
            "schedule_a_contract_type": classification.contract_type,
            "schedule_a_contract_type_reason": classification.reason,
            "schedule_a_contract_type_confirmed": True,
            "schedule_a_contract_type_confidence": classification.confidence,
            "schedule_a_contract_type_evidence": list(classification.evidence),
            "overall_confidence": summary["overall_confidence"],
            "missing_high_priority_count": summary["missing_high_priority_count"],
            "missing_medium_priority_count": summary["missing_medium_priority_count"],
            "missing_low_priority_count": summary["missing_low_priority_count"],
            "low_confidence_count": summary["low_confidence_count"],
            "unmapped_count": summary["unmapped_count"],
            "review_field_count": summary["review_field_count"],
            "found_field_count": summary["found_field_count"],
            "excluded_field_count": max(0, len(fields) - len(relevant_fields)),
        },
    )
    field = next((item for item in fields if item.id == field_id), field)
    ftw_review = None
    try:
        ftw_review = await FTWilliamsReviewService().prepare_review(
            filing_id,
            send_queries=False,
            preloaded=(filing, fields, published_rules, existing_review),
        )
    except ValueError:
        pass
    await repo.add_event(
        ReviewEvent(
            filing_id=filing_id,
            type="MARK_MISSING" if payload.mark_missing else "EDIT",
            field_id=field_id,
            before=before,
            after="" if payload.mark_missing else payload.proposed_value,
        )
    )
    return {"field": field, "proposed_xml": proposed_xml, "ftw_review": ftw_review}


@router.post("/{filing_id}/approve")
async def approve_filing(filing_id: str, payload: ApproveRequest):
    repo = get_repository()
    if not await repo.get_filing(filing_id):
        raise HTTPException(status_code=404, detail="Filing not found")
    try:
        review = await FTWilliamsReviewService().approve_and_update(
            filing_id,
            reason=payload.reason,
            send_to_ftw=payload.send_to_ftw,
            refresh_current_before_update=payload.refresh_current_before_update,
            run_edit_checks=payload.run_edit_checks,
            override_blockers=payload.override_blockers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    updated = await repo.get_filing(filing_id)
    return {"status": updated.status if updated else FilingStatus.APPROVED, "ftw_review": review}


@router.post("/{filing_id}/unapprove")
async def unapprove_filing(filing_id: str):
    repo = get_repository()
    filing = await repo.get_filing(filing_id)
    if not filing:
        raise HTTPException(status_code=404, detail="Filing not found")

    fields = await repo.list_fields(filing_id)
    has_unresolved_fields = any(
        field.status in {ExtractedFieldStatus.MISSING, ExtractedFieldStatus.LOW_CONFIDENCE, ExtractedFieldStatus.UNMAPPED}
        for field in fields
    )
    next_status = FilingStatus.NEEDS_REVIEW if has_unresolved_fields else FilingStatus.READY_FOR_APPROVAL
    updated = await repo.update_filing(
        filing_id,
        {
            "status": next_status,
            "approved_at": None,
            "error_message": None,
        },
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Filing not found")
    await repo.add_event(ReviewEvent(filing_id=filing_id, type="UNAPPROVE"))
    await repo.add_audit(
        AuditLog(
            filing_id=filing_id,
            event="UNAPPROVED",
            message="Reviewer removed filing approval.",
            details={"previous_status": filing.status, "next_status": next_status},
        )
    )
    return {"status": updated.status}


@router.post("/{filing_id}/reject")
async def reject_filing(filing_id: str, payload: RejectRequest):
    repo = get_repository()
    filing = await repo.update_filing(
        filing_id,
        {
            "status": FilingStatus.REJECTED,
            "rejected_at": datetime.utcnow(),
            "rejection_reason": payload.reason,
            "error_message": None,
        },
    )
    if not filing:
        raise HTTPException(status_code=404, detail="Filing not found")
    await repo.add_event(ReviewEvent(filing_id=filing_id, type="REJECT", reason=payload.reason))
    await repo.add_audit(AuditLog(filing_id=filing_id, event="REJECTED", message="Reviewer rejected filing.", details={"reason": payload.reason}))
    return {"status": FilingStatus.REJECTED}


@router.post("/{filing_id}/regenerate-xml")
async def regenerate_xml(filing_id: str):
    repo = get_repository()
    if not await repo.get_filing(filing_id):
        raise HTTPException(status_code=404, detail="Filing not found")
    fields = await repo.list_fields(filing_id)
    try:
        proposed_xml = build_proposed_ftw_xml(fields)
    except FTWPayloadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"proposed_xml": proposed_xml}


@router.post("/{filing_id}/ftw/prepare")
async def prepare_ftwilliams_review(filing_id: str, payload: FTWilliamsPrepareReviewRequest):
    repo = get_repository()
    if not await repo.get_filing(filing_id):
        raise HTTPException(status_code=404, detail="Filing not found")
    try:
        review = await FTWilliamsReviewService().prepare_review(filing_id, send_queries=payload.send_queries)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ftw_review": review}


@router.post("/{filing_id}/ftw/bring-forward-link")
async def get_ftwilliams_bring_forward_link(filing_id: str):
    repo = get_repository()
    filing = await repo.get_filing(filing_id)
    if not filing:
        raise HTTPException(status_code=404, detail="Filing not found")
    review = await repo.get_ftwilliams_review(filing_id)
    if not review or not review.bring_forward_required:
        raise HTTPException(status_code=409, detail="FT Williams Bring Forward is not required for this filing.")
    url = FTWilliamsReviewService().plan_page_url_for_review(review)
    if not url:
        raise HTTPException(
            status_code=400,
            detail="A plan-specific FT Williams URL cannot be created without FTW customer ID, plan ID, and year.",
        )
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        safe_url = (
            parsed.scheme == "https"
            and (host == "ftwilliam.com" or host.endswith(".ftwilliam.com"))
            and not parsed.username
            and not parsed.password
            and parsed.port in {None, 443}
        )
    except ValueError:
        safe_url = False
    if not safe_url:
        raise HTTPException(status_code=400, detail="A safe plan-specific FT Williams URL is not configured.")
    await repo.add_audit(
        AuditLog(
            filing_id=filing_id,
            event="FTWILLIAMS_BRING_FORWARD_OPENED",
            message="Reviewer opened FT Williams to complete the native Bring Forward action.",
            details={
                "target_year": review.year,
                "prior_year": review.comparison_year,
                "mutation_requested": False,
            },
        )
    )
    return {
        "url": url,
        "target_year": review.year,
        "prior_year": review.comparison_year,
    }


@router.post("/{filing_id}/ftw/manual-match")
async def apply_manual_ftwilliams_match(filing_id: str, payload: FTWilliamsManualMatchRequest):
    try:
        review = await FTWilliamsReviewService().apply_manual_plan_match(filing_id, payload)
    except ValueError as exc:
        status_code = 404 if str(exc) == "Filing not found" else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {"ftw_review": review}


@router.post("/{filing_id}/ftw/schedule-a-match")
async def select_ftwilliams_schedule_a_match(filing_id: str, payload: FTWilliamsScheduleAMatchRequest):
    try:
        review = await FTWilliamsReviewService().select_schedule_a_match(filing_id, payload)
    except ValueError as exc:
        status_code = 404 if str(exc) == "Filing not found" else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {"ftw_review": review}


@router.post("/{filing_id}/ftw/schedule-a-broker-matches")
async def set_ftwilliams_schedule_a_broker_matches(filing_id: str, payload: FTWilliamsBrokerMatchesRequest):
    try:
        review = await FTWilliamsReviewService().set_schedule_a_broker_matches(filing_id, payload)
    except ValueError as exc:
        status_code = 404 if str(exc) == "Filing not found" else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {"ftw_review": review}


@router.post("/{filing_id}/ftw/schedule-a-contract-type")
async def set_ftwilliams_schedule_a_contract_type(filing_id: str, payload: FTWilliamsScheduleAContractTypeRequest):
    try:
        review = await FTWilliamsReviewService().set_schedule_a_contract_type(filing_id, payload)
    except ValueError as exc:
        status_code = 404 if str(exc) == "Filing not found" else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {"ftw_review": review}


@router.post("/{filing_id}/ftw/send-update")
async def send_approved_ftwilliams_update(filing_id: str, payload: FTWilliamsSendUpdateRequest):
    try:
        review = await FTWilliamsReviewService().send_approved_update(filing_id, payload)
    except ValueError as exc:
        status_code = 404 if str(exc) == "Filing not found" else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {"ftw_review": review}


@router.get("/{filing_id}/ftw/audit-pdf")
async def download_ftwilliams_audit_pdf(filing_id: str):
    review = await get_repository().get_ftwilliams_review(filing_id)
    if not review:
        raise HTTPException(status_code=404, detail="FT Williams review not found")
    if review.audit_pdf_status != "AVAILABLE" or not review.audit_pdf_key:
        raise HTTPException(status_code=404, detail="Verified FT Williams Schedule A PDF is not available")
    try:
        pdf = StorageService().load_pdf(
            review.audit_pdf_key,
            review.audit_pdf_bucket,
            review.audit_pdf_local_path,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="ftw-schedule-a-{review.year or "current"}.pdf"'},
    )
