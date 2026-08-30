"""Predictive Engine wiring: pushes a webhook for any CRITICAL alert it sees."""
import unittest
from unittest.mock import AsyncMock, patch


class _StubResponse:
    def __init__(self, content: str):
        self.content = content
        self.usage_metadata = {"input_tokens": 1, "output_tokens": 1}


class _StubLlm:
    async def ainvoke(self, messages):
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


if __name__ == "__main__":
    unittest.main()
