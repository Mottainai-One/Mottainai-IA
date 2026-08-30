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
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.settings import get_settings

logger = logging.getLogger(__name__)

_ALLOWED_ROLES = frozenset({"CLIENTE", "ESTOQUISTA", "GERENTE", "DONO"})
_JWT_SECRET_PLACEHOLDER_PREFIXES = ("replace_", "change_me", "your_", "example_")
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthContext:
    usuario_id: int
    empresa_id: int
    role: str
    jti: str | None = None
    exp: int | None = None


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
    if not is_configured_jwt_secret(settings.jwt_secret):
        raise _invalid_token()

    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "empresa_id", "role"]},
        )
        role = str(claims["role"]).upper()
        if role not in _ALLOWED_ROLES:
            raise ValueError
        jti = claims.get("jti")
        exp = claims.get("exp")
        if jti and await _is_revoked(str(jti)):
            raise ValueError
        return AuthContext(
            usuario_id=_positive_int(claims["sub"]),
            empresa_id=_positive_int(claims["empresa_id"]),
            role=role,
            jti=str(jti) if jti else None,
            exp=_positive_int(exp) if exp else None,
        )
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise _invalid_token() from exc


async def require_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _invalid_token()
    return await decode_access_token(credentials.credentials)


def require_roles(*roles: str):
    allowed = frozenset(role.upper() for role in roles)

    async def dependency(principal: Annotated[AuthContext, Depends(require_auth)]) -> AuthContext:
        if principal.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permission.")
        return principal

    return dependency
