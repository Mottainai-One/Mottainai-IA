"""Logging contract for the graph's node-instrumentation wrapper."""
import unittest
from unittest.mock import AsyncMock, patch

from app.agents.supervisor import _instrument_node


def _rendered(call) -> str:
    fmt, *args = call.args
    return fmt % tuple(args)


class InstrumentNodeLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_logs_start_and_ok_with_empresa_id_and_latency_on_success(self):
        node = AsyncMock(return_value={"agent_response": "ok"})
        wrapped = _instrument_node("cliente", node)

        with patch("app.agents.supervisor.logger") as logger:
            result = await wrapped({"empresa_id": 42})

        self.assertIsInstance(result["node_latencies_ms"]["cliente"], float)
        start_call, ok_call = logger.info.call_args_list
        self.assertIn("status=started", _rendered(start_call))
        self.assertIn("empresa_id=42", _rendered(start_call))
        self.assertIn("status=ok", _rendered(ok_call))
        self.assertIn("latency_ms=", _rendered(ok_call))
        logger.exception.assert_not_called()

    async def test_logs_failure_and_still_reraises_the_original_exception(self):
        node = AsyncMock(side_effect=RuntimeError("boom"))
        wrapped = _instrument_node("dono", node)

        with patch("app.agents.supervisor.logger") as logger:
            with self.assertRaises(RuntimeError):
                await wrapped({"empresa_id": 7})

        logger.exception.assert_called_once()
        self.assertIn("status=failed", _rendered(logger.exception.call_args))

    async def test_does_not_log_user_input_or_agent_response_content(self):
        node = AsyncMock(return_value={"agent_response": "SENHA-SECRETA-DO-USUARIO"})
        wrapped = _instrument_node("faq", node)

        with patch("app.agents.supervisor.logger") as logger:
            await wrapped({"empresa_id": 1, "sanitized_input": "SENHA-SECRETA-DO-USUARIO"})

        rendered = [_rendered(call) for call in logger.info.call_args_list]
        self.assertTrue(all("SENHA-SECRETA-DO-USUARIO" not in line for line in rendered))


if __name__ == "__main__":
    unittest.main()
