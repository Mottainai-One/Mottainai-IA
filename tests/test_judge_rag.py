"""Fail-closed judge and tenant-scoped RAG tests."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.juiz import node_agente_juiz
from app.rag.retriever import get_embedding_model, retrieve
from config.settings import Settings


class DirectAgentImportTests(unittest.TestCase):
    def test_domain_agents_do_not_import_or_build_the_graph(self):
        script = """
import importlib
import sys

for module_name in (
    "cliente",
    "dono",
    "faq",
    "funcionario",
    "juiz",
    "motor_preditivo",
):
    importlib.import_module(f"app.agents.{module_name}")

if "app.agents.supervisor" in sys.modules:
    raise SystemExit("domain agent import unexpectedly loaded supervisor")
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


class FakeCollection:
    def __init__(self, documents): self.documents = documents
    def find(self, query, projection=None):
        documents = self.documents
        class Cursor:
            def __aiter__(self):
                async def iterate():
                    for document in documents: yield document
                return iterate()
        return Cursor()
    async def insert_one(self, document): self.documents.append(document)


class _StubResponse:
    def __init__(self, content: str):
        self.content = content


class SequencedLlm:
    """Returns a different scripted response on each successive ainvoke call."""

    def __init__(self, contents: list[str]):
        self._contents = iter(contents)
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return _StubResponse(next(self._contents))


def _judge_json(*, approved: bool, score: float, scope_ok: bool = True, grounding_ok: bool = True) -> str:
    return json.dumps({
        "approved": approved,
        "confidence_score": score,
        "grounding_ok": grounding_ok,
        "scope_ok": scope_ok,
        "issues": [],
        "revised_response": None,
    })


class JudgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_fails_closed_when_judge_provider_fails(self):
        class Database:
            def __init__(self): self.prompt_evaluations = FakeCollection([])
        class BrokenLlm:
            async def ainvoke(self, messages): raise RuntimeError("offline")
        database = Database()
        state = {
            "empresa_id": 42,
            "session_id": "session-42",
            "agent_response": "dado não verificado",
            "user_role": "CLIENTE",
            "sources": [],
            "sanitized_input": "teste",
            "selected_agent": "cliente",
        }
        with patch("app.agents.juiz.get_llm", return_value=BrokenLlm()), patch("app.database.mongo.get_mongo_db", return_value=database):
            result = await node_agente_juiz(state)
        self.assertFalse(result["judge_approved"])
        self.assertEqual(result["judge_score"], 0.0)
        self.assertIn("não tenho informações suficientes", result["agent_response"].lower())
        self.assertEqual(database.prompt_evaluations.documents[0]["empresaId"], 42)
        self.assertEqual(database.prompt_evaluations.documents[0]["sessionId"], "session-42")

    async def test_recovers_from_a_flaky_first_rejection(self):
        # Reproduces what was observed live: the exact same grounded response
        # scored 0.35 on one evaluation and 0.86 on an identical retry.
        llm = SequencedLlm([
            _judge_json(approved=False, score=0.35),
            _judge_json(approved=True, score=0.86),
        ])
        database = type("Database", (), {"prompt_evaluations": FakeCollection([])})()
        state = {
            "empresa_id": 1, "session_id": "s1", "agent_response": "resposta correta e fundamentada",
            "user_role": "DONO", "sources": [], "sanitized_input": "compare minhas lojas",
            "selected_agent": "dono",
        }
        with patch("app.agents.juiz.get_llm", return_value=llm), patch("app.database.mongo.get_mongo_db", return_value=database):
            result = await node_agente_juiz(state)
        self.assertEqual(llm.calls, 2)
        self.assertTrue(result["judge_approved"])
        self.assertEqual(result["judge_score"], 0.86)
        self.assertEqual(result["agent_response"], "resposta correta e fundamentada")

    async def test_keeps_rejecting_when_retry_also_rejects(self):
        llm = SequencedLlm([
            _judge_json(approved=False, score=0.3, grounding_ok=False),
            _judge_json(approved=False, score=0.4, grounding_ok=False),
        ])
        database = type("Database", (), {"prompt_evaluations": FakeCollection([])})()
        state = {
            "empresa_id": 1, "session_id": "s1", "agent_response": "resposta inventada",
            "user_role": "DONO", "sources": [], "sanitized_input": "qual o lucro?",
            "selected_agent": "dono",
        }
        with patch("app.agents.juiz.get_llm", return_value=llm), patch("app.database.mongo.get_mongo_db", return_value=database):
            result = await node_agente_juiz(state)
        self.assertEqual(llm.calls, 2)
        self.assertFalse(result["judge_approved"])
        self.assertIn("não encontrei essa informação", result["agent_response"].lower())


class RagTests(unittest.IsolatedAsyncioTestCase):
    async def test_retrieves_only_chunks_of_requested_company(self):
        class Database:
            rag_documents = FakeCollection([{"_id": "doc-company-1"}])
            rag_chunks = FakeCollection([{"documentId": "doc-company-1", "text": "procedimento de estoque", "embedding": [1.0, 0.0], "chunk": 0}])
        with patch("app.rag.retriever.get_mongo_db", return_value=Database()), patch("app.rag.retriever.embed", return_value=[1.0, 0.0]):
            results = await retrieve("estoque", empresa_id=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["documentId"], "doc-company-1")


class LocalModelAndVisionConfigTests(unittest.TestCase):
    def tearDown(self):
        get_embedding_model.cache_clear()

    def test_embedding_model_uses_local_cache_only(self):
        with patch("app.rag.retriever.SentenceTransformer") as model:
            get_embedding_model.cache_clear()
            get_embedding_model()

        self.assertTrue(model.call_args.kwargs["local_files_only"])

    def test_migrates_the_retired_vision_model_name(self):
        settings = Settings(_env_file=None, gemini_vision_model="gemini-1.5-flash")

        self.assertEqual(settings.gemini_vision_model, "gemini-2.5-flash")


if __name__ == "__main__":
    unittest.main()
