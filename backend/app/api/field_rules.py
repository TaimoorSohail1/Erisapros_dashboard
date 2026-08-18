from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import has_field_rule_admin_access, require_field_rule_admin
from app.config import get_settings
from app.models import FieldRuleActionRequest, FieldRuleDraftRequest, FieldRuleTestRequest
from app.repositories import get_repository, retry_repository_read
from app.services.field_rule_admin import FieldRuleService, FieldRuleValidationError
from app.services.field_rules import find_rule_for_field

router = APIRouter(prefix="/field-rules", tags=["field-rules"])


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
                "update_supported": bool(FieldRuleService.approved_update_tag(rule.key)),
                "approved_update_tag": FieldRuleService.approved_update_tag(rule.key),
            }
            for rule in rules
        ],
        "published_version": snapshot.version,
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
