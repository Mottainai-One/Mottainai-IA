"""Contract for the app/memory/short_term.py functions not already covered
elsewhere: get_or_create_conversation/save_message are exercised in
tests/api/test_access_boundaries.py, and get_recent_vision_analyses in
tests/test_vision_context.py — not duplicated here."""
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.memory import short_term


class _AsyncCursor:
    def __init__(self, docs):
        self._docs = docs
        self.sort_args = None
        self.limit_value = None

    def sort(self, *args):
        self.sort_args = args
        return self

    def limit(self, n):
        self.limit_value = n
        self._docs = self._docs[:n]
        return self

    def __aiter__(self):
        docs = self._docs

        async def iterate():
            for doc in docs:
                yield doc
        return iterate()


class FakeConversationsCollection:
    def __init__(self, documents=None):
        self._documents = documents or []
        self.last_find_query = None
        self.last_find_projection = None
        self.last_cursor = None
        self.update_filter = None
        self.update_body = None

    async def find_one(self, query):
        for doc in self._documents:
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    def find(self, query, projection=None):
        self.last_find_query = query
        self.last_find_projection = projection
        self.last_cursor = _AsyncCursor(self._documents)
        return self.last_cursor

    async def update_one(self, query, update):
        self.update_filter = query
        self.update_body = update
        matched = any(all(doc.get(k) == v for k, v in query.items()) for doc in self._documents)

        class Result:
            modified_count = 1 if matched else 0
        return Result()


class FakeMessagesCollection:
    def __init__(self, documents):
        self._documents = documents
        self.last_query = None
        self.last_sort = None
        self.last_cursor = None

    def find(self, query, sort=None):
        self.last_query = query
        self.last_sort = sort
        self.last_cursor = _AsyncCursor(self._documents)
        return self.last_cursor


class LoadHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, conv, messages, limit=20):
        db = type("Database", (), {
            "conversations": FakeConversationsCollection([conv] if conv else []),
            "messages": FakeMessagesCollection(messages),
        })()
        with patch.object(short_term, "get_mongo_db", return_value=db):
            result = await short_term.load_history("s1", limit=limit)
        return result, db

    async def test_converts_user_role_to_human_message(self):
        result, _ = await self._run({"sessionId": "s1", "_id": "c1"}, [{"role": "user", "content": "oi"}])

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], HumanMessage)
        self.assertEqual(result[0].content, "oi")

    async def test_converts_assistant_role_to_ai_message(self):
        result, _ = await self._run({"sessionId": "s1", "_id": "c1"}, [{"role": "assistant", "content": "olá"}])

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], AIMessage)

    async def test_converts_system_role_to_system_message(self):
        result, _ = await self._run({"sessionId": "s1", "_id": "c1"}, [{"role": "system", "content": "contexto"}])

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], SystemMessage)

    async def test_returns_empty_list_when_no_conversation(self):
        result, _ = await self._run(None, [])

        self.assertEqual(result, [])

    async def test_applies_the_given_limit(self):
        messages = [{"role": "user", "content": str(i)} for i in range(5)]
        result, db = await self._run({"sessionId": "s1", "_id": "c1"}, messages, limit=2)

        self.assertEqual(len(result), 2)
        self.assertEqual(db.messages.last_cursor.limit_value, 2)


class ListConversationsTests(unittest.IsolatedAsyncioTestCase):
    async def test_projects_out_id_and_sorts_by_last_interaction_desc(self):
        docs = [{"sessionId": "s1"}]
        collection = FakeConversationsCollection(docs)
        db = type("Database", (), {"conversations": collection})()

        with patch.object(short_term, "get_mongo_db", return_value=db):
            result = await short_term.list_conversations(42, 9)

        self.assertEqual(collection.last_find_query, {"empresaId": 42, "usuarioId": 9})
        self.assertEqual(collection.last_find_projection, {
            "_id": 0, "sessionId": 1, "agent": 1, "status": 1, "title": 1,
            "startedAt": 1, "lastInteraction": 1, "endedAt": 1,
        })
        self.assertEqual(collection.last_cursor.sort_args, ("lastInteraction", -1))
        self.assertEqual(result, docs)

    async def test_caps_limit_at_100(self):
        collection = FakeConversationsCollection([])
        db = type("Database", (), {"conversations": collection})()

        with patch.object(short_term, "get_mongo_db", return_value=db):
            await short_term.list_conversations(42, 9, limit=500)

        self.assertEqual(collection.last_cursor.limit_value, 100)


class CloseConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_closes_active_session_owned_by_caller(self):
        doc = {"sessionId": "s1", "empresaId": 42, "usuarioId": 9, "status": "active"}
        db = type("Database", (), {"conversations": FakeConversationsCollection([doc])})()

        with patch.object(short_term, "get_mongo_db", return_value=db):
            closed = await short_term.close_conversation("s1", 42, 9)

        self.assertTrue(closed)

    async def test_returns_false_when_not_owner(self):
        doc = {"sessionId": "s1", "empresaId": 99, "usuarioId": 9, "status": "active"}
        db = type("Database", (), {"conversations": FakeConversationsCollection([doc])})()

        with patch.object(short_term, "get_mongo_db", return_value=db):
            closed = await short_term.close_conversation("s1", 42, 9)

        self.assertFalse(closed)

    async def test_returns_false_when_already_closed(self):
        doc = {"sessionId": "s1", "empresaId": 42, "usuarioId": 9, "status": "closed"}
        db = type("Database", (), {"conversations": FakeConversationsCollection([doc])})()

        with patch.object(short_term, "get_mongo_db", return_value=db):
            closed = await short_term.close_conversation("s1", 42, 9)

        self.assertFalse(closed)


class GetConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_none_when_session_not_found(self):
        db = type("Database", (), {"conversations": FakeConversationsCollection([])})()

        with patch.object(short_term, "get_mongo_db", return_value=db):
            result = await short_term.get_conversation("s1", 42, 9)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
