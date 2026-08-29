"""Operational contracts for safe health and container entrypoints."""
import io
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from google.api_core.exceptions import ServiceUnavailable
from groq import APIConnectionError
from openai import OpenAIError
from starlette.datastructures import Headers, UploadFile

from app.database.operational_schema import OPERATIONAL_SCHEMA_READY_QUERY
from app.memory.short_term import SessionOwnershipError
from app.security.auth import AuthContext
from config.settings import Settings
from interfaces.api.main import (
    _dependency_checks,
    analyze_shelf_image,
    app,
    chat,
    ChatRequest,
    health_check,
    live_check,
    readiness_check,
    trigger_motor_preditivo,
)


class SettingsContractTests(unittest.TestCase):
    def test_accepts_documented_database_aliases(self):
        settings = Settings(
            _env_file=None,
            DATABASE_URL="postgresql+asyncpg://mottainai:mottainai@localhost:5432/mottainai",
            MONGO_URL="mongodb://localhost:27017/mottainai",
        )

        self.assertEqual(settings.postgres_dsn, "postgresql+asyncpg://mottainai:mottainai@localhost:5432/mottainai")
        self.assertEqual(settings.mongo_uri, "mongodb://localhost:27017/mottainai")


class OperationalContractTests(unittest.IsolatedAsyncioTestCase):
    def test_exposes_liveness_and_readiness_routes(self):
        paths = {route.path for route in app.routes}
        self.assertTrue({"/health", "/livez", "/readyz"}.issubset(paths))

    async def test_liveness_does_not_depend_on_infrastructure(self):
        self.assertEqual(await live_check(), {"status": "alive"})

    async def test_health_sanitizes_dependency_failures(self):
        checks = {"mongodb": "unavailable", "redis": "ok", "postgres": "ok"}
        with patch("interfaces.api.main._dependency_checks", new=AsyncMock(return_value=checks)):
            result = await health_check()

        self.assertEqual(result, {"status": "degraded", "checks": checks})
        self.assertNotIn("error:", json.dumps(result))

    async def test_readiness_returns_503_when_a_dependency_is_unavailable(self):
        checks = {"mongodb": "ok", "redis": "unavailable", "postgres": "ok"}
        with patch("interfaces.api.main._dependency_checks", new=AsyncMock(return_value=checks)):
            response = await readiness_check()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.body), {"status": "unavailable", "checks": checks})

    async def test_postgres_check_requires_operational_schema(self):
        statements = []

        class Database:
            async def command(self, command):
                return {"ok": 1}

        class Redis:
            async def ping(self):
                return True

        class Result:
            def scalar(self):
                return True

        class Session:
            async def execute(self, statement):
                statements.append(str(statement))
                return Result()

        class SessionContext:
            async def __aenter__(self):
                return Session()

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        with (
            patch("interfaces.api.main.get_mongo_db", return_value=Database()),
            patch("app.database.redis_client.get_redis", return_value=Redis()),
            patch("app.database.postgres.get_pg_session", return_value=SessionContext()),
        ):
            checks = await _dependency_checks()

        self.assertEqual(checks, {"mongodb": "ok", "redis": "ok", "postgres": "ok"})
        self.assertIn("to_regclass('mottainai.inventory')", statements[0])
        self.assertIn("to_regclass('mottainai.alert')", statements[0])
        self.assertIn("to_regclass('mottainai.disposal_item')", OPERATIONAL_SCHEMA_READY_QUERY)
        self.assertIn("to_regclass('mottainai.promotion')", OPERATIONAL_SCHEMA_READY_QUERY)
        self.assertIn("to_regclass('mottainai.sale_payment')", OPERATIONAL_SCHEMA_READY_QUERY)
        self.assertIn("column_name = 'status'", OPERATIONAL_SCHEMA_READY_QUERY)
        self.assertIn("to_regprocedure('mottainai.fn_get_current_company_id()')", statements[0])


class ProtectedOperationalRoutesTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _image() -> UploadFile:
        return UploadFile(
            file=io.BytesIO(b"image-bytes"),
            filename="shelf.png",
            headers=Headers({"content-type": "image/png"}),
        )

    async def test_predictive_trigger_returns_guardrailed_response(self):
        generated = {"agent_response": "resposta bruta", "judge_score": 0.9, "sources": []}
        judged = {**generated, "agent_response": "resposta revisada"}
        guarded = {**judged, "final_response": "resposta filtrada"}
        guardrail = AsyncMock(return_value=guarded)

        with (
            patch("app.agents.motor_preditivo.node_motor_preditivo", new=AsyncMock(return_value=generated)),
            patch("app.agents.juiz.node_agente_juiz", new=AsyncMock(return_value=judged)),
            patch("interfaces.api.main.node_guardrail_saida", new=guardrail),
        ):
            result = await trigger_motor_preditivo(AuthContext(usuario_id=7, empresa_id=42, role="DONO"))

        self.assertEqual(result["analysis"], "resposta filtrada")
        guardrail.assert_awaited_once_with(judged)

    async def test_predictive_trigger_scopes_state_to_the_requested_store(self):
        generated = {"agent_response": "resposta bruta", "judge_score": 0.9, "sources": []}
        node = AsyncMock(return_value=generated)

        with (
            patch("app.agents.motor_preditivo.node_motor_preditivo", new=node),
            patch("app.agents.juiz.node_agente_juiz", new=AsyncMock(return_value=generated)),
            patch("interfaces.api.main.node_guardrail_saida", new=AsyncMock(return_value={**generated, "final_response": "ok"})),
        ):
            await trigger_motor_preditivo(
                AuthContext(usuario_id=7, empresa_id=42, role="DONO"),
                store_id=99,
            )

        state_passed = node.await_args.args[0]
        self.assertEqual(state_passed["store_id"], 99)

    async def test_predictive_trigger_defaults_to_company_wide_scope(self):
        generated = {"agent_response": "resposta bruta", "judge_score": 0.9, "sources": []}
        node = AsyncMock(return_value=generated)

        with (
            patch("app.agents.motor_preditivo.node_motor_preditivo", new=node),
            patch("app.agents.juiz.node_agente_juiz", new=AsyncMock(return_value=generated)),
            patch("interfaces.api.main.node_guardrail_saida", new=AsyncMock(return_value={**generated, "final_response": "ok"})),
        ):
            await trigger_motor_preditivo(AuthContext(usuario_id=7, empresa_id=42, role="DONO"))

        state_passed = node.await_args.args[0]
        self.assertIsNone(state_passed["store_id"])

    async def test_predictive_trigger_sanitizes_provider_failure(self):
        error = APIConnectionError(request=httpx.Request("POST", "https://api.groq.com"))
        with patch("app.agents.motor_preditivo.node_motor_preditivo", new=AsyncMock(side_effect=error)):
            with self.assertRaises(HTTPException) as context:
                await trigger_motor_preditivo(AuthContext(usuario_id=7, empresa_id=42, role="DONO"))

        self.assertEqual(context.exception.status_code, 503)
        self.assertNotIn("api.groq.com", context.exception.detail)

    async def test_predictive_trigger_sanitizes_openai_compatible_provider_failure(self):
        with patch(
            "app.agents.motor_preditivo.node_motor_preditivo",
            new=AsyncMock(side_effect=OpenAIError("local provider unavailable")),
        ):
            with self.assertRaises(HTTPException) as context:
                await trigger_motor_preditivo(AuthContext(usuario_id=7, empresa_id=42, role="DONO"))

        self.assertEqual(context.exception.status_code, 503)
        self.assertNotIn("local provider unavailable", context.exception.detail)

    async def test_chat_sanitizes_openai_compatible_provider_failure(self):
        with (
            patch("interfaces.api.main.mottainai_graph.ainvoke", new=AsyncMock(side_effect=OpenAIError("provider blocked"))),
            patch("interfaces.api.main.record_agent_execution", new=AsyncMock()),
            patch("interfaces.api.main.record_execution_metrics", new=AsyncMock()),
        ):
            with self.assertRaises(HTTPException) as context:
                await chat(
                    ChatRequest(message="teste", session_id="ollama-network-check"),
                    background_tasks=__import__("fastapi").BackgroundTasks(),
                    principal=AuthContext(usuario_id=7, empresa_id=42, role="CLIENTE"),
                )

        self.assertEqual(context.exception.status_code, 503)
        self.assertNotIn("provider blocked", context.exception.detail)

    async def test_shelf_analysis_rejects_session_from_another_principal(self):
        with patch(
            "interfaces.api.main.get_conversation",
            new=AsyncMock(side_effect=SessionOwnershipError("Sessão não pertence ao usuário.")),
        ):
            with self.assertRaises(HTTPException) as context:
                await analyze_shelf_image(
                    principal=AuthContext(usuario_id=7, empresa_id=42, role="ESTOQUISTA"),
                    image=self._image(),
                    session_id="other-session",
                )

        self.assertEqual(context.exception.status_code, 403)

    async def test_shelf_analysis_passes_authenticated_session_context_to_vision(self):
        analysis = {
            "estado_geral": "adequado",
            "ocupacao_pct": 80,
            "confianca_analise": 0.9,
            "produtos_detectados": [],
            "slots_vazios": {},
            "cruzamento_inventario": {},
            "acoes_sugeridas": [],
            "relatorio_texto": "ok",
        }
        vision = AsyncMock(return_value=analysis)
        with (
            patch("interfaces.api.main.get_conversation", new=AsyncMock(return_value={"_id": "conversation-1"})),
            patch("app.agents.visao.analyze_shelf", new=vision),
        ):
            result = await analyze_shelf_image(
                principal=AuthContext(usuario_id=7, empresa_id=42, role="ESTOQUISTA"),
                image=self._image(),
                store_id=5,
                session_id="session-1",
            )

        self.assertEqual(result["empresa_id"], 42)
        self.assertEqual(vision.await_args.kwargs["usuario_id"], 7)
        self.assertEqual(vision.await_args.kwargs["conversation_id"], "conversation-1")
        self.assertEqual(vision.await_args.kwargs["session_id"], "session-1")

    async def test_shelf_analysis_sanitizes_provider_failure(self):
        with patch(
            "app.agents.visao.analyze_shelf",
            new=AsyncMock(side_effect=ServiceUnavailable("provider offline")),
        ):
            with self.assertRaises(HTTPException) as context:
                await analyze_shelf_image(
                    principal=AuthContext(usuario_id=7, empresa_id=42, role="ESTOQUISTA"),
                    image=self._image(),
                )

        self.assertEqual(context.exception.status_code, 503)
        self.assertNotIn("provider offline", context.exception.detail)


if __name__ == "__main__":
    unittest.main()
