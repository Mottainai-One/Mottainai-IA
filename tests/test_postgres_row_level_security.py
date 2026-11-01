"""Live RLS check for the official operational database.

Opt-in checks require a non-owner login inheriting mottainai_api. Without
an authenticated bootstrap, protected tables must expose no business rows.
The canonical schema owns RLS policies; IA does not change its DDL.
"""
import os
import unittest

from sqlalchemy import text

from app.database.postgres import engine


@unittest.skipUnless(os.getenv("MOTTAINAI_POSTGRES_INTEGRATION_TESTS") == "1", "Opt-in PostgreSQL integration check")
class RowLevelSecurityLeakTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_without_a_tenant_filter_sees_no_rows(self):
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
            self.skipTest(f"Postgres check unavailable ({type(exc).__name__})")

        for table, count in counts.items():
            self.assertEqual(
                count,
                0,
                f"{table}: expected 0 rows without a tenant filter under RLS, got {count}. "
                "If POSTGRES_DSN still connects as `mottainai` (the schema owner and a "
                "Postgres superuser), this is expected to fail — RLS never applies to "
                "that role regardless of FORCE. Point POSTGRES_DSN at a non-owner login inheriting `mottainai_api` "
                "(configured by the official runtime helper) "
                "for this to pass.",
            )


if __name__ == "__main__":
    unittest.main()
