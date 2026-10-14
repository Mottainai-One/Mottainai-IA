"""Official bootstrap, current authorization and transaction boundaries."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.database.identity import OperationalIdentity, current_identity, initialize_pg_context


def result(value):
    return SimpleNamespace(scalar_one=lambda: value,
                           mappings=lambda: SimpleNamespace(first=lambda: value))


class OperationalIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.token = current_identity.set(OperationalIdentity(101, 7, "ESTOQUISTA", "EMAIL_SHA256_" + "a" * 64))
        self.addCleanup(current_identity.reset, self.token)
        self.actor = {"user_id": 101, "company_id": 7, "store_id": 3,
                      "employee_id": 29, "database_role": "OPERATOR"}

    async def test_uses_official_bootstrap_and_preserves_distinct_employee_id(self):
        session = SimpleNamespace(execute=AsyncMock(side_effect=[result(True), result(self.actor)]))
        actor = await initialize_pg_context(session, 7)
        self.assertEqual(actor["employee_id"], 29)
        self.assertIn("fn_bootstrap_staff_context", str(session.execute.await_args_list[0].args[0]))
        self.assertTrue(all("set_config" not in str(call.args[0]) for call in session.execute.await_args_list))

    async def test_missing_identity_and_cross_company_fail_before_database_access(self):
        session = SimpleNamespace(execute=AsyncMock())
        with self.assertRaises(PermissionError):
            await initialize_pg_context(session, 8)
        current_identity.set(None)
        with self.assertRaises(PermissionError):
            await initialize_pg_context(session, 7)
        session.execute.assert_not_awaited()

    async def test_inactive_or_unlinked_identity_fails_closed(self):
        session = SimpleNamespace(execute=AsyncMock(return_value=result(False)))
        with self.assertRaises(PermissionError):
            await initialize_pg_context(session, 7)
        self.assertEqual(session.execute.await_count, 1)

    async def test_changed_user_company_or_role_fails_closed(self):
        for changes in ({"user_id": 102}, {"company_id": 8}, {"database_role": "MANAGER"}):
            session = SimpleNamespace(execute=AsyncMock(side_effect=[result(True), result({**self.actor, **changes})]))
            with self.assertRaises(PermissionError):
                await initialize_pg_context(session, 7)
