import asyncio
from functools import lru_cache

import jwt
from jwt import PyJWKClient

from app.config import Settings


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
