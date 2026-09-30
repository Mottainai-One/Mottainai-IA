"""
Pool wiring for app/database/postgres.py (PR: perf/postgres-connection-pool).

Requires a real Postgres reachable at settings.postgres_dsn — skips (not
fails) when it isn't, same as CI, which runs `unittest discover` with no
Postgres service. This can't be proven with the mocked FakeSession used by
tests/test_postgres_contracts.py: the whole point is real connection reuse
across two separate transactions, which a mock doesn't have.
"""
import unittest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.config import get_settings
from app.database.postgres import engine as app_engine


class PostgresPoolConfigurationTests(unittest.TestCase):
    def test_engine_uses_a_real_pool_sized_from_settings(self):
        settings = get_settings()
        self.assertIsInstance(app_engine.pool, AsyncAdaptedQueuePool)
        self.assertEqual(app_engine.pool.size(), settings.postgres_pool_size)


class PostgresPoolTenantIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_set_config_does_not_leak_across_a_reused_pooled_connection(self):
        settings = get_settings()
        # pool_size=1 forces the second `connect()` below to reuse the
        # exact same physical connection as the first, deterministically —
        # the scenario the app's shared pool only produces by chance.
        test_engine = create_async_engine(settings.postgres_dsn, pool_size=1, max_overflow=0)
        try:
            async with test_engine.connect() as conn:
                await conn.execute(
                    text("SELECT set_config('app.current_company_id', '1', true)")
                )
                await conn.commit()
        except Exception as exc:  # pragma: no cover - environment-dependent
            await test_engine.dispose()
            self.skipTest(f"Postgres unreachable at {settings.postgres_dsn!r}: {exc}")

        try:
            # New transaction, same underlying connection, and this one
            # never calls set_config itself. If `is_local=true` (the
            # third argument in app/tools/postgres_tools.py's set_config
            # calls) didn't scope the setting to the first transaction,
            # this would still see '1' here.
            async with test_engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT current_setting('app.current_company_id', true) AS v")
                )
                leaked_value = result.scalar()
        finally:
            await test_engine.dispose()

        # Empirically, Postgres reverts a custom GUC set with is_local=true
        # to an empty string once its transaction ends, not NULL — accept
        # either so the assertion tracks the guarantee (not '1' leaking
        # through), not one server version's exact internal representation.
        self.assertIn(leaked_value, (None, ""))


if __name__ == "__main__":
    unittest.main()
