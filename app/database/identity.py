"""Verified request identity and the official operational RLS bootstrap."""
import hashlib
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import text


@dataclass(frozen=True)
class OperationalIdentity:
    user_id: int
    company_id: int
    role: str
    login_identity: str


current_identity: ContextVar[OperationalIdentity | None] = ContextVar(
    "operational_identity", default=None,
)

DATABASE_ROLES = {
    "ADMINISTRATOR": "DONO",
    "MANAGER": "GERENTE",
    "OPERATOR": "ESTOQUISTA",
}


async def initialize_pg_context(session, company_id: int) -> dict:
    """Bootstrap every transaction; never set protected tenant GUCs directly."""
    identity = current_identity.get()
    if identity is None or identity.company_id != company_id:
        raise PermissionError("Verified operational identity is required")
    initialized = await session.execute(text(
        "SELECT mottainai.fn_bootstrap_staff_context(:identity, 'EMAIL')"
    ), {"identity": identity.login_identity})
    if not initialized.scalar_one():
        raise PermissionError("Operational account is unavailable")
    result = await session.execute(text("""
        SELECT u.user_id, u.employee_id, s.company_id, s.store_id,
               upper(r.name) AS database_role
        FROM mottainai.app_user u
        JOIN mottainai.employee e ON e.employee_id = u.employee_id
        JOIN mottainai.employee_role r ON r.role_id = e.role_id
        JOIN mottainai.retail_store s ON s.store_id = e.store_id
        JOIN mottainai.company c ON c.company_id = s.company_id
        WHERE u.user_id = mottainai.fn_get_current_user_id()
          AND u.active AND e.active AND r.active AND s.active AND c.active
          AND u.deleted_at IS NULL AND e.deleted_at IS NULL
          AND r.deleted_at IS NULL AND s.deleted_at IS NULL AND c.deleted_at IS NULL
    """))
    actor = result.mappings().first()
    if (actor is None or actor["user_id"] != identity.user_id
            or actor["company_id"] != company_id
            or DATABASE_ROLES.get(actor["database_role"]) != identity.role):
        raise PermissionError("Operational identity or permissions have changed")
    return dict(actor)


async def resolve_firebase_identity(uid: str, email: str) -> OperationalIdentity:
    """Require the pre-existing UID binding; never provision accounts at login."""
    from app.database.postgres import get_pg_session

    login_identity = "EMAIL_SHA256_" + hashlib.sha256(email.strip().lower().encode()).hexdigest().upper()
    async with get_pg_session() as session:
        initialized = await session.execute(text(
            "SELECT mottainai.fn_bootstrap_staff_context(:identity, 'EMAIL')"
        ), {"identity": login_identity})
        if not initialized.scalar_one():
            raise PermissionError("Firebase account has no active operational binding")
        result = await session.execute(text("""
            SELECT u.user_id, s.company_id, upper(r.name) AS database_role
            FROM mottainai.app_user u
            JOIN mottainai.employee e ON e.employee_id=u.employee_id
            JOIN mottainai.employee_role r ON r.role_id=e.role_id
            JOIN mottainai.retail_store s ON s.store_id=e.store_id
            JOIN mottainai.company c ON c.company_id=s.company_id
            WHERE u.user_id=mottainai.fn_get_current_user_id()
              AND u.firebase_uid=:uid AND lower(u.email)=lower(:identity)
              AND u.active AND e.active AND r.active AND s.active AND c.active
              AND u.deleted_at IS NULL AND e.deleted_at IS NULL
              AND r.deleted_at IS NULL AND s.deleted_at IS NULL AND c.deleted_at IS NULL
        """), {"uid": uid, "identity": login_identity})
        actor = result.mappings().first()
        role = DATABASE_ROLES.get(actor["database_role"]) if actor else None
        if not role:
            raise PermissionError("Firebase UID or operational role is not authorized")
        return OperationalIdentity(actor["user_id"], actor["company_id"], role, login_identity)
