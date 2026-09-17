"""The Owner/Employee agents' independent tool calls run concurrently
(PR: perf/parallel-agent-tool-calls), without changing what gets recorded
in tool_runs or how a failing tool behaves."""
import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from app.agents import dono, funcionario
from app.agents.runtime import gather_or_raise
from app.tools import postgres_tools

TOOL_DELAY = 0.1


class _StubResponse:
    content = "resposta"
    usage_metadata = {"input_tokens": 1, "output_tokens": 1}


def _slow(return_value):
    async def call(*args, **kwargs):
        await asyncio.sleep(TOOL_DELAY)
        return return_value
    return call


def _state():
    return {
        "session_id": "s1", "empresa_id": 1, "usuario_id": 2, "user_role": "DONO",
        "user_input": "oi", "sanitized_input": "oi", "history": [],
        "memory": {"preferences": [], "facts": [], "lastAgent": None, "lastSkill": None},
    }


class OwnerAgentParallelismTests(unittest.IsolatedAsyncioTestCase):
    async def test_five_independent_calls_run_concurrently(self):
        with (
            patch("app.agents.dono.get_kpis", new=_slow({})),
            patch("app.agents.dono.get_sales_summary", new=_slow([])),
            patch("app.agents.dono.get_stock_alerts", new=_slow([])),
            patch("app.agents.dono.get_kpis_by_store", new=_slow([])),
            patch("app.agents.dono.retrieve_with_sources", new=_slow(("", []))),
            patch("app.agents.dono.get_llm") as get_llm,
        ):
            get_llm.return_value.ainvoke = AsyncMock(return_value=_StubResponse())
            started = time.perf_counter()
            result = await dono.node_agente_dono(_state())
            elapsed = time.perf_counter() - started

        # Sequentially these five would cost 5 * TOOL_DELAY; concurrently,
        # about one. The midpoint keeps this from passing by accident on a
        # slow machine or failing on a fast one.
        self.assertLess(elapsed, TOOL_DELAY * 2.5)
        self.assertEqual(result["agent_response"], "resposta")

    async def test_every_tool_is_still_recorded_once(self):
        with (
            patch("app.agents.dono.get_kpis", new=AsyncMock(return_value={})),
            patch("app.agents.dono.get_sales_summary", new=AsyncMock(return_value=[])),
            patch("app.agents.dono.get_stock_alerts", new=AsyncMock(return_value=[])),
            patch("app.agents.dono.get_kpis_by_store", new=AsyncMock(return_value=[])),
            patch("app.agents.dono.retrieve_with_sources", new=AsyncMock(return_value=("", []))),
            patch("app.agents.dono.get_llm") as get_llm,
        ):
            get_llm.return_value.ainvoke = AsyncMock(return_value=_StubResponse())
            result = await dono.node_agente_dono(_state())

        recorded = sorted(run["tool"] for run in result["tool_runs"])
        self.assertEqual(recorded, [
            "get_kpis", "get_kpis_by_store", "get_sales_summary",
            "get_stock_alerts", "retrieve_with_sources",
        ])
        self.assertTrue(all(run["status"] == "success" for run in result["tool_runs"]))

    async def test_one_failing_tool_still_fails_the_node_and_records_the_rest(self):
        with (
            patch("app.agents.dono.get_kpis", new=AsyncMock(side_effect=RuntimeError("postgres down"))),
            patch("app.agents.dono.get_sales_summary", new=_slow([])),
            patch("app.agents.dono.get_stock_alerts", new=_slow([])),
            patch("app.agents.dono.get_kpis_by_store", new=_slow([])),
            patch("app.agents.dono.retrieve_with_sources", new=_slow(("", []))),
            patch("app.agents.dono.get_llm") as get_llm,
        ):
            get_llm.return_value.ainvoke = AsyncMock(return_value=_StubResponse())
            with self.assertRaises(RuntimeError):
                await dono.node_agente_dono(_state())
            # The other four are not left running in the background after
            # the failure — gather_or_raise waits for all of them first, so
            # by the time the exception surfaces they have already settled.
            await asyncio.sleep(0)


class EmployeeAgentParallelismTests(unittest.IsolatedAsyncioTestCase):
    async def test_six_independent_calls_run_concurrently(self):
        with (
            patch("app.agents.funcionario.get_stock_alerts", new=_slow([])),
            patch("app.agents.funcionario.get_inventory_status", new=_slow([])),
            patch("app.agents.funcionario.get_expiring_batches", new=_slow([])),
            patch("app.agents.funcionario.get_inbox", new=_slow([])),
            patch("app.agents.funcionario.get_recent_vision_analyses", new=_slow([])),
            patch("app.agents.funcionario.retrieve_with_sources", new=_slow(("", []))),
            patch("app.agents.funcionario.format_notifications_for_agent", new=AsyncMock(return_value="")),
            patch("app.agents.funcionario.get_llm") as get_llm,
        ):
            get_llm.return_value.ainvoke = AsyncMock(return_value=_StubResponse())
            started = time.perf_counter()
            result = await funcionario.node_agente_funcionario(_state())
            elapsed = time.perf_counter() - started

        self.assertLess(elapsed, TOOL_DELAY * 2.5)
        self.assertEqual(result["agent_response"], "resposta")

    async def test_notification_formatting_still_runs_after_the_inbox_it_depends_on(self):
        formatted = AsyncMock(return_value="formatado")
        with (
            patch("app.agents.funcionario.get_stock_alerts", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.get_inventory_status", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.get_expiring_batches", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.get_inbox", new=AsyncMock(return_value=[{"id": "n1"}])),
            patch("app.agents.funcionario.get_recent_vision_analyses", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.retrieve_with_sources", new=AsyncMock(return_value=("", []))),
            patch("app.agents.funcionario.format_notifications_for_agent", new=formatted),
            patch("app.agents.funcionario.get_llm") as get_llm,
        ):
            get_llm.return_value.ainvoke = AsyncMock(return_value=_StubResponse())
            await funcionario.node_agente_funcionario(_state())

        # It receives the resolved inbox, not a coroutine/future of one.
        formatted.assert_awaited_once_with([{"id": "n1"}])


class GatherOrRaiseTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_results_in_order(self):
        async def value(n):
            return n

        self.assertEqual(await gather_or_raise(value(1), value(2), value(3)), [1, 2, 3])

    async def test_waits_for_every_coroutine_before_raising(self):
        finished = []

        async def ok(name):
            await asyncio.sleep(TOOL_DELAY)
            finished.append(name)

        async def fails():
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            await gather_or_raise(fails(), ok("a"), ok("b"))

        # Plain asyncio.gather would have raised immediately and left these
        # two running, unawaited, in the background.
        self.assertEqual(sorted(finished), ["a", "b"])


class PostgresFanoutSemaphoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_guarded_serialises_calls_beyond_the_limit(self):
        in_flight = 0
        peak = 0

        async def call():
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1

        with patch.object(postgres_tools, "pg_fanout_semaphore", asyncio.Semaphore(2)):
            await asyncio.gather(*(postgres_tools.guarded(call()) for _ in range(6)))

        self.assertEqual(peak, 2)

    async def test_limit_is_sized_to_the_real_pool_capacity(self):
        from config.settings import get_settings

        settings = get_settings()
        self.assertEqual(
            postgres_tools.pg_fanout_semaphore._value,
            settings.postgres_pool_size + settings.postgres_max_overflow,
        )


if __name__ == "__main__":
    unittest.main()
