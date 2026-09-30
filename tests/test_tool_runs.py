"""Contract for app/observability/tool_runs.py — per-tool-call logging that
agent nodes accumulate and interfaces/api/main.py flushes once per request."""
import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from app.observability import tool_runs as tool_runs_module
from app.observability.tool_runs import record_tool_runs, timed_tool_call


class FakeToolRunsCollection:
    def __init__(self):
        self.inserted_many_docs = None

    async def insert_many(self, docs):
        self.inserted_many_docs = docs


class TimedToolCallSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_appends_a_success_entry_with_the_raw_result_as_output(self):
        tool_runs: list[dict] = []

        result = await timed_tool_call(
            tool_runs, "get_stock_alerts", self._coro({"alerts": 3}),
            input={"empresa_id": 1},
        )

        self.assertEqual(result, {"alerts": 3})
        self.assertEqual(len(tool_runs), 1)
        entry = tool_runs[0]
        self.assertEqual(entry["tool"], "get_stock_alerts")
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["input"], {"empresa_id": 1})
        self.assertEqual(entry["output"], {"alerts": 3})
        self.assertIsNone(entry["error"])

    async def test_wraps_a_non_dict_output_and_a_none_input(self):
        # output/input are bsonType object|null in the schema — a tool that
        # returns a string, list or tuple (retrieve_with_sources does) would
        # otherwise fail validation on insert.
        tool_runs: list[dict] = []

        await timed_tool_call(tool_runs, "format_notifications_for_agent", self._coro("texto pronto"), input=None)

        entry = tool_runs[0]
        self.assertEqual(entry["output"], {"value": "texto pronto"})
        self.assertIsNone(entry["input"])

    async def test_wraps_a_postgres_row_containing_decimal_and_datetime(self):
        # get_kpis() returns Decimal for money columns and several other
        # postgres_tools functions return raw datetime values. bson cannot
        # encode either natively — insert_many() raised inside the
        # try-less background task, invisibly, since the response had
        # already gone out 200 OK. json.dumps(default=str) is the same
        # escape hatch the agents already use to embed this exact row
        # shape into their own prompts.
        tool_runs: list[dict] = []
        row = {
            "revenue_30d": Decimal("32473.65"),
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "active_alerts": 85,
        }

        await timed_tool_call(tool_runs, "get_kpis", self._coro(row), input=None)

        output = tool_runs[0]["output"]
        # must be BSON-encodable — this is the actual failure mode: a
        # dict is not enough, its values also have to be BSON-safe.
        json.dumps(output)
        self.assertEqual(output["revenue_30d"], "32473.65")
        self.assertEqual(output["active_alerts"], 85)

    async def test_latency_is_non_negative_and_finished_after_started(self):
        tool_runs: list[dict] = []

        await timed_tool_call(tool_runs, "get_inventory_status", self._coro([]), input=None)

        entry = tool_runs[0]
        self.assertGreaterEqual(entry["latency"], 0)
        self.assertGreaterEqual(entry["finishedAt"], entry["startedAt"])

    @staticmethod
    async def _coro(value):
        return value


class TimedToolCallFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_appends_an_error_entry_and_reraises_the_original_exception(self):
        tool_runs: list[dict] = []

        async def _failing():
            raise ValueError("tenant not found")

        with self.assertRaises(ValueError):
            await timed_tool_call(tool_runs, "get_kpis", _failing(), input={"empresa_id": 1})

        entry = tool_runs[0]
        self.assertEqual(entry["status"], "error")
        self.assertEqual(entry["output"], None)
        self.assertEqual(entry["error"], {"message": "tenant not found"})

    async def test_reraise_lets_an_outer_try_except_still_catch_it(self):
        # motor_preditivo.py wraps mcp_call_weather_agent in its own
        # try/except; timed_tool_call must not swallow or change the
        # exception, or that fallback stops working.
        tool_runs: list[dict] = []

        async def _failing():
            raise TimeoutError("weather API down")

        weather_context = None
        try:
            await timed_tool_call(tool_runs, "mcp_call_weather_agent", _failing(), input=None)
        except Exception as exc:
            weather_context = f"unavailable: {exc}"

        self.assertEqual(weather_context, "unavailable: weather API down")
        self.assertEqual(tool_runs[0]["status"], "error")


class RecordToolRunsTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_list_never_touches_mongo(self):
        collection = FakeToolRunsCollection()
        db = type("Database", (), {"tool_runs": collection})()

        with patch.object(tool_runs_module, "get_mongo_db", return_value=db) as get_db:
            await record_tool_runs(conversation_id="c1", agent="dono", tool_runs=[])

        get_db.assert_not_called()
        self.assertIsNone(collection.inserted_many_docs)

    async def test_inserts_one_document_per_accumulated_run_with_conversation_and_agent(self):
        collection = FakeToolRunsCollection()
        db = type("Database", (), {"tool_runs": collection})()
        runs = [
            {"tool": "get_kpis", "status": "success", "input": None, "output": {"revenue": 1}, "error": None, "latency": 0.01, "startedAt": 1, "finishedAt": 2},
            {"tool": "get_sales_summary", "status": "error", "input": None, "output": None, "error": {"message": "x"}, "latency": 0.02, "startedAt": 3, "finishedAt": 4},
        ]

        with patch.object(tool_runs_module, "get_mongo_db", return_value=db):
            await record_tool_runs(conversation_id="c1", agent="dono", tool_runs=runs)

        docs = collection.inserted_many_docs
        self.assertEqual(len(docs), 2)
        for doc, run in zip(docs, runs):
            self.assertEqual(doc["conversationId"], "c1")
            self.assertEqual(doc["agent"], "dono")
            self.assertEqual(doc["tool"], run["tool"])
            self.assertEqual(doc["status"], run["status"])


if __name__ == "__main__":
    unittest.main()
