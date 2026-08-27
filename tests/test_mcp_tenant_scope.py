from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.integrations.mcp_a2a import dispatch_a2a, dispatch_mcp
from app.tools.mcp_tools import mcp_expose_tool


class McpTenantScopeContracts(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_rejects_a_different_company_before_tool_execution(self):
        settings = SimpleNamespace(mcp_shared_token="mcp-token", mcp_empresa_id=7)
        with (
            patch("app.integrations.mcp_a2a.get_settings", return_value=settings),
            patch("app.integrations.mcp_a2a.mcp_expose_tool", new=AsyncMock()) as expose,
        ):
            result = await dispatch_mcp(
                {
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "get_active_alerts", "arguments": {"empresa_id": 999}},
                },
                "Bearer mcp-token",
            )

        self.assertEqual(result["error"]["code"], -32602)
        expose.assert_not_awaited()

    async def test_mcp_overrides_missing_company_with_token_scope(self):
        settings = SimpleNamespace(mcp_shared_token="mcp-token", mcp_empresa_id=7)
        with (
            patch("app.integrations.mcp_a2a.get_settings", return_value=settings),
            patch(
                "app.integrations.mcp_a2a.mcp_expose_tool",
                new=AsyncMock(return_value={"result": []}),
            ) as expose,
        ):
            result = await dispatch_mcp(
                {"id": 1, "method": "tools/call", "params": {"name": "get_active_alerts", "arguments": {}}},
                "Bearer mcp-token",
            )

        self.assertFalse(result["result"]["isError"])
        expose.assert_awaited_once_with("get_active_alerts", {"empresa_id": 7})

    async def test_a2a_rejects_a_different_company_before_tool_execution(self):
        settings = SimpleNamespace(a2a_shared_token="a2a-token", a2a_empresa_id=7)
        with (
            patch("app.integrations.mcp_a2a.get_settings", return_value=settings),
            patch("app.integrations.mcp_a2a.mcp_expose_tool", new=AsyncMock()) as expose,
        ):
            result = await dispatch_a2a(
                {"action": "get_company_kpis", "payload": {"empresa_id": 999}},
                "Bearer a2a-token",
            )

        self.assertEqual(result["error"]["code"], "tenant_not_authorized")
        expose.assert_not_awaited()

    async def test_a2a_uses_company_bound_to_token(self):
        settings = SimpleNamespace(a2a_shared_token="a2a-token", a2a_empresa_id=7)
        with (
            patch("app.integrations.mcp_a2a.get_settings", return_value=settings),
            patch(
                "app.integrations.mcp_a2a.mcp_expose_tool",
                new=AsyncMock(return_value={"result": {"active_alerts": 1}}),
            ) as expose,
        ):
            result = await dispatch_a2a(
                {"action": "get_company_kpis", "payload": {}},
                "Bearer a2a-token",
            )

        self.assertEqual(result["status"], "completed")
        expose.assert_awaited_once_with("get_company_kpis", {"empresa_id": 7})

    async def test_direct_mcp_tool_rejects_missing_company(self):
        result = await mcp_expose_tool("get_active_alerts", {})

        self.assertEqual(result["error"], "Empresa autorizada obrigatória.")


if __name__ == "__main__":
    unittest.main()
