"""
Async connection to PostgreSQL (asyncpg + SQLAlchemy 2.0).
Mottainai's operational source of truth.
"""
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
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
    connect_args={"server_settings": {"statement_timeout": str(settings.postgres_statement_timeout_ms)}},
    echo=False,
)

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
    """Reject connections that silently bypass tenant isolation."""
    async with engine.connect() as connection:
        role = (await connection.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ))).mappings().one()
        if role["rolsuper"] or role["rolbypassrls"]:
            raise RuntimeError("PostgreSQL application role must be NOSUPERUSER NOBYPASSRLS")
        tables = (await connection.execute(text(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
            "EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid) AS has_policy "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'mottainai' AND c.relname IN "
            "('company', 'retail_store', 'employee', 'inventory', 'sales_transaction', 'purchase_order')"
        ))).mappings().all()
        if len(tables) != 6 or any(
            not row["relrowsecurity"] or not row["relforcerowsecurity"] or not row["has_policy"]
            for row in tables
        ):
            raise RuntimeError("PostgreSQL tenant tables require FORCE ROW LEVEL SECURITY and policies")
