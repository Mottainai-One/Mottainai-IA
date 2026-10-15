"""Regressions observed against the official dual-database contract."""
import unittest
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.cache.idempotency import InvalidIdempotencyKey, scoped_write_key
from app.database.identity import OperationalIdentity, current_identity
from app.integrations.mcp_a2a import dispatch_mcp
from app.rag.external_source import interpret_weather_for_demand
from app.security.auth import AuthContext, require_operational_roles
from tests.prompt_support import read_json_block


class WriteAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_replay_still_requires_active_database_account(self):
        @asynccontextmanager
        async def session():
            yield object()

        with (
            patch("app.database.postgres.get_pg_session", session),
            patch("app.database.identity.initialize_pg_context", AsyncMock(side_effect=PermissionError("inactive"))),
        ):
            with self.assertRaises(PermissionError):
                await require_operational_roles("ESTOQUISTA")(
                    AuthContext(usuario_id=1003, empresa_id=1, role="ESTOQUISTA"))

    def test_retry_namespace_separates_operations_and_users(self):
        first = scoped_write_key(1, "receive_inventory", "retry")
        self.assertEqual(first, scoped_write_key(1, "receive_inventory", "retry"))
        self.assertNotEqual(first, scoped_write_key(1, "discard_batch", "retry"))
        self.assertNotEqual(first, scoped_write_key(2, "receive_inventory", "retry"))
        with self.assertRaises(InvalidIdempotencyKey):
            scoped_write_key(1, "receive_inventory", "bad:key")


class IntegrationIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_operational_mapping_cannot_call_business_tool(self):
        settings = SimpleNamespace(mcp_shared_token="m"*32, mcp_empresa_id=1)
        with (patch("app.integrations.mcp_a2a.get_settings", return_value=settings),
              patch("app.integrations.mcp_a2a.mcp_expose_tool", AsyncMock()) as tool):
            result = await dispatch_mcp({"id":1,"method":"tools/call", "params":{
                "name":"get_company_kpis", "arguments":{}}}, "Bearer "+"m"*32)
        self.assertIn("error", result)
        tool.assert_not_awaited()

    async def test_tool_gets_server_identity_and_context_is_restored_on_denial(self):
        settings = SimpleNamespace(mcp_shared_token="m"*32, mcp_empresa_id=1,
            mcp_user_id=1003, mcp_role="ESTOQUISTA", mcp_operational_identity="EMAIL_SHA256_"+"6"*64)
        seen=[]
        async def tool(*args):
            seen.append(current_identity.get())
            raise PermissionError("revoked")
        prior=OperationalIdentity(7,7,"DONO","prior")
        token=current_identity.set(prior)
        try:
            with (patch("app.integrations.mcp_a2a.get_settings", return_value=settings),
                  patch("app.integrations.mcp_a2a.mcp_expose_tool", tool)):
                result = await dispatch_mcp({"id":1,"method":"tools/call", "params":{
                    "name":"get_company_kpis", "arguments":{}}}, "Bearer "+"m"*32)
            self.assertIn("error", result)
            self.assertEqual(seen[0].user_id, 1003)
            self.assertIs(current_identity.get(), prior)
        finally:
            current_identity.reset(token)


class ForecastPublicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_publication_includes_sales_on_last_published_day(self):
        from app.agents import motor_preditivo
        published=date(2020,1,1)
        sales=[{"product_id":2,"product_name":"Milk","snapshot_date":published}]
        rows=[{"product_id":2,"sale_date":published,"quantity_sold":28}]
        llm=SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content="result", usage_metadata={})))
        with (
            patch.object(motor_preditivo,"get_expiring_batches",AsyncMock(return_value=[])),
            patch.object(motor_preditivo,"get_sales_summary",AsyncMock(return_value=sales)),
            patch.object(motor_preditivo,"get_daily_sales_series",AsyncMock(return_value=rows)),
            patch.object(motor_preditivo,"get_stock_alerts",AsyncMock(return_value=[])),
            patch.object(motor_preditivo,"notify_new_critical_alerts",AsyncMock()),
            patch.object(motor_preditivo,"mcp_call_weather_agent",AsyncMock(side_effect=RuntimeError("offline"))),
            patch.object(motor_preditivo,"get_llm",return_value=llm),
        ):
            result=await motor_preditivo.node_motor_preditivo({"empresa_id":1})
        forecast=read_json_block(result["grounding_context"],"apenas explique):")[0]
        self.assertIn("Dados publicados em 01/01/2020", result["agent_response"])
        self.assertEqual(forecast["avg_daily_demand"],1)
        self.assertGreater(forecast["forecast_next_7_days"],0)

    def test_weather_does_not_invent_sales_impact(self):
        context=interpret_weather_for_demand([{"has_rain":True,"is_hot":False}]*7)
        self.assertNotIn("20%",context)
        self.assertIn("não há dados",context)
        self.assertNotIn("AUMENTAR",interpret_weather_for_demand([]))
