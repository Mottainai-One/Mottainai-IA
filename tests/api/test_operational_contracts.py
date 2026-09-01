"""Operational contracts for safe health and container entrypoints."""
import io
import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
    correlation_id_middleware,
    descartar_lote,
    DescartarLoteRequest,
    health_check,
    live_check,
    logout,
    readiness_check,
    RagDocumentUploadRequest,
    receber_mercadoria,
    ReceberMercadoriaRequest,
    trigger_motor_preditivo,
    unhandled_exception_handler,
    upload_rag_document,
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


class CorrelationIdMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_generates_and_echoes_a_new_correlation_id(self):
        request = MagicMock()
        request.headers.get.return_value = None
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        with patch("interfaces.api.main.set_correlation_id") as set_id:
            result = await correlation_id_middleware(request, call_next)

        set_id.assert_called_once()
        generated = set_id.call_args.args[0]
        self.assertEqual(result.headers["X-Request-ID"], generated)

    async def test_reuses_an_incoming_request_id_header(self):
        request = MagicMock()
        request.headers.get.return_value = "client-supplied-id"
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        with patch("interfaces.api.main.set_correlation_id") as set_id:
            result = await correlation_id_middleware(request, call_next)

        set_id.assert_called_once_with("client-supplied-id")
        self.assertEqual(result.headers["X-Request-ID"], "client-supplied-id")


class UnhandledExceptionHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_logs_and_returns_a_sanitized_portuguese_500(self):
        request = SimpleNamespace(method="GET", url=SimpleNamespace(path="/audit/report"))
        error = RuntimeError("connection to postgresql://mottainai:s3cr3t@db failed")

        with patch("interfaces.api.main.logger") as logger:
            response = await unhandled_exception_handler(request, error)

        self.assertEqual(response.status_code, 500)
        body = json.loads(response.body)
        self.assertNotIn("s3cr3t", body["detail"])
        self.assertNotIn("postgresql://", body["detail"])
        self.assertEqual(body["detail"], "Erro interno inesperado. Tente novamente ou contate o suporte.")
        logger.error.assert_called_once()
        self.assertIn("/audit/report", logger.error.call_args.args)


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


class RagDocumentUploadRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_ingests_the_document_for_the_authenticated_company(self):
        tool = AsyncMock(return_value={"document_id": "d1", "slug": "faq-x", "chunks": 3})
        body = RagDocumentUploadRequest(slug="faq-x", title="FAQ X", source="faq", text="algo")

        with patch("app.rag.ingestion.ingest_document", new=tool):
            result = await upload_rag_document(body, AuthContext(usuario_id=7, empresa_id=42, role="GERENTE"))

        # category/version are required by rag_documents' $jsonSchema; the
        # route forwards them so the write is not rejected by Mongo.
        tool.assert_awaited_once_with(
            empresa_id=42, slug="faq-x", title="FAQ X", source="faq", text="algo",
            category=None, version="1.0",
        )
        self.assertEqual(result["chunks"], 3)

    async def test_returns_409_for_a_duplicate_slug(self):
        from app.rag.ingestion import DuplicateSlugError

        body = RagDocumentUploadRequest(slug="ja-existe", title="T", source="faq", text="algo")
        with patch("app.rag.ingestion.ingest_document", new=AsyncMock(side_effect=DuplicateSlugError("dup"))):
            with self.assertRaises(HTTPException) as context:
                await upload_rag_document(body, AuthContext(usuario_id=7, empresa_id=42, role="DONO"))

        self.assertEqual(context.exception.status_code, 409)

    async def test_returns_422_when_no_chunks_are_produced(self):
        body = RagDocumentUploadRequest(slug="vazio", title="T", source="faq", text="algo")
        with patch("app.rag.ingestion.ingest_document", new=AsyncMock(side_effect=ValueError("sem chunks"))):
            with self.assertRaises(HTTPException) as context:
                await upload_rag_document(body, AuthContext(usuario_id=7, empresa_id=42, role="DONO"))

        self.assertEqual(context.exception.status_code, 422)


class EmployeeWriteRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def test_descartar_lote_calls_the_tool_with_the_authenticated_employee(self):
        tool = AsyncMock(return_value={"disposal_id": 1, "new_inventory_balance": Decimal("2")})
        body = DescartarLoteRequest(store_id=1, batch_id=7, quantity=Decimal("3"), reason="vencido")

        with patch("app.tools.postgres_tools.discard_batch", new=tool):
            result = await descartar_lote(body, AuthContext(usuario_id=9, empresa_id=42, role="ESTOQUISTA"))

        tool.assert_awaited_once_with(
            empresa_id=42, store_id=1, batch_id=7, employee_id=9,
            quantity=Decimal("3"), reason="vencido", observation=None,
        )
        self.assertEqual(result["disposal_id"], 1)

    async def test_descartar_lote_returns_404_when_inventory_not_found(self):
        with patch("app.tools.postgres_tools.discard_batch", new=AsyncMock(side_effect=ValueError("não encontrado"))):
            with self.assertRaises(HTTPException) as context:
                await descartar_lote(
                    DescartarLoteRequest(store_id=1, batch_id=7, quantity=Decimal("3"), reason="vencido"),
                    AuthContext(usuario_id=9, empresa_id=42, role="ESTOQUISTA"),
                )

        self.assertEqual(context.exception.status_code, 404)

    async def test_receber_mercadoria_calls_the_tool_with_the_authenticated_employee(self):
        tool = AsyncMock(return_value={"new_inventory_balance": Decimal("50")})
        body = ReceberMercadoriaRequest(store_id=1, batch_id=7, quantity=Decimal("20"))

        with patch("app.tools.postgres_tools.receive_inventory", new=tool):
            result = await receber_mercadoria(body, AuthContext(usuario_id=9, empresa_id=42, role="GERENTE"))

        tool.assert_awaited_once_with(
            empresa_id=42, store_id=1, batch_id=7, employee_id=9,
            quantity=Decimal("20"), observation=None,
        )
        self.assertEqual(result["new_inventory_balance"], Decimal("50"))

    async def test_receber_mercadoria_returns_404_when_inventory_not_found(self):
        with patch("app.tools.postgres_tools.receive_inventory", new=AsyncMock(side_effect=ValueError("não encontrado"))):
            with self.assertRaises(HTTPException) as context:
                await receber_mercadoria(
                    ReceberMercadoriaRequest(store_id=1, batch_id=7, quantity=Decimal("20")),
                    AuthContext(usuario_id=9, empresa_id=42, role="GERENTE"),
                )

        self.assertEqual(context.exception.status_code, 404)


class LogoutRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_revokes_the_callers_own_token(self):
        principal = AuthContext(usuario_id=7, empresa_id=42, role="DONO", jti="tok-1", exp=9999999999)
        revoke = AsyncMock()
        with patch("app.security.auth.revoke_token", new=revoke):
            result = await logout(principal)

        revoke.assert_awaited_once_with("tok-1", 9999999999)
        self.assertEqual(result, {"status": "revoked"})

    async def test_rejects_a_token_without_jti(self):
        principal = AuthContext(usuario_id=7, empresa_id=42, role="DONO")
        with self.assertRaises(HTTPException) as context:
            await logout(principal)

        self.assertEqual(context.exception.status_code, 400)

    async def test_returns_503_when_revocation_cannot_be_persisted(self):
        principal = AuthContext(usuario_id=7, empresa_id=42, role="DONO", jti="tok-1", exp=9999999999)
        with patch("app.security.auth.revoke_token", new=AsyncMock(side_effect=ConnectionError("down"))):
            with self.assertRaises(HTTPException) as context:
                await logout(principal)

        self.assertEqual(context.exception.status_code, 503)


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
