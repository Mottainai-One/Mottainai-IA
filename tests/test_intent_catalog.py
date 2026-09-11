"""Tests for app/agents/intent_catalog.py — the TTL-cached, fail-open
replacement for the hardcoded KEYWORDS_PREDITIVO/FAQ_KEYWORDS sets that used
to live in supervisor.py."""
import asyncio
import unittest
from unittest.mock import patch

from app.agents import intent_catalog


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        for doc in self._docs:
            yield doc


class _FakeIntentCatalogCollection:
    def __init__(self, docs):
        self._docs = docs
        self.calls = 0

    def find(self, query):
        self.calls += 1
        return _FakeCursor(self._docs)


class GetActiveIntentsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        intent_catalog._cache = None
        intent_catalog._cache_fetched_monotonic = None

    def tearDown(self):
        intent_catalog._cache = None
        intent_catalog._cache_fetched_monotonic = None

    async def test_fetches_and_caches_active_intents(self):
        docs = [{"key": "x", "agent": "faq", "active": True, "examples": ["oi"]}]
        collection = _FakeIntentCatalogCollection(docs)
        db = type("Database", (), {"intent_catalog": collection})()

        with patch.object(intent_catalog, "get_mongo_db", return_value=db):
            result = await intent_catalog.get_active_intents()

        self.assertEqual(result, docs)
        self.assertEqual(collection.calls, 1)

    async def test_second_call_within_ttl_does_not_hit_mongo_again(self):
        collection = _FakeIntentCatalogCollection([{"key": "x", "agent": "faq"}])
        db = type("Database", (), {"intent_catalog": collection})()

        with patch.object(intent_catalog, "get_mongo_db", return_value=db):
            await intent_catalog.get_active_intents()
            await intent_catalog.get_active_intents()

        self.assertEqual(collection.calls, 1)

    async def test_refetches_once_the_ttl_has_elapsed(self):
        collection = _FakeIntentCatalogCollection([{"key": "x", "agent": "faq"}])
        db = type("Database", (), {"intent_catalog": collection})()

        with patch.object(intent_catalog, "get_mongo_db", return_value=db):
            await intent_catalog.get_active_intents()
            intent_catalog._cache_fetched_monotonic -= intent_catalog.CACHE_TTL_SECONDS + 1
            await intent_catalog.get_active_intents()

        self.assertEqual(collection.calls, 2)

    async def test_falls_back_to_built_in_intents_when_mongo_raises(self):
        with patch.object(intent_catalog, "get_mongo_db", side_effect=RuntimeError("mongo down")):
            result = await intent_catalog.get_active_intents()

        self.assertEqual(result, intent_catalog.FALLBACK_INTENTS)

    async def test_falls_back_to_built_in_intents_on_timeout(self):
        async def _hangs():
            await asyncio.sleep(10)
            return []

        with (
            patch.object(intent_catalog, "_fetch_active_intents", side_effect=_hangs),
            patch.object(intent_catalog, "LOOKUP_TIMEOUT_S", 0.05),
        ):
            result = await intent_catalog.get_active_intents()

        self.assertEqual(result, intent_catalog.FALLBACK_INTENTS)

    async def test_falls_back_to_built_in_intents_when_collection_is_empty(self):
        collection = _FakeIntentCatalogCollection([])
        db = type("Database", (), {"intent_catalog": collection})()

        with patch.object(intent_catalog, "get_mongo_db", return_value=db):
            result = await intent_catalog.get_active_intents()

        self.assertEqual(result, intent_catalog.FALLBACK_INTENTS)

    async def test_a_failed_attempt_still_stamps_the_ttl_clock(self):
        # An outage should cost one timeout per CACHE_TTL_SECONDS window for
        # the whole process, not one per request.
        with patch.object(intent_catalog, "get_mongo_db", side_effect=RuntimeError("down")) as get_db:
            await intent_catalog.get_active_intents()
            await intent_catalog.get_active_intents()

        get_db.assert_called_once()


class MatchIntentTests(unittest.TestCase):
    INTENTS = [
        {"key": "forecast", "agent": "motor_preditivo", "examples": ["previsão", "demanda"], "confidenceThreshold": 0.0},
        {"key": "faq", "agent": "faq", "examples": ["fidelidade", "pontos", "ajuda", "suporte"], "confidenceThreshold": 0.0},
        {"key": "faq_strict", "agent": "faq", "examples": ["fidelidade"], "confidenceThreshold": 0.9},
    ]

    def test_matches_a_single_keyword_when_threshold_is_zero(self):
        key, agent, confidence = intent_catalog.match_intent(
            "qual a previsão de vendas?", ["motor_preditivo"], self.INTENTS,
        )
        self.assertEqual((key, agent), ("forecast", "motor_preditivo"))
        self.assertGreater(confidence, 0)

    def test_ignores_intents_outside_the_candidate_agents(self):
        # "previsão" matches the forecast intent's examples, but motor_preditivo
        # isn't an allowed agent for this call — same boundary CLIENTE/DONO
        # role-gating enforces in supervisor.py.
        key, agent, confidence = intent_catalog.match_intent(
            "previsão", ["faq"], self.INTENTS,
        )
        self.assertEqual((key, agent, confidence), (None, None, None))

    def test_highest_confidence_candidate_wins(self):
        # "fidelidade" alone matches both faq intents; faq_strict needs 0.9
        # confidence and only has one example, so a single match already
        # clears its threshold and wins for being the more specific match.
        key, agent, confidence = intent_catalog.match_intent(
            "fidelidade", ["faq"], self.INTENTS,
        )
        self.assertEqual(key, "faq_strict")
        self.assertEqual(confidence, 1.0)

    def test_confidence_threshold_excludes_a_weak_match(self):
        # Only "fidelidade" out of faq_strict's single example — but with a
        # generic multi-keyword intent also in play, a low-confidence single
        # keyword still is not enough for the strict one.
        intents = [{"key": "strict", "agent": "faq", "examples": ["a", "b", "c", "d"], "confidenceThreshold": 0.9}]
        key, agent, confidence = intent_catalog.match_intent("a", ["faq"], intents)
        self.assertEqual((key, agent, confidence), (None, None, None))

    def test_no_match_returns_none_triple(self):
        result = intent_catalog.match_intent("assunto qualquer", ["faq"], self.INTENTS)
        self.assertEqual(result, (None, None, None))

    def test_empty_examples_does_not_raise(self):
        intents = [{"key": "empty", "agent": "faq", "examples": [], "confidenceThreshold": 0.0}]
        result = intent_catalog.match_intent("qualquer coisa", ["faq"], intents)
        self.assertEqual(result, (None, None, None))

    def test_missing_examples_key_does_not_raise(self):
        intents = [{"key": "no_examples", "agent": "faq", "confidenceThreshold": 0.0}]
        result = intent_catalog.match_intent("qualquer coisa", ["faq"], intents)
        self.assertEqual(result, (None, None, None))


if __name__ == "__main__":
    unittest.main()
