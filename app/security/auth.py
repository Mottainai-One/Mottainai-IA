"""API authentication and authorization via JWT Bearer.

This service is a token *verifier*, not an issuer — nothing here mints
tokens for a login flow, so a refresh-token flow doesn't belong in this
repo (it would belong to whichever system issues the JWTs). What this
service CAN and does own: revoking a token it verifies, before its natural
expiry, via a Redis deny-list keyed by the token's "jti" claim.

Checking the deny-list is fail-open (a Redis outage must not lock every
authenticated request out of the whole API) — same tradeoff as the RAG
cache in app/rag/retriever.py. Writing to it (actually revoking a token) is
NOT fail-open: if that write fails we raise, because silently pretending a
token was revoked when it wasn't is worse than a clear error.

Tokens minted without a "jti" (e.g. by an older client, or an external
issuer that hasn't adopted the claim yet) still decode successfully — they
just can't be individually revoked.
"""
import logging
import time
import base64
import binascii
import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.settings import get_settings
from app.security.tenant_context import TenantIdentity, reset_tenant_identity, set_tenant_identity
from app.security.firebase_auth import firebase_provider_unavailable, verify_firebase_id_token

logger = logging.getLogger(__name__)

_ALLOWED_ROLES = frozenset({"CLIENTE", "ESTOQUISTA", "GERENTE", "DONO"})
_JWT_SECRET_PLACEHOLDER_PREFIXES = (
    "replace_", "change_me", "your_", "example_", "gere-um-segredo",
)
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthContext:
    usuario_id: int
    empresa_id: int
    role: str
    jti: str | None = None
    exp: int | None = None
    store_id: int | None = None
    firebase_uid: str = ""


def _invalid_token() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token.")


def _positive_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError
    parsed = int(value)
    if parsed < 1:
        raise ValueError
    return parsed


def is_configured_jwt_secret(secret: object) -> bool:
    """Distinguishes a locally configured JWT secret from example placeholders."""
    if not isinstance(secret, str):
        return False
    normalized = secret.strip().lower()
    return len(normalized) >= 32 and not normalized.startswith(_JWT_SECRET_PLACEHOLDER_PREFIXES)


def is_configured_shared_secret(secret: object) -> bool:
    """Validates integration secrets using the same rules as the JWT secret."""
    return is_configured_jwt_secret(secret)


def jwt_verification_key(settings: object) -> str | bytes:
    """Returns the signing key in the same byte representation used by the issuer."""
    secret = getattr(settings, "jwt_secret", "")
    if not is_configured_jwt_secret(secret):
        raise ValueError("JWT_SECRET is missing, weak or a placeholder")
    if getattr(settings, "jwt_secret_base64", False):
        key = base64.b64decode(secret, validate=True)
        if len(key) < 32:
            raise ValueError("Base64 JWT_SECRET must decode to at least 32 bytes")
        return key
    return secret


async def _is_revoked(jti: str) -> bool:
    try:
        from app.cache.keyspace import revoked_token
        from app.database.redis_client import get_redis

        return bool(await get_redis().exists(revoked_token(jti)))
    except Exception:
        logger.warning("Token-revocation check unavailable — failing open", exc_info=True)
        return False


async def revoke_token(jti: str, exp: int) -> None:
    """
    Adds a token's jti to the Redis deny-list until its natural expiry.
    NOT fail-open: if this can't be persisted, the caller must know the
    token is still valid, so the error propagates instead of being swallowed.
    """
    ttl = exp - int(time.time())
    if ttl <= 0:
        return  # already expired on its own — nothing to revoke

    from app.cache.keyspace import revoked_token
    from app.database.redis_client import get_redis

    await get_redis().set(revoked_token(jti), "1", ex=ttl)


async def decode_access_token(token: str) -> AuthContext:
    settings = get_settings()
    try:
        unverified_claims = jwt.decode(token, options={"verify_signature": False})
    except (jwt.PyJWTError, TypeError, ValueError) as exc:
        raise _invalid_token() from exc

    firebase_issuer = f"https://securetoken.google.com/{getattr(settings, 'firebase_project_id', '')}"
    firebase_project_id = getattr(settings, "firebase_project_id", "")
    if firebase_project_id and unverified_claims.get("iss") == firebase_issuer:
        try:
            claims = await asyncio.to_thread(
                verify_firebase_id_token, token, firebase_project_id
            )
        except Exception as exc:
            if firebase_provider_unavailable(exc):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Firebase token verification is temporarily unavailable.",
                ) from exc
            raise _invalid_token() from exc

        role = str(claims.get("role", "")).upper()
        if role not in _ALLOWED_ROLES:
            raise _invalid_token()
        try:
            firebase_uid = str(claims["sub"])
            claimed_user_id = _positive_int(claims["user_id"])
            raw_store_ids = [claims[key] for key in ("store_id", "storeID", "storeId") if claims.get(key) is not None]
            claimed_store_id = _positive_int(raw_store_ids[0]) if raw_store_ids else None
            if any(_positive_int(value) != claimed_store_id for value in raw_store_ids):
                raise ValueError

            from sqlalchemy import text
            from app.database.postgres import get_pg_session

            async with get_pg_session() as session:
                established = await session.execute(
                    text("SELECT mottainai.fn_bootstrap_staff_context(:uid, 'FIREBASE_UID')"),
                    {"uid": firebase_uid},
                )
                if not established.scalar():
                    raise ValueError
                resolved = await session.execute(text(
                    "SELECT mottainai.fn_get_current_user_id() AS usuario_id, "
                    "mottainai.fn_get_current_company_id() AS empresa_id, "
                    "mottainai.fn_get_current_store_id() AS store_id"
                ))
                identity = resolved.mappings().one()
                usuario_id = _positive_int(identity["usuario_id"])
                empresa_id = _positive_int(identity["empresa_id"])
                store_id = _positive_int(identity["store_id"])
                if usuario_id != claimed_user_id or (claimed_store_id is not None and store_id != claimed_store_id):
                    raise ValueError

            return AuthContext(
                usuario_id=usuario_id,
                empresa_id=empresa_id,
                role=role,
                exp=_positive_int(claims.get("exp")) if claims.get("exp") else None,
                store_id=store_id,
                firebase_uid=firebase_uid,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise _invalid_token() from exc

    if not is_configured_jwt_secret(settings.jwt_secret):
        raise _invalid_token()

    try:
        decode_options: dict[str, object] = {
            "algorithms": [settings.jwt_algorithm],
            "options": {"require": ["exp", "sub", "empresa_id", "role", "use"]},
        }
        issuer = getattr(settings, "jwt_issuer", "").strip()
        audience = getattr(settings, "jwt_audience", "").strip()
        if issuer:
            decode_options["issuer"] = issuer
        if audience:
            decode_options["audience"] = audience
        signing_key = jwt_verification_key(settings)
        claims = jwt.decode(token, signing_key, **decode_options)
        firebase_uid = claims["sub"]
        if not isinstance(firebase_uid, str) or not firebase_uid.strip():
            raise ValueError
        if claims["use"] != "access":
            raise ValueError
        role = str(claims["role"]).upper()
        if role not in _ALLOWED_ROLES:
            raise ValueError
        jti = claims.get("jti")
        exp = claims.get("exp")
        if jti and await _is_revoked(str(jti)):
            raise ValueError
        return AuthContext(
            usuario_id=_positive_int(claims.get("usuario_id", claims["sub"])),
            empresa_id=_positive_int(claims["empresa_id"]),
            role=role,
            jti=str(jti) if jti else None,
            exp=_positive_int(exp) if exp else None,
            store_id=_positive_int(claims["store_id"]) if claims.get("store_id") is not None else None,
            firebase_uid=firebase_uid,
        )
    except (jwt.PyJWTError, binascii.Error, KeyError, TypeError, ValueError) as exc:
        raise _invalid_token() from exc


async def require_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AsyncIterator[AuthContext]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _invalid_token()
    principal = await decode_access_token(credentials.credentials)
    token = set_tenant_identity(TenantIdentity(
        firebase_uid=principal.firebase_uid,
        usuario_id=principal.usuario_id,
        empresa_id=principal.empresa_id,
        store_id=principal.store_id,
    ))
    try:
        yield principal
    finally:
        reset_tenant_identity(token)


def require_roles(*roles: str):
    allowed = frozenset(role.upper() for role in roles)

    async def dependency(principal: Annotated[AuthContext, Depends(require_auth)]) -> AuthContext:
        if principal.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permission.")
        return principal

    return dependency
