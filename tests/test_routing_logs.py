"""Contract for app/observability/routing_logs.py::record_routing_log —
persistence of the supervisor's routing decision, one document per request."""
import unittest
from unittest.mock import patch

from app.observability import routing_logs


class FakeRoutingLogsCollection:
    def __init__(self):
        self.inserted = []

    async def insert_one(self, document):
        self.inserted.append(document)


class RecordRoutingLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_inserts_the_routing_decision(self):
        collection = FakeRoutingLogsCollection()
        db = type("Database", (), {"routing_logs": collection})()

        with patch.object(routing_logs, "get_mongo_db", return_value=db):
            await routing_logs.record_routing_log(
                conversation_id="c1", intent="cliente_faq",
                selected_agent="faq", selected_skill=None, confidence=0.5,
            )

        document = collection.inserted[0]
        self.assertEqual(document["conversationId"], "c1")
        self.assertEqual(document["intent"], "cliente_faq")
        self.assertEqual(document["selectedAgent"], "faq")
        self.assertIsNone(document["selectedSkill"])
        self.assertEqual(document["confidence"], 0.5)
        self.assertIn("createdAt", document)

    async def test_defaults_selected_skill_and_confidence_to_none(self):
        collection = FakeRoutingLogsCollection()
        db = type("Database", (), {"routing_logs": collection})()

        with patch.object(routing_logs, "get_mongo_db", return_value=db):
            await routing_logs.record_routing_log(
                conversation_id="c1", intent="default", selected_agent="dono",
            )

        document = collection.inserted[0]
        self.assertIsNone(document["selectedSkill"])
        self.assertIsNone(document["confidence"])


if __name__ == "__main__":
    unittest.main()
