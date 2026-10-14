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
    def setUp(self):
        self.bootstrap_patch = patch("app.tools.postgres_tools.initialize_pg_context", new=AsyncMock())
        self.bootstrap = self.bootstrap_patch.start()
        self.addCleanup(self.bootstrap_patch.stop)

    async def test_sets_transaction_local_company_context_before_query(self):
        session = FakeSession([FakeResult(["id"], [(7,)])])
        with patch(
            "app.tools.postgres_tools.get_pg_session",
            return_value=FakeSessionContext(session),
        ):
            rows = await postgres_tools._exec(
                "SELECT :empresa_id AS id",
                empresa_id=42,
            )

        self.assertEqual(rows, [{"id": 7}])
        self.bootstrap.assert_awaited_once_with(session, 42)
        query_sql, query_params = session.calls[0]
        self.assertIn("SELECT :empresa_id AS id", query_sql)
        self.assertEqual(query_params["empresa_id"], 42)

    async def test_rejects_invalid_company_context(self):
        with self.assertRaises(ValueError):
            await postgres_tools._exec("SELECT 1", empresa_id=0)


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
                "app.tools.postgres_tools.get_inventory_matches",
                new=AsyncMock(return_value={"leite integral": {"id": 1, "name": "Leite Integral"}}),
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
