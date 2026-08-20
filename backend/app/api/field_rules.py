from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from app.auth import has_field_rule_admin_access, require_field_rule_admin
from app.config import get_settings
from app.models import DocumentType, FieldRuleActionRequest, FieldRuleDraftRequest, FieldRuleMappingMode, FieldRuleTestRequest
from app.repositories import get_repository, retry_repository_read
from app.services.field_rule_admin import FieldRuleService, FieldRuleValidationError
from app.services.field_rule_qa import run_field_rule_qa
from app.services.ftw_field_catalog import field_catalog, field_catalog_version
from app.services.field_rules import find_rule_for_field

router = APIRouter(prefix="/field-rules", tags=["field-rules"])
FIELD_RULE_QA_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".xlsm", ".csv", ".txt"}
FIELD_RULE_QA_MAX_BYTES = 20 * 1024 * 1024


def actor_from_claims(claims: dict) -> str:
    return str(claims.get("email") or claims.get("cognito:username") or claims.get("sub") or "unknown")


@router.get("")
async def list_field_rules(request: Request):
    async def load(repo):
        service = FieldRuleService(repo)
        return await service.list_rules(), await service.published_snapshot()

    rules, snapshot = await retry_repository_read(load)
    return {
        "field_rules": [
            rule.model_dump()
            | {
                "update_supported": (
                    rule.mapping_mode == FieldRuleMappingMode.FTW_MAPPED
                    and bool(FieldRuleService.approved_update_tag(rule.key))
                ),
                "approved_update_tag": FieldRuleService.approved_update_tag(rule.key),
            }
            for rule in rules
        ],
        "published_version": snapshot.version,
        "field_catalog": [entry.model_dump() for entry in field_catalog()],
        "catalog_version": field_catalog_version(),
        "can_manage": has_field_rule_admin_access(getattr(request.state, "user", None), get_settings()),
    }


@router.post("/drafts")
async def create_field_rule_draft(
    payload: FieldRuleDraftRequest,
    claims: dict = Depends(require_field_rule_admin),
):
    try:
        rule = await FieldRuleService(get_repository()).create_draft(
            payload.rule,
            actor=actor_from_claims(claims),
            reason=payload.reason or "Draft saved by administrator.",
        )
        return {"field_rule": rule}
    except FieldRuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/test")
async def test_field_rule(
    payload: FieldRuleTestRequest,
    claims: dict = Depends(require_field_rule_admin),
):
    del claims
    try:
        await FieldRuleService(get_repository()).validate(payload.rule, ignore_record_id=payload.rule.id)
    except FieldRuleValidationError as exc:
        return {"valid": False, "matched": False, "message": str(exc)}
    match = find_rule_for_field(payload.sample_field_name, [payload.rule])
    return {
        "valid": True,
        "matched": bool(match),
        "mapped_rule_key": match.key if match else None,
        "mapped_ftw_field": match.ftw_field if match else None,
        "message": "Sample matched this rule." if match else "Sample did not match this rule.",
    }


@router.post("/qa-extraction")
async def qa_field_rule_extraction(
    document_type: DocumentType = Form(...),
    file: UploadFile = File(...),
    claims: dict = Depends(require_field_rule_admin),
):
    del claims
    file_name = Path(file.filename or "qa-document").name
    extension = Path(file_name).suffix.lower()
    if extension not in FIELD_RULE_QA_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Use a PDF, DOCX, XLSX, XLSM, CSV, or TXT document for extraction QA.",
        )
    if document_type not in {DocumentType.SCHEDULE_A, DocumentType.PLAN_WORKSHEET}:
        raise HTTPException(status_code=400, detail="Choose Schedule A or Plan Worksheet for extraction QA.")
    file_bytes = await file.read(FIELD_RULE_QA_MAX_BYTES + 1)
    if not file_bytes:
        raise HTTPException(status_code=400, detail="The QA document is empty.")
    if len(file_bytes) > FIELD_RULE_QA_MAX_BYTES:
        raise HTTPException(status_code=400, detail="The QA document must be 20 MB or smaller.")

    snapshot = await FieldRuleService(get_repository()).published_snapshot()
    try:
        return await run_field_rule_qa(
            file_bytes,
            file_name,
            document_type,
            snapshot.rules,
            rule_set_version=snapshot.version,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Extraction QA could not read this document: {exc}") from exc


@router.get("/{key}/history")
async def field_rule_history(key: str, claims: dict = Depends(require_field_rule_admin)):
    del claims
    return {"history": await FieldRuleService(get_repository()).history(key)}


@router.post("/{key}/publish")
async def publish_field_rule(
    key: str,
    payload: FieldRuleActionRequest,
    claims: dict = Depends(require_field_rule_admin),
):
    try:
        rule = await FieldRuleService(get_repository()).publish(
            key,
            actor=actor_from_claims(claims),
            reason=payload.reason,
        )
        return {"field_rule": rule}
    except FieldRuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{key}/disable")
async def disable_field_rule(
    key: str,
    payload: FieldRuleActionRequest,
    claims: dict = Depends(require_field_rule_admin),
):
    try:
        rule = await FieldRuleService(get_repository()).disable(
            key,
            actor=actor_from_claims(claims),
            reason=payload.reason,
        )
        return {"field_rule": rule}
    except FieldRuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{key}/rollback")
async def rollback_field_rule(
    key: str,
    payload: FieldRuleActionRequest,
    claims: dict = Depends(require_field_rule_admin),
):
    if payload.version is None:
        raise HTTPException(status_code=400, detail="A history version is required.")
    try:
        rule = await FieldRuleService(get_repository()).rollback(
            key,
            payload.version,
            actor=actor_from_claims(claims),
            reason=payload.reason,
        )
        return {"field_rule": rule}
    except FieldRuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
