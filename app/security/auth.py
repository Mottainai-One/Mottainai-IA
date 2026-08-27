"""Autenticação e autorização da API por JWT Bearer."""
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.settings import get_settings

_ALLOWED_ROLES = frozenset({"CLIENTE", "ESTOQUISTA", "GERENTE", "DONO"})
_JWT_SECRET_PLACEHOLDER_PREFIXES = ("replace_", "change_me", "your_", "example_")
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthContext:
    usuario_id: int
    empresa_id: int
    role: str


def _invalid_token() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token de acesso inválido.")


def _positive_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError
    parsed = int(value)
    if parsed < 1:
        raise ValueError
    return parsed


def is_configured_jwt_secret(secret: object) -> bool:
    """Distingue segredo JWT local configurado de placeholders de exemplo."""
    if not isinstance(secret, str):
        return False
    normalized = secret.strip().lower()
    return len(normalized) >= 32 and not normalized.startswith(_JWT_SECRET_PLACEHOLDER_PREFIXES)


def decode_access_token(token: str) -> AuthContext:
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
        return AuthContext(
            usuario_id=_positive_int(claims["sub"]),
            empresa_id=_positive_int(claims["empresa_id"]),
            role=role,
        )
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise _invalid_token() from exc


async def require_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _invalid_token()
    return decode_access_token(credentials.credentials)


def require_roles(*roles: str):
    allowed = frozenset(role.upper() for role in roles)

    async def dependency(principal: Annotated[AuthContext, Depends(require_auth)]) -> AuthContext:
        if principal.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permissão insuficiente.")
        return principal

    return dependency
