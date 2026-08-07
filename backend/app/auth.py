import asyncio
from functools import lru_cache

import jwt
from jwt import PyJWKClient
from fastapi import HTTPException, Request

from app.config import Settings, get_settings


class AuthenticationError(Exception):
    pass


@lru_cache(maxsize=8)
def _jwks_client(issuer: str) -> PyJWKClient:
    return PyJWKClient(f"{issuer}/.well-known/jwks.json", cache_keys=True, lifespan=3600)


def _decode_cognito_id_token(token: str, settings: Settings) -> dict:
    issuer = f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/{settings.cognito_user_pool_id}"
    try:
        signing_key = _jwks_client(issuer).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.cognito_app_client_id,
            issuer=issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthenticationError("The login session is invalid or expired.") from exc
    except Exception as exc:
        raise AuthenticationError("The login session could not be verified.") from exc

    if claims.get("token_use") != "id":
        raise AuthenticationError("An ID token is required.")
    return claims


async def verify_cognito_id_token(token: str, settings: Settings) -> dict:
    return await asyncio.to_thread(_decode_cognito_id_token, token, settings)


def has_field_rule_admin_access(claims: dict | None, settings: Settings) -> bool:
    if not settings.auth_enabled:
        return True
    claims = claims or {}
    raw_groups = claims.get("cognito:groups") or []
    groups = {str(group).strip().lower() for group in (raw_groups if isinstance(raw_groups, list) else [raw_groups])}
    email = str(claims.get("email") or claims.get("cognito:username") or "").strip().lower()
    return "admins" in groups or email in settings.field_rules_admin_email_set


async def require_field_rule_admin(request: Request) -> dict:
    settings = get_settings()
    claims = getattr(request.state, "user", None)
    if not has_field_rule_admin_access(claims, settings):
        raise HTTPException(status_code=403, detail="Administrator access is required to manage field rules.")
    return claims or {"sub": "local-admin", "email": "local-admin"}
