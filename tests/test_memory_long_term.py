"""Contract for app/memory/long_term.py — load/update of the user's
long-term memory (MongoDB `memories` collection) and its prompt formatting."""
import unittest
from unittest.mock import patch

from app.memory import long_term


class FakeMemoriesCollection:
    def __init__(self, document=None):
        self._document = document
        self.update_filter = None
        self.update_body = None
        self.upsert = None

    async def find_one(self, query):
        return self._document

    async def update_one(self, query, update, upsert=False):
        self.update_filter = query
        self.update_body = update
        self.upsert = upsert


class LoadMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_defaults_when_no_document(self):
        db = type("Database", (), {"memories": FakeMemoriesCollection(None)})()

        with patch.object(long_term, "get_mongo_db", return_value=db):
            memory = await long_term.load_memory(42, 9)

        self.assertEqual(memory, {"preferences": [], "facts": [], "lastAgent": None, "lastSkill": None})

    async def test_returns_stored_preferences_facts_last_agent_last_skill(self):
        document = {
            "preferences": ["gosta de café"], "facts": ["trabalha na filial X"],
            "lastAgent": "funcionario", "lastSkill": "estoque",
        }
        db = type("Database", (), {"memories": FakeMemoriesCollection(document)})()

        with patch.object(long_term, "get_mongo_db", return_value=db):
            memory = await long_term.load_memory(42, 9)

        self.assertEqual(memory, document)


class UpdateMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_upserts_with_setOnInsert(self):
        collection = FakeMemoriesCollection()
        db = type("Database", (), {"memories": collection})()

        with patch.object(long_term, "get_mongo_db", return_value=db):
            await long_term.update_memory(42, 9, last_agent="dono")

        self.assertTrue(collection.upsert)
        self.assertEqual(collection.update_body["$setOnInsert"], {"empresaId": 42, "usuarioId": 9})
        self.assertEqual(collection.update_filter, {"empresaId": 42, "usuarioId": 9})

    async def test_uses_addToSet_for_new_preference(self):
        collection = FakeMemoriesCollection()
        db = type("Database", (), {"memories": collection})()

        with patch.object(long_term, "get_mongo_db", return_value=db):
            await long_term.update_memory(42, 9, new_preference="gosta de café")

        self.assertEqual(collection.update_body["$addToSet"], {"preferences": "gosta de café"})

    async def test_uses_addToSet_for_new_fact(self):
        collection = FakeMemoriesCollection()
        db = type("Database", (), {"memories": collection})()

        with patch.object(long_term, "get_mongo_db", return_value=db):
            await long_term.update_memory(42, 9, new_fact="trabalha na filial X")

        self.assertEqual(collection.update_body["$addToSet"], {"facts": "trabalha na filial X"})

    async def test_sets_last_agent_and_last_skill(self):
        collection = FakeMemoriesCollection()
        db = type("Database", (), {"memories": collection})()

        with patch.object(long_term, "get_mongo_db", return_value=db):
            await long_term.update_memory(42, 9, last_agent="dono", last_skill="kpis")

        self.assertEqual(collection.update_body["$set"]["lastAgent"], "dono")
        self.assertEqual(collection.update_body["$set"]["lastSkill"], "kpis")

    async def test_omits_addToSet_when_no_preference_or_fact_given(self):
        collection = FakeMemoriesCollection()
        db = type("Database", (), {"memories": collection})()

        with patch.object(long_term, "get_mongo_db", return_value=db):
            await long_term.update_memory(42, 9, last_agent="dono")

        self.assertNotIn("$addToSet", collection.update_body)


class FormatMemoryForPromptTests(unittest.TestCase):
    def test_includes_preferences_and_facts_lines(self):
        memory = {
            "preferences": ["gosta de café", "prefere e-mail"], "facts": ["trabalha na filial X"],
            "lastAgent": None, "lastSkill": None,
        }

        text = long_term.format_memory_for_prompt(memory)

        self.assertIn("gosta de café, prefere e-mail", text)
        self.assertIn("trabalha na filial X", text)

    def test_includes_last_agent_and_last_skill_lines(self):
        memory = {"preferences": [], "facts": [], "lastAgent": "dono", "lastSkill": "kpis"}

        text = long_term.format_memory_for_prompt(memory)

        self.assertIn("dono", text)
        self.assertIn("kpis", text)

    def test_returns_placeholder_when_all_empty(self):
        memory = {"preferences": [], "facts": [], "lastAgent": None, "lastSkill": None}

        text = long_term.format_memory_for_prompt(memory)

        self.assertEqual(text, "Nenhuma memória prévia registrada.")


if __name__ == "__main__":
    unittest.main()
