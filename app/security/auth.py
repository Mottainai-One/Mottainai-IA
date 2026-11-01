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
import base64
import binascii
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.settings import get_settings

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
    operational_identity: str | None = None
    store_id: int | None = None


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
    """Decode Base64 secrets exactly as the SQL API's JWT signer does."""
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
        algorithm = jwt.get_unverified_header(token).get("alg")
    except jwt.PyJWTError as exc:
        raise _invalid_token() from exc
    if algorithm == "RS256":
        from app.database.identity import (
            resolve_firebase_customer_identity,
            resolve_firebase_identity,
        )
        from app.security.firebase_auth import (
            FirebaseVerificationUnavailable,
            verify_firebase_id_token,
        )

        try:
            firebase = await verify_firebase_id_token(token)
        except jwt.PyJWTError as exc:
            raise _invalid_token() from exc
        except FirebaseVerificationUnavailable as exc:
            raise HTTPException(status_code=503, detail="Não foi possível validar sua sessão. Tente novamente.") from exc
        jti = "firebase-" + hashlib.sha256(token.encode()).hexdigest()
        if await _is_revoked(jti):
            raise _invalid_token()
        customer = await resolve_firebase_customer_identity(firebase.uid)
        if customer is not None:
            # Negative IDs keep customer chat/session/memory keys disjoint
            # from employee app_user IDs in Mongo and Redis.
            return AuthContext(
                -customer.customer_id, customer.chat_company_id, "CLIENTE",
                jti, firebase.expires_at, None,
            )
        identity = await resolve_firebase_identity(firebase.uid)
        return AuthContext(identity.user_id, identity.company_id, identity.role,
                           jti, firebase.expires_at, identity.login_identity,
                           identity.store_id)
    if not is_configured_jwt_secret(settings.jwt_secret):
        raise _invalid_token()

    try:
        decode_options: dict[str, object] = {
            "algorithms": [settings.jwt_algorithm],
            "options": {"require": ["exp", "sub", "empresa_id", "role"]},
        }
        issuer = getattr(settings, "jwt_issuer", "").strip()
        audience = getattr(settings, "jwt_audience", "").strip()
        if issuer:
            decode_options["issuer"] = issuer
        if audience:
            decode_options["audience"] = audience
        signing_key = jwt_verification_key(settings)
        claims = jwt.decode(token, signing_key, **decode_options)
        role = str(claims["role"]).upper()
        if role not in _ALLOWED_ROLES:
            raise ValueError
        if claims.get("use") not in (None, "access"):
            raise ValueError
        jti = claims.get("jti")
        exp = claims.get("exp")
        operational_identity = claims.get("operational_identity")
        if operational_identity is not None and (
            not isinstance(operational_identity, str)
            or re.fullmatch(r"EMAIL_SHA256_[A-Fa-f0-9]{64}", operational_identity) is None
        ):
            raise ValueError
        if jti and await _is_revoked(str(jti)):
            raise ValueError
        if "usuario_id" in claims:
            firebase_uid = claims["sub"]
            if not isinstance(firebase_uid, str) or not firebase_uid.strip():
                raise ValueError
            from app.database.identity import resolve_firebase_identity

            identity = await resolve_firebase_identity(firebase_uid)
            user_id = _positive_int(claims["usuario_id"])
            company_id = _positive_int(claims["empresa_id"])
            claimed_store = _positive_int(claims["store_id"]) if claims.get("store_id") is not None else None
            if (identity.user_id != user_id or identity.company_id != company_id
                    or identity.role != role
                    or (claimed_store is not None and identity.store_id != claimed_store)):
                raise ValueError
            operational_identity = identity.login_identity
            store_id = identity.store_id
        else:
            user_id = _positive_int(claims["sub"])
            company_id = _positive_int(claims["empresa_id"])
            store_id = None
        return AuthContext(
            usuario_id=user_id,
            empresa_id=company_id,
            role=role,
            jti=str(jti) if jti else None,
            exp=_positive_int(exp) if exp else None,
            operational_identity=operational_identity,
            store_id=store_id,
        )
    except (jwt.PyJWTError, binascii.Error, KeyError, TypeError, ValueError, PermissionError) as exc:
        raise _invalid_token() from exc


async def require_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _invalid_token()
    principal = await decode_access_token(credentials.credentials)
    from app.database.identity import OperationalIdentity, current_identity
    current_identity.set(
        OperationalIdentity(principal.usuario_id, principal.empresa_id,
                            principal.role, principal.operational_identity, principal.store_id)
        if principal.operational_identity and principal.role != "CLIENTE" else None
    )
    return principal


def require_roles(*roles: str):
    allowed = frozenset(role.upper() for role in roles)

    async def dependency(principal: Annotated[AuthContext, Depends(require_auth)]) -> AuthContext:
        if principal.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permission.")
        return principal

    return dependency


def require_operational_roles(*roles: str):
    """Revalidate the database account before any write or cached replay."""
    role_dependency = require_roles(*roles)

    async def dependency(principal: Annotated[AuthContext, Depends(role_dependency)]) -> AuthContext:
        from app.database.identity import initialize_pg_context
        from app.database.postgres import get_pg_session

        async with get_pg_session() as session:
            await initialize_pg_context(session, principal.empresa_id)
        return principal

    return dependency
