"""Contratos das consultas da IA Layer com o schema operacional v6."""
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.tools import postgres_tools
from app.tools.vision_tools import crosscheck_with_inventory


class FakeResult:
    def __init__(self, keys: list[str] | None = None, rows: list[tuple] | None = None):
        self._keys = keys or []
        self._rows = rows or []

    def keys(self):
        return self._keys

    def fetchall(self):
        return self._rows


class FakeSession:
    def __init__(self, results: list[FakeResult]):
        self._results = iter(results)
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return next(self._results)


class FakeSessionContext:
    def __init__(self, session: FakeSession):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class PostgresExecutionContracts(unittest.IsolatedAsyncioTestCase):
    async def test_sets_transaction_local_company_context_before_query(self):
        session = FakeSession([FakeResult(), FakeResult(["id"], [(7,)])])
        with patch(
            "app.tools.postgres_tools.get_pg_session",
            return_value=FakeSessionContext(session),
        ):
            rows = await postgres_tools._exec(
                "SELECT :empresa_id AS id",
                empresa_id=42,
            )

        self.assertEqual(rows, [{"id": 7}])
        context_sql, context_params = session.calls[0]
        query_sql, query_params = session.calls[1]
        self.assertIn("set_config('app.current_company_id'", context_sql)
        self.assertEqual(context_params, {"empresa_id": "42"})
        self.assertIn("SELECT :empresa_id AS id", query_sql)
        self.assertEqual(query_params["empresa_id"], 42)

    async def test_rejects_invalid_company_context(self):
        with self.assertRaises(ValueError):
            await postgres_tools._exec("SELECT 1", empresa_id=0)


class PostgresSchemaContracts(unittest.IsolatedAsyncioTestCase):
    async def test_stock_alerts_binds_optional_store_filter(self):
        with patch(
            "app.tools.postgres_tools._exec",
            new=AsyncMock(return_value=[]),
        ) as execute:
            await postgres_tools.get_stock_alerts(42, limit=5, store_id=99)

        sql = execute.await_args.args[0]
        self.assertIn("AND a.store_id = :store_id", sql)
        self.assertEqual(execute.await_args.kwargs["empresa_id"], 42)
        self.assertEqual(execute.await_args.kwargs["params"], {"limit": 5, "store_id": 99})

    async def test_expiring_batches_uses_v6_relations_and_store_identity(self):
        with patch(
            "app.tools.postgres_tools._exec",
            new=AsyncMock(return_value=[]),
        ) as execute:
            await postgres_tools.get_expiring_batches(42, days_ahead=14)

        sql = execute.await_args.args[0]
        self.assertIn("mottainai.inventory i ON i.batch_id = b.batch_id", sql)
        self.assertIn("p.barcode AS barcode", sql)
        self.assertNotIn("p.sku", sql)
        self.assertIn("rs.store_id", sql)
        self.assertIn("i.deleted_at IS NULL", sql)
        self.assertIn("GROUP BY", sql)
        self.assertEqual(execute.await_args.kwargs["empresa_id"], 42)
        self.assertEqual(execute.await_args.kwargs["params"]["days_ahead"], 14)

    async def test_sales_summary_excludes_cancelled_and_deleted_records(self):
        with patch(
            "app.tools.postgres_tools._exec",
            new=AsyncMock(return_value=[]),
        ) as execute:
            await postgres_tools.get_sales_summary(42, days_back=60)

        sql = execute.await_args.args[0]
        self.assertIn("si.sale_id = st.sale_id AND si.sale_date = st.sale_date", sql)
        # sale_item.status is an enum of SOLD/CANCELED/RETURNED, and the whole
        # point of this test's name is that the summary counts only real sales.
        # It used to assert the opposite (assertNotIn "si.status"), pinning the
        # missing filter in place: cancelled and returned line items were summed
        # into "top produtos" and into the demand forecast that reads this data.
        # get_kpis and get_kpis_by_store already filtered it — these two did not.
        self.assertIn("si.status = 'SOLD'", sql)
        self.assertIn("st.deleted_at IS NULL", sql)
        self.assertIn("p.barcode     AS barcode", sql)
        self.assertNotIn("p.sku", sql)
        self.assertNotIn("AND rs.store_id = :store_id", sql)
        self.assertEqual(execute.await_args.kwargs["empresa_id"], 42)
        self.assertEqual(execute.await_args.kwargs["params"]["days_back"], 60)
        self.assertNotIn("store_id", execute.await_args.kwargs["params"])

    async def test_daily_sales_series_counts_only_sold_line_items(self):
        # This series is the demand forecast's only input
        # (app/analytics/forecasting.py). Counting CANCELED/RETURNED items here
        # inflates every prediction, silently and in the same direction.
        with patch(
            "app.tools.postgres_tools._exec",
            new=AsyncMock(return_value=[]),
        ) as execute:
            await postgres_tools.get_daily_sales_series(42, [10], days_back=28)

        self.assertIn("si.status = 'SOLD'", execute.await_args.args[0])

    async def test_sales_summary_binds_optional_store_filter(self):
        with patch(
            "app.tools.postgres_tools._exec",
            new=AsyncMock(return_value=[]),
        ) as execute:
            await postgres_tools.get_sales_summary(42, days_back=60, store_id=7)

        sql = execute.await_args.args[0]
        self.assertIn("AND rs.store_id = :store_id", sql)
        self.assertEqual(execute.await_args.kwargs["params"]["store_id"], 7)

    async def test_sales_summary_rejects_invalid_store_id(self):
        with self.assertRaises(ValueError):
            await postgres_tools.get_sales_summary(42, store_id=0)

    async def test_expiring_batches_binds_optional_store_filter(self):
        with patch(
            "app.tools.postgres_tools._exec",
            new=AsyncMock(return_value=[]),
        ) as execute:
            await postgres_tools.get_expiring_batches(42, days_ahead=14, store_id=7)

        sql = execute.await_args.args[0]
        self.assertIn("AND rs.store_id = :store_id", sql)
        self.assertEqual(execute.await_args.kwargs["params"]["store_id"], 7)

    async def test_expiring_batches_rejects_invalid_store_id(self):
        with self.assertRaises(ValueError):
            await postgres_tools.get_expiring_batches(42, store_id=-1)

    async def test_daily_sales_series_binds_product_ids_and_skips_query_when_empty(self):
        with patch(
            "app.tools.postgres_tools._exec",
            new=AsyncMock(return_value=[]),
        ) as execute:
            empty_result = await postgres_tools.get_daily_sales_series(42, [], days_back=28)
            await postgres_tools.get_daily_sales_series(42, [10, 20], days_back=28)

        self.assertEqual(empty_result, [])
        execute.assert_awaited_once()  # the empty-product-ids call never touched the DB
        sql = execute.await_args.args[0]
        self.assertIn("p.product_id = ANY(:product_ids)", sql)
        # sale_date is a `timestamp`, so it must be cast to a date to actually
        # aggregate per day — grouping by the raw timestamp yields one row per
        # sale instant and a series that never lines up with a date lookup.
        self.assertIn("CAST(st.sale_date AS DATE) AS sale_date", sql)
        self.assertIn("GROUP BY p.product_id, CAST(st.sale_date AS DATE)", sql)
        self.assertEqual(execute.await_args.kwargs["empresa_id"], 42)
        self.assertEqual(
            execute.await_args.kwargs["params"],
            {"days_back": 28, "product_ids": [10, 20]},
        )

    async def test_daily_sales_series_binds_optional_store_filter(self):
        with patch(
            "app.tools.postgres_tools._exec",
            new=AsyncMock(return_value=[]),
        ) as execute:
            await postgres_tools.get_daily_sales_series(42, [10], days_back=28, store_id=7)

        sql = execute.await_args.args[0]
        self.assertIn("AND rs.store_id = :store_id", sql)
        self.assertEqual(execute.await_args.kwargs["params"]["store_id"], 7)

    async def test_daily_sales_series_rejects_invalid_store_id(self):
        with self.assertRaises(ValueError):
            await postgres_tools.get_daily_sales_series(42, [10], store_id=0)

    async def test_inventory_query_keeps_store_filter_bound(self):
        with patch(
            "app.tools.postgres_tools._exec",
            new=AsyncMock(return_value=[]),
        ) as execute:
            await postgres_tools.get_inventory_status(42, store_id=99)

        sql = execute.await_args.args[0]
        self.assertIn("AND i.store_id = :store_id", sql)
        self.assertIn("mottainai.batch b ON b.batch_id = i.batch_id", sql)
        self.assertNotIn("p.sku", sql)
        self.assertNotIn("p.barcode     AS sku", sql)
        self.assertEqual(execute.await_args.kwargs["params"]["store_id"], 99)

    async def test_inventory_query_rejects_invalid_store_id(self):
        with self.assertRaises(ValueError):
            await postgres_tools.get_inventory_status(42, store_id=0)

    async def test_inventory_match_uses_company_scoped_batches(self):
        with patch(
            "app.tools.postgres_tools._exec",
            new=AsyncMock(return_value=[]),
        ) as execute:
            await postgres_tools.get_inventory_match(42, "Leite Integral", store_id=99)

        sql = execute.await_args.args[0]
        self.assertIn("WITH company_inventory AS", sql)
        self.assertIn("mottainai.inventory i", sql)
        self.assertIn("c.company_id = :empresa_id", sql)
        self.assertIn("COUNT(ci.batch_id) = 0 THEN 'SEM_INVENTARIO'", sql)
        self.assertNotIn("p.sku", sql)
        self.assertEqual(execute.await_args.kwargs["params"]["store_id"], 99)

    async def test_kpis_preserve_decimal_precision(self):
        responses = [
            [{"revenue_30d": Decimal("0.30")}],
            [{"disposal_cost_30d": Decimal("0.10")}],
            [{"active_alerts": 2}],
        ]
        with patch(
            "app.tools.postgres_tools._exec",
            new=AsyncMock(side_effect=responses),
        ) as execute:
            result = await postgres_tools.get_kpis(42)

        self.assertEqual(result["revenue_30d"], Decimal("0.30"))
        self.assertEqual(result["disposal_cost_30d"], Decimal("0.10"))
        self.assertEqual(result["active_alerts"], 2)
        revenue_sql = execute.await_args_list[0].args[0]
        self.assertIn("st.status = 'COMPLETED'", revenue_sql)
        self.assertIn("si.status = 'SOLD'", revenue_sql)

    async def test_kpis_by_store_merges_revenue_losses_and_alerts(self):
        responses = [
            [
                {"store_id": 1, "store_name": "Centro", "revenue": Decimal("100.00"), "transactions": 5},
                {"store_id": 2, "store_name": "Norte", "revenue": Decimal("50.00"), "transactions": 2},
            ],
            [{"store_id": 1, "disposal_cost": Decimal("10.00")}],
            [{"store_id": 1, "active_alerts": 3}, {"store_id": 2, "active_alerts": 0}],
        ]
        with patch(
            "app.tools.postgres_tools._exec",
            new=AsyncMock(side_effect=responses),
        ) as execute:
            result = await postgres_tools.get_kpis_by_store(42, days_back=30)

        self.assertEqual(
            result,
            [
                {
                    "store_id": 1, "store_name": "Centro", "revenue": Decimal("100.00"),
                    "transactions": 5, "disposal_cost": Decimal("10.00"), "active_alerts": 3,
                },
                {
                    "store_id": 2, "store_name": "Norte", "revenue": Decimal("50.00"),
                    "transactions": 2, "disposal_cost": Decimal("0"), "active_alerts": 0,
                },
            ],
        )
        revenue_sql = execute.await_args_list[0].args[0]
        self.assertIn("GROUP BY rs.store_id, rs.name", revenue_sql)
        self.assertEqual(execute.await_args_list[0].kwargs["params"], {"days_back": 30})

    async def test_kpis_by_store_defaults_missing_store_to_zero(self):
        responses = [
            [{"store_id": 1, "store_name": "Centro", "revenue": Decimal("0"), "transactions": 0}],
            [],
            [],
        ]
        with patch(
            "app.tools.postgres_tools._exec",
            new=AsyncMock(side_effect=responses),
        ):
            result = await postgres_tools.get_kpis_by_store(42)

        self.assertEqual(result[0]["disposal_cost"], Decimal("0"))
        self.assertEqual(result[0]["active_alerts"], 0)


class ShelfInventoryContracts(unittest.IsolatedAsyncioTestCase):
    async def test_crosscheck_uses_critical_inventory_for_missing_products(self):
        inventory = [
            {
                "product_name": "Leite Integral",
                "quantity": Decimal("0"),
                "min_quantity": Decimal("10"),
                "stock_status": "RUPTURA",
                "store_name": "Centro",
            },
            {
                "product_name": "Iogurte Natural",
                "quantity": Decimal("2"),
                "min_quantity": Decimal("10"),
                "stock_status": "ABAIXO_MINIMO",
                "store_name": "Centro",
            },
        ]
        with (
            patch(
                "app.tools.postgres_tools.get_inventory_match",
                new=AsyncMock(return_value={"id": 1, "name": "Leite Integral"}),
            ),
            patch(
                "app.tools.postgres_tools.get_inventory_status",
                new=AsyncMock(return_value=inventory),
            ) as status,
            patch(
                "app.tools.postgres_tools.get_stock_alerts",
                new=AsyncMock(return_value=[{"type": "EXPIRATION"}]),
            ) as alerts,
        ):
            result = await postgres_tools.get_shelf_inventory_crosscheck(
                empresa_id=42,
                store_id=7,
                detected_products=["Leite Integral", "leite integral"],
            )

        self.assertEqual(result["encontrados"], [{"id": 1, "name": "Leite Integral"}])
        self.assertEqual(result["ausentes_esperados"], [inventory[1]])
        self.assertEqual(result["alertas_ativos"], [{"type": "EXPIRATION"}])
        status.assert_awaited_once_with(42, 7)
        alerts.assert_awaited_once_with(42, limit=10, store_id=7)

    async def test_vision_wrapper_keeps_authenticated_company_and_store(self):
        with patch(
            "app.tools.vision_tools.get_shelf_inventory_crosscheck",
            new=AsyncMock(return_value={"encontrados": []}),
        ) as crosscheck:
            result = await crosscheck_with_inventory(
                empresa_id=42,
                store_id=7,
                detected_products=["Leite Integral"],
            )

        self.assertEqual(result, {"encontrados": []})
        crosscheck.assert_awaited_once_with(42, 7, ["Leite Integral"])

    async def test_empty_shelf_still_queries_operational_context(self):
        with patch(
            "app.tools.vision_tools.get_shelf_inventory_crosscheck",
            new=AsyncMock(return_value={"ausentes_esperados": [{"product_name": "Leite"}]}),
        ) as crosscheck:
            result = await crosscheck_with_inventory(empresa_id=42, store_id=7, detected_products=[])

        self.assertEqual(result, {"ausentes_esperados": [{"product_name": "Leite"}]})
        crosscheck.assert_awaited_once_with(42, 7, [])


if __name__ == "__main__":
    unittest.main()
