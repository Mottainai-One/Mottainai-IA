"""Predictive Engine node behavior: alert notifications and store scoping."""
import unittest
from unittest.mock import AsyncMock, patch


class _StubResponse:
    def __init__(self, content: str):
        self.content = content
        self.usage_metadata = {"input_tokens": 1, "output_tokens": 1}


class _StubLlm:
    async def ainvoke(self, messages):
        self.last_messages = messages
        return _StubResponse("análise gerada")


class MotorPreditivoNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_forwards_fetched_alerts_to_the_notifier(self):
        from app.agents import motor_preditivo

        alerts = [{"id": "1", "priority": "CRITICAL"}]
        notify = AsyncMock(return_value=1)

        with (
            patch("app.agents.motor_preditivo.get_expiring_batches", new=AsyncMock(return_value=[])),
            patch("app.agents.motor_preditivo.get_sales_summary", new=AsyncMock(return_value=[])),
            patch("app.agents.motor_preditivo.get_stock_alerts", new=AsyncMock(return_value=alerts)),
            patch("app.agents.motor_preditivo.notify_new_critical_alerts", new=notify),
            patch("app.agents.motor_preditivo.mcp_call_weather_agent", new=AsyncMock(side_effect=RuntimeError("sem clima"))),
            patch("app.agents.motor_preditivo.get_llm", return_value=_StubLlm()),
        ):
            await motor_preditivo.node_motor_preditivo({"empresa_id": 42})

        notify.assert_awaited_once_with(42, alerts)

    async def test_still_calls_the_notifier_with_no_active_alerts(self):
        from app.agents import motor_preditivo

        notify = AsyncMock(return_value=0)

        with (
            patch("app.agents.motor_preditivo.get_expiring_batches", new=AsyncMock(return_value=[])),
            patch("app.agents.motor_preditivo.get_sales_summary", new=AsyncMock(return_value=[])),
            patch("app.agents.motor_preditivo.get_stock_alerts", new=AsyncMock(return_value=[])),
            patch("app.agents.motor_preditivo.notify_new_critical_alerts", new=notify),
            patch("app.agents.motor_preditivo.mcp_call_weather_agent", new=AsyncMock(side_effect=RuntimeError("sem clima"))),
            patch("app.agents.motor_preditivo.get_llm", return_value=_StubLlm()),
        ):
            result = await motor_preditivo.node_motor_preditivo({"empresa_id": 42})

        notify.assert_awaited_once_with(42, [])
        self.assertEqual(result["agent_response"], "análise gerada")


class MotorPreditivoStoreScopeTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, store_id):
        from app.agents import motor_preditivo

        llm = _StubLlm()
        state = {"empresa_id": 42, "store_id": store_id}

        with (
            patch("app.agents.motor_preditivo.get_expiring_batches", new=AsyncMock(return_value=[])) as expiring,
            patch("app.agents.motor_preditivo.get_sales_summary", new=AsyncMock(return_value=[])) as sales,
            patch("app.agents.motor_preditivo.get_stock_alerts", new=AsyncMock(return_value=[])) as alerts,
            patch("app.agents.motor_preditivo.mcp_call_weather_agent", new=AsyncMock(side_effect=RuntimeError("sem clima"))),
            patch("app.agents.motor_preditivo.get_llm", return_value=llm),
        ):
            result = await motor_preditivo.node_motor_preditivo(state)

        return result, llm, expiring, sales, alerts

    async def test_scopes_all_three_queries_to_the_requested_store(self):
        result, llm, expiring, sales, alerts = await self._run(store_id=7)

        expiring.assert_awaited_once_with(42, days_ahead=14, store_id=7)
        sales.assert_awaited_once_with(42, days_back=60, store_id=7)
        alerts.assert_awaited_once_with(42, store_id=7)
        prompt_text = str(llm.last_messages[0].content)
        self.assertIn("Loja específica (store_id=7)", prompt_text)
        self.assertEqual(result["agent_response"], "análise gerada")

    async def test_defaults_to_company_wide_scope_when_no_store_given(self):
        result, llm, expiring, sales, alerts = await self._run(store_id=None)

        expiring.assert_awaited_once_with(42, days_ahead=14, store_id=None)
        sales.assert_awaited_once_with(42, days_back=60, store_id=None)
        alerts.assert_awaited_once_with(42, store_id=None)
        prompt_text = str(llm.last_messages[0].content)
        self.assertIn("Todas as lojas da empresa", prompt_text)
        self.assertEqual(result["agent_response"], "análise gerada")


if __name__ == "__main__":
    unittest.main()
