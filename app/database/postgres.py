"""
Async connection to PostgreSQL (asyncpg + SQLAlchemy 2.0).
Mottainai's operational source of truth.
"""
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.postgres_dsn,
    poolclass=AsyncAdaptedQueuePool,
    pool_size=settings.postgres_pool_size,
    max_overflow=settings.postgres_max_overflow,
    pool_timeout=settings.postgres_pool_timeout_seconds,
    pool_pre_ping=settings.postgres_pool_pre_ping,
    connect_args={"server_settings": {
        "statement_timeout": str(settings.postgres_statement_timeout_ms),
        "search_path": "pg_catalog,mottainai",
    }},
    echo=False,
)


async def register_operational_enum_codecs(connection) -> None:
    """Avoid asyncpg's set_config-based JIT introspection on protected roles."""
    enums = await connection.fetch("""
        SELECT t.typname::text AS name
        FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'mottainai' AND t.typtype = 'e'
    """)
    for enum in enums:
        await connection.set_type_codec(
            enum["name"], schema="mottainai", encoder=str, decoder=str, format="text",
        )


@event.listens_for(engine.sync_engine, "connect")
def configure_operational_connection(connection, record) -> None:
    connection.run_async(register_operational_enum_codecs)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_pg_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_pg_engine() -> None:
    """Releases pooled PostgreSQL connections during application shutdown."""
    await engine.dispose()


async def validate_pg_security() -> None:
    """Validate the official group's RLS without altering the database."""
    async with engine.connect() as connection:
        role = (await connection.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ))).mappings().one()
        if role["rolsuper"] or role["rolbypassrls"]:
            raise RuntimeError("PostgreSQL application role must be NOSUPERUSER NOBYPASSRLS")
        tables = (await connection.execute(text(
            "SELECT c.relname, c.relrowsecurity, "
            "pg_has_role(current_user, c.relowner, 'USAGE') AS owns_table, "
            "EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid) AS has_policy "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'mottainai' AND c.relname IN "
            "('company', 'retail_store', 'employee', 'inventory', 'sales_transaction', "
            "'purchase_order', 'product', 'app_user', 'batch', 'alert', 'disposal')"
        ))).mappings().all()
        if len(tables) != 11 or any(
            not row["relrowsecurity"] or row["owns_table"] or not row["has_policy"]
            for row in tables
        ):
            raise RuntimeError("PostgreSQL requires the official RLS policies and a non-owner runtime role")
        allowed = (await connection.execute(text(
            "SELECT pg_has_role(current_user, 'mottainai_api', 'USAGE')"
        ))).scalar_one()
        if not allowed:
            raise RuntimeError("PostgreSQL runtime role must inherit mottainai_api")
        from app.database.operational_schema import OPERATIONAL_SCHEMA_READY_QUERY
        ready = (await connection.execute(text(OPERATIONAL_SCHEMA_READY_QUERY))).scalar_one()
        if not ready:
            raise RuntimeError("Official operational database contract is incomplete")
