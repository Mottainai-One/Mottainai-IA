"""Tests for scripts/mongo/schema.json — the declared MongoDB schema.

The file exists because setup_mongo.py had drifted from the database it is
supposed to build: it created 7 collections with no validators while the real
database had 22, all validated. These tests keep the declaration honest about
the two things the application code depends on — that every collection it
writes to is declared, and that the source-type enum it validates against is
the same one the database enforces.
"""
import json
import unittest
from pathlib import Path

from app.database.mongo_schema import SOURCE_TYPES

ROOT = Path(__file__).parent.parent
SCHEMA_PATH = ROOT / "scripts" / "mongo" / "schema.json"

# Collections the application reads or writes at runtime. A collection may
# exist in the schema without appearing here (the database carries several the
# code does not use yet); the reverse is what breaks an environment.
COLLECTIONS_USED_BY_THE_APP = {
    "conversations", "messages", "memories", "metrics", "agent_executions",
    "prompt_evaluations", "rag_documents", "rag_chunks", "ai_results",
    "agent_policies", "conversation_events",
}


class SchemaFileTests(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_every_collection_the_app_uses_is_declared(self):
        missing = COLLECTIONS_USED_BY_THE_APP - set(self.schema)
        self.assertEqual(missing, set(), f"coleções usadas pelo código e não declaradas: {missing}")

    def test_every_declared_collection_has_a_validator(self):
        # A collection without a validator accepts anything, which is the
        # condition that let four contract bugs reach production.
        without = [name for name, spec in self.schema.items() if "validator" not in spec]
        self.assertEqual(without, [], f"coleções sem validador: {without}")

    def test_message_source_types_match_the_python_constant(self):
        # app/agents/*.py build sources dicts against SOURCE_TYPES, and
        # scripts/validate_ai.py enforces that statically. If the database
        # enum and the constant drift apart, the static check passes and the
        # write still 500s — so pin them together.
        enum = (
            self.schema["messages"]["validator"]["$jsonSchema"]
            ["properties"]["sources"]["items"]["properties"]["type"]["enum"]
        )
        self.assertEqual(set(enum), set(SOURCE_TYPES))

    def test_rag_document_slug_is_unique_per_company_not_globally(self):
        indexes = self.schema["rag_documents"]["indexes"]
        slug_indexes = [
            (name, spec) for name, spec in indexes.items()
            if [field for field, _ in spec["key"]] == ["empresaId", "slug"]
        ]
        self.assertEqual(len(slug_indexes), 1, "índice (empresaId, slug) ausente ou duplicado")
        self.assertTrue(slug_indexes[0][1].get("unique"))
        # a globally unique slug would let one tenant claim a name for everyone
        for name, spec in indexes.items():
            fields = [field for field, _ in spec["key"]]
            self.assertNotEqual(fields, ["slug"], f"{name} torna o slug único globalmente")

    def test_index_keys_are_well_formed(self):
        for collection, spec in self.schema.items():
            for name, index in spec.get("indexes", {}).items():
                self.assertTrue(index.get("key"), f"{collection}.{name} sem chave")
                for entry in index["key"]:
                    self.assertEqual(len(entry), 2, f"{collection}.{name}: par (campo, direção) inválido")
                    field, direction = entry
                    self.assertIsInstance(field, str)
                    self.assertIn(direction, (1, -1, "text", "2dsphere", "hashed"))


if __name__ == "__main__":
    unittest.main()
