"""Request-local identity used to bootstrap the database's RLS context."""
from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class TenantIdentity:
    firebase_uid: str
    usuario_id: int
    empresa_id: int
    store_id: int | None


_tenant_identity: ContextVar[TenantIdentity | None] = ContextVar("tenant_identity", default=None)


def set_tenant_identity(identity: TenantIdentity) -> Token[TenantIdentity | None]:
    return _tenant_identity.set(identity)


def reset_tenant_identity(token: Token[TenantIdentity | None]) -> None:
    _tenant_identity.reset(token)


def get_tenant_identity() -> TenantIdentity | None:
    return _tenant_identity.get()
