from fastapi import APIRouter
from app.services.field_rules import DEFAULT_FIELD_RULES

router = APIRouter(prefix="/field-rules", tags=["field-rules"])


@router.get("")
async def list_field_rules():
    return {"field_rules": DEFAULT_FIELD_RULES}
