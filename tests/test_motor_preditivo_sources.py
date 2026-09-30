"""Regression test: node_motor_preditivo's "sources" entries must use a
"type" value the messages collection's Mongo $jsonSchema validator accepts
(rag, sql, api, manual, url, other) — this is not exercised by tests that
mock the Mongo layer, which is exactly how an invalid value ("calc") shipped
to main and 500'd every real chat request that reached this node (caught by
live-testing against the real database, not by the unit suite)."""
import unittest
from unittest.mock import AsyncMock, patch

from app.database.mongo_schema import SOURCE_TYPES as ALLOWED_SOURCE_TYPES


class _StubResponse:
    def __init__(self, content: str):
        self.content = content
        self.usage_metadata = {"input_tokens": 1, "output_tokens": 1}


class _StubLlm:
    async def ainvoke(self, messages):
        return _StubResponse("análise gerada")


class MotorPreditivoSourceTypeTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_source_type_is_accepted_by_the_mongo_schema(self):
        from app.agents import motor_preditivo

        with (
            patch("app.agents.motor_preditivo.get_expiring_batches", new=AsyncMock(return_value=[])),
            patch("app.agents.motor_preditivo.get_sales_summary", new=AsyncMock(return_value=[])),
            patch("app.agents.motor_preditivo.get_stock_alerts", new=AsyncMock(return_value=[])),
            patch("app.agents.motor_preditivo.mcp_call_weather_agent", new=AsyncMock(side_effect=RuntimeError("sem clima"))),
            patch("app.agents.motor_preditivo.get_llm", return_value=_StubLlm()),
        ):
            result = await motor_preditivo.node_motor_preditivo({"empresa_id": 42})

        offending = [s for s in result["sources"] if s["type"] not in ALLOWED_SOURCE_TYPES]
        self.assertEqual(offending, [], f"source type(s) not accepted by the Mongo schema: {offending}")


if __name__ == "__main__":
    unittest.main()
