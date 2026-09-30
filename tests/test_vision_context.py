"""Recent shelf-photo analyses reaching the Employee Agent's chat context —
previously persisted (app/agents/visao.py) but never read back anywhere."""
import unittest
from unittest.mock import AsyncMock, patch

from app.memory.short_term import get_recent_vision_analyses


class FakeAiResultsCollection:
    def __init__(self, documents):
        self._documents = documents
        self.last_query = None
        self.last_sort = None
        self.last_limit = None

    def find(self, query, sort=None):
        self.last_query = query
        self.last_sort = sort
        documents = self._documents

        class Cursor:
            def __init__(self, docs):
                self._docs = docs

            def limit(self, n):
                self._docs = self._docs[:n]
                return self

            def __aiter__(self):
                docs = self._docs

                async def iterate():
                    for doc in docs:
                        yield doc
                return iterate()

        return Cursor(documents)


class GetRecentVisionAnalysesTests(unittest.IsolatedAsyncioTestCase):
    async def test_summarizes_the_result_without_the_raw_blob(self):
        documents = [{
            "sessionId": "s1", "agent": "visao", "createdAt": "2026-01-01T00:00:00",
            "result": {
                "estado_geral": "crítico", "ocupacao_pct": 22,
                "produtos_detectados": [{"nome": "Leite"}, {"nome": "Iogurte"}],
                "acoes_sugeridas": ["Repor Leite"],
                "cruzamento_inventario": {"huge": "blob that should not leak into every prompt"},
            },
        }]
        db = type("Database", (), {"ai_results": FakeAiResultsCollection(documents)})()

        with patch("app.memory.short_term.get_mongo_db", return_value=db):
            summaries = await get_recent_vision_analyses("s1")

        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["estado_geral"], "crítico")
        self.assertEqual(summaries[0]["ocupacao_pct"], 22)
        self.assertEqual(summaries[0]["produtos_detectados"], 2)
        self.assertEqual(summaries[0]["acoes_sugeridas"], ["Repor Leite"])
        self.assertNotIn("cruzamento_inventario", summaries[0])

    async def test_filters_by_session_and_the_vision_agent(self):
        db = type("Database", (), {"ai_results": FakeAiResultsCollection([])})()

        with patch("app.memory.short_term.get_mongo_db", return_value=db):
            await get_recent_vision_analyses("s1", limit=3)

        self.assertEqual(db.ai_results.last_query, {"sessionId": "s1", "agent": "visao"})

    async def test_no_analyses_returns_empty_list(self):
        db = type("Database", (), {"ai_results": FakeAiResultsCollection([])})()

        with patch("app.memory.short_term.get_mongo_db", return_value=db):
            summaries = await get_recent_vision_analyses("s1")

        self.assertEqual(summaries, [])


class FuncionarioVisionWiringTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, vision_analyses):
        from app.agents import funcionario

        class _StubResponse:
            content = "resposta"
            usage_metadata = {"input_tokens": 1, "output_tokens": 1}

        state = {
            "sanitized_input": "oi", "empresa_id": 1, "usuario_id": 9,
            "session_id": "s1",
            "memory": {"preferences": [], "facts": [], "lastAgent": None, "lastSkill": None},
            "history": [],
        }

        with (
            patch("app.agents.funcionario.get_stock_alerts", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.get_inventory_status", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.get_expiring_batches", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.get_inbox", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.format_notifications_for_agent", new=AsyncMock(return_value="")),
            patch("app.agents.funcionario.retrieve_with_sources", new=AsyncMock(return_value=("", []))),
            patch("app.agents.funcionario.get_recent_vision_analyses", new=AsyncMock(return_value=vision_analyses)) as get_vision,
            patch("app.agents.funcionario.get_llm") as get_llm,
        ):
            get_llm.return_value.ainvoke = AsyncMock(return_value=_StubResponse())
            result = await funcionario.node_agente_funcionario(state)

        return result, get_vision

    async def test_fetches_vision_analyses_scoped_to_the_session(self):
        result, get_vision = await self._run([{"estado_geral": "crítico"}])

        get_vision.assert_awaited_once_with("s1", limit=3)
        vision_source = next((s for s in result["sources"] if s["ref"] == "app.agents.visao (ai_results)"), None)
        self.assertIsNotNone(vision_source)
        # Must be one of app.database.mongo_schema.SOURCE_TYPES — a made-up
        # value here 500s guardrail_saida's save_message() on every real
        # chat request, invisible to tests that mock Mongo.
        self.assertEqual(vision_source["type"], "other")

    async def test_no_vision_source_when_there_are_no_recent_analyses(self):
        result, _ = await self._run([])

        self.assertFalse(any(s["ref"] == "app.agents.visao (ai_results)" for s in result["sources"]))


if __name__ == "__main__":
    unittest.main()
