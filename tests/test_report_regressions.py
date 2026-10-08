"""Regressions from the October 7 API report, without external dependencies."""
import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from groq import RateLimitError
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agents import runtime
from app.agents.context import context_json
from app.agents.juiz import _approved, _run_judge_evaluation, node_agente_juiz
from app.database.postgres import validate_pg_security
from app.guardrails.saida import MAX_RESPONSE_LEN, guardrail_saida
from config.settings import Settings


class EvidenceTests(unittest.TestCase):
    def test_integral_stock_decimals_are_numbers_and_fractions_keep_precision(self):
        result = json.loads(context_json({
            "quantity": Decimal("48.000"), "min_quantity": Decimal("7.000"),
            "max_quantity": Decimal("170.000"), "fraction": Decimal("7.123456789123456789"),
        }))
        self.assertEqual(result, {"quantity": 48, "min_quantity": 7, "max_quantity": 170,
                                  "fraction": "7.123456789123456789"})

    def test_high_confidence_cannot_override_scope_or_grounding_failure(self):
        for field in ("scope_ok", "grounding_ok"):
            evaluation = {"approved": True, "confidence_score": 0.99,
                          "scope_ok": True, "grounding_ok": True}
            evaluation[field] = False
            self.assertFalse(_approved(evaluation)[0])

    def test_oversized_structured_response_is_blocked_whole(self):
        result = guardrail_saida('{"analysis":"' + 'x' * MAX_RESPONSE_LEN + '"}')
        self.assertFalse(result.safe)
        self.assertNotIn('"analysis"', result.output)
        self.assertNotIn("truncada", result.output)


class JudgeEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_judge_receives_values_not_just_source_labels(self):
        evaluation = {"approved": True, "confidence_score": 0.9, "scope_ok": True,
                      "grounding_ok": True, "issues": []}
        llm = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content=json.dumps(evaluation))))
        database = SimpleNamespace(prompt_evaluations=SimpleNamespace(insert_one=AsyncMock()))
        state = {"empresa_id": 1, "session_id": "test", "user_role": "ESTOQUISTA",
                 "selected_agent": "funcionario", "sanitized_input": "Qual estoque?",
                 "agent_response": "Banana: 48 unidades.", "sources": [],
                 "grounding_context": context_json({"product": "Banana", "quantity": Decimal("48.000")})}
        with patch("app.agents.juiz.get_llm", return_value=llm), patch("app.database.mongo.get_mongo_db", return_value=database):
            result = await node_agente_juiz(state)
        prompt = llm.ainvoke.call_args.args[0][1].content
        self.assertIn('"quantity":48', prompt)
        self.assertTrue(result["judge_approved"])

    async def test_malformed_judge_types_fail_closed(self):
        for data in ([1], {"approved": "false", "confidence_score": 0.9},
                     {"approved": True, "scope_ok": True, "grounding_ok": True,
                      "confidence_score": float("nan")}):
            llm = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content=json.dumps(data))))
            result, unavailable = await _run_judge_evaluation(llm, [])
            self.assertTrue(unavailable)
            self.assertFalse(result["approved"])


class ProviderRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_after_is_observed_and_sdk_retries_are_disabled(self):
        response = httpx.Response(429, headers={"retry-after": "5"},
                                  request=httpx.Request("POST", "https://example.test"))
        error = RateLimitError("quota", response=response, body={})
        call = AsyncMock(side_effect=[error, AIMessage(content="ok")])
        settings = Settings(_env_file=None, llm_provider="groq", groq_api_key="test",
                            llm_tokens_per_minute=0, llm_max_retries=2)
        with patch.object(runtime, "settings", settings), patch.object(runtime, "_provider_ready_at", 0), patch.object(runtime.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            result = await runtime.ProviderRunnable(bound=RunnableLambda(call)).ainvoke([])
            self.assertEqual(result.content, "ok")
            self.assertEqual(call.await_count, 2)
            self.assertTrue(any(args.args[0] > 4 for args in sleep.await_args_list))
            self.assertEqual(runtime._build_llm(0).max_retries, 0)

    async def test_quota_wait_shares_the_request_deadline(self):
        settings = Settings(_env_file=None, llm_provider="groq", llm_request_timeout_seconds=0.01)
        call = AsyncMock(return_value=AIMessage(content="ok"))
        with patch.object(runtime, "settings", settings), patch.object(runtime, "_provider_ready_at", runtime.time.monotonic() + 60):
            with self.assertRaises(TimeoutError):
                await runtime.ProviderRunnable(bound=RunnableLambda(call)).ainvoke([])
        call.assert_not_awaited()


class DatabaseStartupSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_superuser_or_bypass_roles(self):
        for flags in ({"rolsuper": True, "rolbypassrls": False},
                      {"rolsuper": False, "rolbypassrls": True}):
            result = SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: flags))
            connection = SimpleNamespace(execute=AsyncMock(return_value=result))
            context = AsyncMock()
            context.__aenter__.return_value = connection
            with patch("app.database.postgres.engine") as engine:
                engine.connect.return_value = context
                with self.assertRaisesRegex(RuntimeError, "NOSUPERUSER"):
                    await validate_pg_security()
