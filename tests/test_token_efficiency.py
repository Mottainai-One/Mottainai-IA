"""Token reduction must preserve factual evidence, traceability and validation."""
import json
import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from app.agents.context import context_json, model_context_json
from app.agents.juiz import node_agente_juiz
from app.observability.tool_runs import as_object
from app.rag.retriever import CHUNK_SEPARATOR, retrieve_with_sources
from tests.prompt_support import expand_tables


class ModelContextTests(unittest.TestCase):
    def test_compacted_inventory_preserves_every_value_unit_row_and_constant(self):
        records = [
            {"product_name": f"Produto {i}", "quantity": Decimal(f"{i}.123456789123456789"),
             "minimum_quantity": Decimal("7.000"), "maximum_quantity": Decimal("170.000"),
             "store_name": "Mottainai Paulista", "unit": "UN", "status": "NORMAL",
             "date": date(2026, 10, 7), "nullable": None}
            for i in range(20)
        ]
        packed = model_context_json(records)
        expected = json.loads(context_json(records))
        self.assertEqual(expand_tables(json.loads(packed)), expected)
        self.assertLess(len(packed), len(context_json(records)) * 0.6)

    def test_duplicate_rows_are_preserved_not_collapsed(self):
        rows = [{"quantity": 48, "store_name": "Mottainai Paulista", "product_name": "Banana"}] * 10
        self.assertEqual(expand_tables(json.loads(model_context_json(rows))), rows)

    def test_missing_fields_and_null_remain_different(self):
        rows = [{"quantity": 1, "note": None}, {"quantity": 2}, {"quantity": 3, "note": ""}]
        self.assertEqual(json.loads(model_context_json(rows)), rows)

    def test_boolean_is_not_factored_as_integer_one(self):
        rows = [{"quantity": i, "flag": value, "store_name": "same long store name"}
                for i, value in enumerate([True, 1, True, 1, True, 1])]
        expanded = expand_tables(json.loads(model_context_json(rows)))
        self.assertEqual([type(row["flag"]) for row in expanded], [type(row["flag"]) for row in rows])

    def test_metrics_and_audit_keep_the_original_dictionary_contract(self):
        rows = [{"quantity": Decimal("48.000"), "store_name": "Paulista"}] * 10
        stored = as_object(rows)
        self.assertIsInstance(stored["value"], list)
        self.assertEqual(stored["value"][0], {"quantity": 48, "store_name": "Paulista"})


class RagDeduplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_exact_duplicate_text_is_removed_and_all_sources_survive(self):
        rows = [
            {"text": text, "documentId": document, "chunk": 0, "score": 0.8}
            for text, document in [("Pontos expiram em 12 meses.", "a"),
                                   ("Pontos expiram em 12 meses.", "b"),
                                   ("Pontos expiram em 24 meses.", "c")]
        ]
        with patch("app.rag.retriever.retrieve", new=AsyncMock(return_value=rows)), patch("app.rag.retriever._read_rag_cache", new=AsyncMock(return_value=None)), patch("app.rag.retriever._write_rag_cache", new=AsyncMock()):
            context, sources = await retrieve_with_sources("pontos", 1)
        self.assertEqual(context, "Pontos expiram em 12 meses." + CHUNK_SEPARATOR + "Pontos expiram em 24 meses.")
        self.assertEqual([source["ref"] for source in sources], ["doc:a chunk:0", "doc:b chunk:0", "doc:c chunk:0"])


class JudgeUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_counts_agent_and_both_judge_attempts_even_invalid_json(self):
        llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
            AIMessage(content="invalid JSON", usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}),
            AIMessage(content=json.dumps({"approved": True, "scope_ok": True, "grounding_ok": True, "confidence_score": 0.9}),
                      usage_metadata={"input_tokens": 100, "output_tokens": 25, "total_tokens": 125}),
        ]))
        database = SimpleNamespace(prompt_evaluations=SimpleNamespace(insert_one=AsyncMock()))
        state = {"empresa_id": 1, "session_id": "s", "sanitized_input": "oi", "user_role": "CLIENTE",
                 "agent_response": "Olá!", "input_tokens": 200, "output_tokens": 30}
        with patch("app.agents.juiz.get_llm", return_value=llm), patch("app.database.mongo.get_mongo_db", return_value=database):
            result = await node_agente_juiz(state)
        self.assertTrue(result["judge_approved"])
        self.assertEqual((result["input_tokens"], result["output_tokens"]), (400, 75))
        self.assertEqual(llm.ainvoke.await_count, 2)
