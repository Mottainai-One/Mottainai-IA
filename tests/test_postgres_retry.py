"""Retry/backoff contract for Postgres queries (mirrors the LLM provider's)."""
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy.exc import OperationalError

from app.tools import postgres_tools


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def keys(self):
        return ["id"]

    def fetchall(self):
        return self._rows


class FakeSession:
    """
    The set_config statement always succeeds silently (it's not what
    these tests are about); `query_side_effect` drives what happens on
    each attempt at the REAL query, one item consumed per attempt.
    """

    def __init__(self, query_side_effect):
        self._query_side_effect = list(query_side_effect)
        self.query_call_count = 0

    async def execute(self, statement, params=None):
        if "set_config" in str(statement):
            return None
        self.query_call_count += 1
        outcome = self._query_side_effect.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def _op_error() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("connection reset"))


class PostgresRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_on_operational_error_and_eventually_succeeds(self):
        session = FakeSession(query_side_effect=[_op_error(), _op_error(), FakeResult([(7,)])])
        with (
            patch("app.tools.postgres_tools.get_pg_session", return_value=FakeSessionContext(session)),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            rows = await postgres_tools._exec("SELECT 1", empresa_id=1)

        self.assertEqual(rows, [{"id": 7}])
        self.assertEqual(session.query_call_count, 3)  # 2 failures + the succeeding attempt

    async def test_gives_up_after_postgres_max_retries_and_reraises(self):
        session = FakeSession(query_side_effect=[_op_error(), _op_error(), _op_error()])
        with (
            patch("app.tools.postgres_tools.get_pg_session", return_value=FakeSessionContext(session)),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            with self.assertRaises(OperationalError):
                await postgres_tools._exec("SELECT 1", empresa_id=1)

        self.assertEqual(session.query_call_count, 3)  # settings.postgres_max_retries, all exhausted

    async def test_does_not_retry_non_operational_errors(self):
        session = FakeSession(query_side_effect=[RuntimeError("not transient, a real bug")])
        with (
            patch("app.tools.postgres_tools.get_pg_session", return_value=FakeSessionContext(session)),
            patch("asyncio.sleep", new=AsyncMock()) as sleep,
        ):
            with self.assertRaises(RuntimeError):
                await postgres_tools._exec("SELECT 1", empresa_id=1)

        self.assertEqual(session.query_call_count, 1)  # exactly one attempt, no retry
        sleep.assert_not_awaited()

    async def test_invalid_empresa_id_is_rejected_without_any_query_attempt(self):
        session = FakeSession(query_side_effect=[])
        with patch("app.tools.postgres_tools.get_pg_session", return_value=FakeSessionContext(session)):
            with self.assertRaises(ValueError):
                await postgres_tools._exec("SELECT 1", empresa_id=0)

        self.assertEqual(session.query_call_count, 0)


if __name__ == "__main__":
    unittest.main()
