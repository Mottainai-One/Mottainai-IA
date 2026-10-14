"""Read-only connection to the official physical analytics database."""
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config.settings import get_settings

settings = get_settings()
engine = None

REQUIRED_RELATIONS = (
    "dim_company", "dim_store", "dim_product", "fact_sales", "fact_sale_item",
    "fact_loss_destination", "fact_alert_snapshot", "vw_published_batch",
    "fact_inventory_snapshot",
)


def get_analytics_engine():
    global engine
    if not settings.analytics_postgres_dsn:
        raise RuntimeError("Configure ANALYTICS_POSTGRES_DSN for the physical analytics database")
    operational = make_url(settings.postgres_dsn)
    analytical = make_url(settings.analytics_postgres_dsn)
    if (operational.host, operational.port, operational.database) == (
        analytical.host, analytical.port, analytical.database,
    ):
        raise RuntimeError("Operational and analytical connections must use separate physical databases")
    if engine is None:
        engine = create_async_engine(
            settings.analytics_postgres_dsn, pool_pre_ping=True,
            pool_size=settings.postgres_pool_size, max_overflow=settings.postgres_max_overflow,
            connect_args={"server_settings": {
                "default_transaction_read_only": "on",
                "search_path": "pg_catalog,mottainai_analytics",
                "statement_timeout": str(settings.postgres_statement_timeout_ms),
            }},
        )
    return engine


@asynccontextmanager
async def get_analytics_session():
    factory = async_sessionmaker(get_analytics_engine(), expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            yield session


async def validate_analytics_security():
    async with get_analytics_session() as session:
        flags = (await session.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ))).mappings().one()
        if flags["rolsuper"] or flags["rolbypassrls"]:
            raise RuntimeError("Analytics runtime must be NOSUPERUSER NOBYPASSRLS")
        for relation in REQUIRED_RELATIONS:
            valid = (await session.execute(text("""
                SELECT to_regclass(:relation) IS NOT NULL
                   AND has_table_privilege(current_user, :relation, 'SELECT')
            """), {"relation": f"mottainai_analytics.{relation}"})).scalar_one()
            if not valid:
                raise RuntimeError(f"Analytics read contract unavailable: {relation}")
        writable = (await session.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'mottainai_analytics' AND c.relkind IN ('r','p','v','m')
                  AND (pg_has_role(current_user, c.relowner, 'USAGE')
                       OR has_table_privilege(current_user, c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE'))
            )
        """))).scalar_one()
        if writable:
            raise RuntimeError("Analytics runtime must have SELECT privileges only")


async def close_analytics_engine():
    global engine
    if engine is not None:
        await engine.dispose()
        engine = None
