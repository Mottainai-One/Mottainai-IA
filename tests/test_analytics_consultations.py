"""Tenant/store scope, publication boundaries and analytical value contracts."""
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.tools import analytics_tools as tools


def mapped(first=None, rows=None):
    return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: first, all=lambda: rows or []))


class Context:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        return False


class AnalyticalConsultationTests(unittest.IsolatedAsyncioTestCase):
    async def test_scope_is_derived_from_current_operational_permissions(self):
        publication = {"snapshot_date": date(2026, 10, 14),
                       "source_cutoff_at": datetime(2026, 10, 14, tzinfo=timezone.utc)}
        for admin, expected in ((False, 3), (True, None)):
            operational = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: admin)))
            analytic = SimpleNamespace(execute=AsyncMock(side_effect=[mapped(publication), mapped(rows=[{"quantity": Decimal("48")}])]))
            with (patch.object(tools, "get_pg_session", return_value=Context(operational)),
                  patch.object(tools, "initialize_pg_context", new=AsyncMock(return_value={"store_id": 3})),
                  patch.object(tools, "get_analytics_session", return_value=Context(analytic))):
                rows = await tools.get_inventory_status(7)
            sql, params = analytic.execute.await_args.args
            self.assertIn("s.company_id = :empresa_id", str(sql))
            self.assertEqual(params["empresa_id"], 7)
            self.assertEqual(params["scope_store_id"], expected)
            self.assertIn("mottainai_analytics.fact_inventory_snapshot", str(sql))
            self.assertNotIn("FROM mottainai.inventory", str(sql))
            self.assertEqual(rows[0]["quantity"], Decimal("48"))
            self.assertEqual(rows[0]["snapshot_date"], publication["snapshot_date"])

    async def test_unauthorized_store_never_reaches_analytics(self):
        operational = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: False)))
        with (patch.object(tools, "get_pg_session", return_value=Context(operational)),
              patch.object(tools, "initialize_pg_context", new=AsyncMock(return_value={"store_id": 3})),
              patch.object(tools, "get_analytics_session") as analytic):
            with self.assertRaises(PermissionError):
                await tools.get_inventory_status(7, store_id=99)
        analytic.assert_not_called()

    async def test_missing_publication_does_not_fall_back_to_operational_stock(self):
        operational = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: True)))
        analytic = SimpleNamespace(execute=AsyncMock(return_value=mapped()))
        with (patch.object(tools, "get_pg_session", return_value=Context(operational)),
              patch.object(tools, "initialize_pg_context", new=AsyncMock(return_value={"store_id": 3})),
              patch.object(tools, "get_analytics_session", return_value=Context(analytic))):
            with self.assertRaises(tools.AnalyticsPublicationUnavailable):
                await tools.get_inventory_status(7)
        self.assertEqual(analytic.execute.await_count, 1)
        self.assertNotIn("inventory", str(operational.execute.await_args.args[0]))

    async def test_sales_exclude_cancelled_headers_and_use_publication_window(self):
        with patch.object(tools, "_read", new=AsyncMock(return_value=[])) as read:
            await tools.get_sales_summary(7, days_back=28, store_id=3)
        self.assertIn("f.status='COMPLETED'", read.await_args.args[0])
        self.assertIn(":snapshot_date", read.await_args.args[0])
        self.assertEqual(read.await_args.kwargs["store_id"], 3)

    async def test_kpis_preserve_decimal_precision(self):
        values = [{"revenue": Decimal("0.1"), "disposal_cost": Decimal("0.2"), "active_alerts": 2,
                   "snapshot_date": date(2026, 10, 14), "source_cutoff_at": "published", "data_source": "analytics"},
                  {"revenue": Decimal("0.2"), "disposal_cost": Decimal("0.1"), "active_alerts": 1}]
        with patch.object(tools, "get_kpis_by_store", new=AsyncMock(return_value=values)):
            result = await tools.get_kpis(7)
        self.assertEqual(result["revenue_30d"], Decimal("0.3"))
        self.assertEqual(result["disposal_cost_30d"], Decimal("0.3"))
        self.assertEqual(result["active_alerts"], 3)

    async def test_invalid_company_or_store_is_rejected_before_connections(self):
        with patch.object(tools, "get_pg_session") as connection:
            for company, store in ((0, None), (True, None), (7, 0), (7, True)):
                with self.assertRaises(ValueError):
                    await tools.get_inventory_status(company, store)
        connection.assert_not_called()
