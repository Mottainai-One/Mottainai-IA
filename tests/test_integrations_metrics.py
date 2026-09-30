"""Contracts for MCP/A2A and configurable Groq cost accounting."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.agents.governanca import run_relatorio_conformidade
from app.integrations.mcp_a2a import dispatch_a2a, dispatch_mcp
from app.observability.metrics import record_execution_metrics


class IntegrationContractsTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_rejects_missing_token(self):
        settings = SimpleNamespace(
            mcp_shared_token="mcp-token", mcp_empresa_id=1,
            a2a_shared_token="a2a-token", a2a_empresa_id=1,
        )
        with patch("app.integrations.mcp_a2a.get_settings", return_value=settings):
            result = await dispatch_mcp({"id": 1, "method": "tools/list"}, None)
        self.assertEqual(result["error"]["code"], -32001)

    async def test_a2a_allows_only_explicit_read_actions(self):
        settings = SimpleNamespace(
            mcp_shared_token="mcp-token", mcp_empresa_id=1,
            a2a_shared_token="a2a-token", a2a_empresa_id=1,
        )
        with patch("app.integrations.mcp_a2a.get_settings", return_value=settings), patch(
            "app.integrations.mcp_a2a.mcp_expose_tool", new=AsyncMock(return_value={"result": [{"id": 1}]})
        ):
            result = await dispatch_a2a(
                {"action": "get_active_alerts", "payload": {"empresa_id": 1}}, "Bearer a2a-token"
            )
        self.assertEqual(result["status"], "completed")

    async def test_a2a_rejects_actions_outside_allowlist(self):
        settings = SimpleNamespace(
            mcp_shared_token="mcp-token", mcp_empresa_id=1,
            a2a_shared_token="a2a-token", a2a_empresa_id=1,
        )
        with patch("app.integrations.mcp_a2a.get_settings", return_value=settings):
            result = await dispatch_a2a({"action": "delete_inventory"}, "Bearer a2a-token")
        self.assertEqual(result["error"]["code"], "unsupported_action")


class MetricsContractsTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_zero_cost_for_groq_free_tier(self):
        inserted = []
        class Metrics:
            async def insert_one(self, document): inserted.append(document)
        database = SimpleNamespace(metrics=Metrics())
        settings = SimpleNamespace(llm_input_cost_per_million_usd=0.0, llm_output_cost_per_million_usd=0.0)
        with patch("app.observability.metrics.get_mongo_db", return_value=database), patch(
            "app.observability.metrics.get_settings", return_value=settings
        ):
            await record_execution_metrics(
                "s1", None, "cliente", None, "groq/model", 1000, 500, 0.2,
                empresa_id=42,
            )
        self.assertEqual(inserted[0]["estimatedCost"], 0.0)
        self.assertEqual(inserted[0]["costReference"]["inputPerMillionUsd"], 0.0)
        self.assertEqual(inserted[0]["empresaId"], 42)


class GovernanceComplianceContractsTests(unittest.IsolatedAsyncioTestCase):
    async def test_compliance_report_uses_configured_cost_rates(self):
        class Collection:
            def __init__(self, count=0):
                self.count = count

            async def count_documents(self, query):
                return self.count

            def find(self, query, projection=None):
                class Cursor:
                    def __aiter__(self):
                        async def iterate():
                            return
                            yield
                        return iterate()
                return Cursor()

        class Metrics:
            def find(self, query):
                return self

            async def to_list(self, length):
                return [{"inputTokens": 1_000_000, "outputTokens": 500_000}]

        database = SimpleNamespace(
            conversations=Collection(count=2),
            messages=Collection(count=4),
            feedbacks=Collection(count=1),
            metrics=Metrics(),
        )
        settings = SimpleNamespace(
            llm_input_cost_per_million_usd=1.0,
            llm_output_cost_per_million_usd=2.0,
        )

        with patch("app.agents.governanca.get_mongo_db", return_value=database), patch(
            "app.agents.governanca.get_settings", return_value=settings
        ):
            report = await run_relatorio_conformidade(42)

        self.assertEqual(report["empresa_id"], 42)
        self.assertEqual(report["tokens"], {"input": 1_000_000, "output": 500_000})
        self.assertEqual(report["custo_estimado_usd"], 2.0)
        self.assertEqual(report["metodologia_custo"], "projecao_configurada")


if __name__ == "__main__":
    unittest.main()
