"""Contract for app/observability/executions.py::record_agent_execution —
persistence of execution traces for audit/SRE."""
import unittest
from unittest.mock import patch

from app.observability import executions


class FakeAgentExecutionsCollection:
    def __init__(self):
        self.inserted = []

    async def insert_one(self, document):
        self.inserted.append(document)


class RecordAgentExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_inserts_execution_document_with_rounded_latency(self):
        collection = FakeAgentExecutionsCollection()
        db = type("Database", (), {"agent_executions": collection})()

        with patch.object(executions, "get_mongo_db", return_value=db):
            await executions.record_agent_execution(
                empresa_id=42, session_id="s1", agent="dono", status="completed",
                latency_s=0.123456,
            )

        document = collection.inserted[0]
        self.assertEqual(document["empresaId"], 42)
        self.assertEqual(document["sessionId"], "s1")
        self.assertEqual(document["agent"], "dono")
        self.assertEqual(document["status"], "completed")
        self.assertEqual(document["latency"], 0.1235)

    async def test_defaults_node_latencies_ms_to_empty_dict_and_error_to_none(self):
        collection = FakeAgentExecutionsCollection()
        db = type("Database", (), {"agent_executions": collection})()

        with patch.object(executions, "get_mongo_db", return_value=db):
            await executions.record_agent_execution(
                empresa_id=42, session_id="s1", agent="dono", status="completed", latency_s=0.1,
            )

        document = collection.inserted[0]
        self.assertEqual(document["nodeLatenciesMs"], {})
        self.assertIsNone(document["error"])
        self.assertIsNone(document["conversationId"])

    async def test_stores_provided_error_and_node_latencies_ms(self):
        collection = FakeAgentExecutionsCollection()
        db = type("Database", (), {"agent_executions": collection})()

        with patch.object(executions, "get_mongo_db", return_value=db):
            await executions.record_agent_execution(
                empresa_id=42, session_id="s1", agent="dono", status="error", latency_s=0.1,
                conversation_id="c1", node_latencies_ms={"supervisor": 12.3}, error="timeout",
            )

        document = collection.inserted[0]
        self.assertEqual(document["conversationId"], "c1")
        self.assertEqual(document["nodeLatenciesMs"], {"supervisor": 12.3})
        self.assertEqual(document["error"], "timeout")


if __name__ == "__main__":
    unittest.main()
