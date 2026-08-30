"""Contracts for the Employee Agent's write tools (discard_batch, receive_inventory)."""
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.tools import postgres_tools


class FakeMappingResult:
    def __init__(self, row: dict | None):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class FakeWriteSession:
    def __init__(self, results: list):
        self._results = iter(results)
        self.statements: list[str] = []

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        return next(self._results)


class FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class DiscardBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_writes_disposal_audit_rows_and_decrements_inventory(self):
        session = FakeWriteSession([
            None,  # set_config
            FakeMappingResult({"inventory_id": 5}),  # _find_inventory_id
            FakeMappingResult({"disposal_id": 900}),  # INSERT disposal RETURNING
            None,  # INSERT disposal_item
            FakeMappingResult({"new_balance": Decimal("12.000")}),  # fn_atomic_update_inventory
        ])
        with patch("app.tools.postgres_tools.get_pg_session", return_value=FakeSessionContext(session)):
            result = await postgres_tools.discard_batch(
                empresa_id=42, store_id=1, batch_id=77, employee_id=9,
                quantity=Decimal("3"), reason="vencido",
            )

        self.assertEqual(result, {
            "disposal_id": 900, "batch_id": 77, "store_id": 1,
            "disposed_quantity": Decimal("3"), "new_inventory_balance": Decimal("12.000"),
        })
        self.assertIn("INSERT INTO mottainai.disposal ", session.statements[2])
        self.assertIn("INSERT INTO mottainai.disposal_item", session.statements[3])
        self.assertIn("'DISPOSAL'", session.statements[4])

    async def test_raises_when_no_matching_inventory_for_tenant(self):
        session = FakeWriteSession([None, FakeMappingResult(None)])
        with patch("app.tools.postgres_tools.get_pg_session", return_value=FakeSessionContext(session)):
            with self.assertRaises(ValueError):
                await postgres_tools.discard_batch(
                    empresa_id=42, store_id=1, batch_id=77, employee_id=9,
                    quantity=Decimal("1"), reason="vencido",
                )

    async def test_rejects_non_positive_quantity(self):
        with self.assertRaises(ValueError):
            await postgres_tools.discard_batch(
                empresa_id=42, store_id=1, batch_id=77, employee_id=9,
                quantity=Decimal("0"), reason="vencido",
            )

    async def test_rejects_blank_reason(self):
        with self.assertRaises(ValueError):
            await postgres_tools.discard_batch(
                empresa_id=42, store_id=1, batch_id=77, employee_id=9,
                quantity=Decimal("1"), reason="   ",
            )

    async def test_rejects_invalid_store_id(self):
        with self.assertRaises(ValueError):
            await postgres_tools.discard_batch(
                empresa_id=42, store_id=0, batch_id=77, employee_id=9,
                quantity=Decimal("1"), reason="vencido",
            )


class ReceiveInventoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_increments_inventory_with_in_movement(self):
        session = FakeWriteSession([
            None,  # set_config
            FakeMappingResult({"inventory_id": 5}),  # _find_inventory_id
            FakeMappingResult({"new_balance": Decimal("40.000")}),  # fn_atomic_update_inventory
        ])
        with patch("app.tools.postgres_tools.get_pg_session", return_value=FakeSessionContext(session)):
            result = await postgres_tools.receive_inventory(
                empresa_id=42, store_id=1, batch_id=77, employee_id=9, quantity=Decimal("20"),
            )

        self.assertEqual(result, {
            "batch_id": 77, "store_id": 1,
            "received_quantity": Decimal("20"), "new_inventory_balance": Decimal("40.000"),
        })
        self.assertIn("'IN'", session.statements[2])

    async def test_raises_when_no_matching_inventory_for_tenant(self):
        session = FakeWriteSession([None, FakeMappingResult(None)])
        with patch("app.tools.postgres_tools.get_pg_session", return_value=FakeSessionContext(session)):
            with self.assertRaises(ValueError):
                await postgres_tools.receive_inventory(
                    empresa_id=42, store_id=1, batch_id=77, employee_id=9, quantity=Decimal("1"),
                )

    async def test_rejects_non_positive_quantity(self):
        with self.assertRaises(ValueError):
            await postgres_tools.receive_inventory(
                empresa_id=42, store_id=1, batch_id=77, employee_id=9, quantity=Decimal("-1"),
            )


if __name__ == "__main__":
    unittest.main()
