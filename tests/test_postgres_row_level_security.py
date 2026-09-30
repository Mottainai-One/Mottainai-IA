"""
Row Level Security enforcement (PR: fix/enforce-row-level-security).

Requires a real Postgres reachable at settings.postgres_dsn — skips (not
fails) when it isn't, same as CI, which runs `unittest discover` with no
Postgres service.

Unlike tests/test_postgres_pool.py, this one's pass/fail is a direct
reflection of the *deployed* state of the migration, not just the code:
- Skips if Postgres is unreachable (CI, or no local Postgres running).
- FAILS if Postgres is reachable but POSTGRES_DSN still connects as the
  schema-owner/superuser role (`mottainai`) — RLS policies exist but do
  nothing for that role, by Postgres's own design, and this is
  precisely the bug this PR closes. This is the "test that fails
  first" the migration is meant to prove.
- PASSES once POSTGRES_DSN has been switched to the least-privilege
  `mottainai_app` role created by this PR's block in
  scripts/sql/mottainai-v6.schema.sql, with FORCE ROW LEVEL SECURITY
  applied to the tables below.
"""
import unittest

from sqlalchemy import text

from app.config import get_settings
from app.database.postgres import engine


class RowLevelSecurityLeakTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_without_a_tenant_filter_sees_no_rows(self):
        settings = get_settings()
        try:
            async with engine.connect() as conn:
                # No set_config('app.current_company_id', ...) call at
                # all here — the "someone forgot to scope this query"
                # scenario. RLS is the only thing that can still save
                # us; the app's own WHERE clauses are not involved.
                counts = {}
                for table in ("company", "retail_store", "inventory", "sales_transaction"):
                    result = await conn.execute(text(f"SELECT count(*) FROM mottainai.{table}"))
                    counts[table] = result.scalar()
        except Exception as exc:  # pragma: no cover - environment-dependent
            await engine.dispose()
            self.skipTest(f"Postgres unreachable at {settings.postgres_dsn!r}: {exc}")

        for table, count in counts.items():
            self.assertEqual(
                count,
                0,
                f"{table}: expected 0 rows without a tenant filter under RLS, got {count}. "
                "If POSTGRES_DSN still connects as `mottainai` (the schema owner and a "
                "Postgres superuser), this is expected to fail — RLS never applies to "
                "that role regardless of FORCE. Point POSTGRES_DSN at `mottainai_app` "
                "(created by this PR's block in scripts/sql/mottainai-v6.schema.sql) "
                "for this to pass.",
            )


if __name__ == "__main__":
    unittest.main()
