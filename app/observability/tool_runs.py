"""Persistence of individual tool/data-access calls made by a chat agent node.

Complements agent_executions (one document per whole node) with one document
per tool call inside that node — which Postgres query, which RAG lookup, how
long it took, whether it failed. The audit that prompted this found agents
making 4-6 sequential I/O calls per chat message with nothing recording that
shape; agent_executions' single node_latencies_ms number can't show it.

Nodes have no access to FastAPI's BackgroundTasks (that only exists in the
route handler), so no Mongo write happens from inside the graph: each node
wraps its own tool calls with timed_tool_call(), accumulating plain dicts in
its own local list and returning them in its `{**state, ...}` result, the
same way `sources` and `node_latencies_ms` already accumulate across nodes.
record_tool_runs() is dispatched once, after the whole graph has returned,
via background_tasks.add_task in interfaces/api/main.py — see
record_agent_execution's docstring for why that call carries no try/except.
"""
import json
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Awaitable, TypeVar

from app.database.mongo import get_mongo_db

T = TypeVar("T")


def _as_object(value: Any, key: str = "value") -> dict | None:
    """input/output/error are bsonType object|null in tool_runs' $jsonSchema.

    Postgres tool results carry types BSON cannot encode natively —
    get_kpis() returns Decimal, several tools return datetime — which made
    every dono/funcionario/motor_preditivo tool_runs insert raise
    bson.errors.InvalidDocument. Because insert_many() runs inside a
    background task with no try/except (by design, see record_tool_runs),
    that failure was invisible: the chat response still came back 200 OK,
    and nothing but a swallowed exception marked the write as lost.
    json.dumps(value, default=str) is the exact same escape hatch already
    used to embed this same row data into the agents' own prompts
    (funcionario.py/dono.py/motor_preditivo.py all build their LLM context
    with it) — round-tripping through it here turns Decimal/datetime/date
    into plain strings, the same lossy-but-safe conversion already trusted
    elsewhere in this codebase, before anything reaches pymongo.

    Also wraps anything that isn't already a dict or None so a tool that
    returns a list, a string, or a number doesn't fail the object|null
    constraint on its own.
    """
    if value is None:
        return None
    safe = json.loads(json.dumps(value, default=str, ensure_ascii=False))
    return safe if isinstance(safe, dict) else {key: safe}


async def timed_tool_call(
    tool_runs: list[dict], tool: str, call: Awaitable[T], *, input: dict | None = None,
) -> T:
    """
    Awaits `call`, appending one raw tool-run dict to `tool_runs` on both
    success and failure, then re-raising the original exception unchanged —
    a node's own error handling (e.g. motor_preditivo's try/except around the
    weather call) sees exactly the same exception it does today, it just also
    gets a tool_runs entry with status="error".

    started_at/finished_at are wall-clock timestamps taken here, at the
    moment of the real call — not later, when record_tool_runs() flushes the
    batch — otherwise every tool_runs document from one request would carry
    the same timestamp (whenever the background task happened to run) and
    the collection's startedAt-ordered indexes would be meaningless.
    """
    started_at = datetime.now(timezone.utc)
    started = perf_counter()
    try:
        result = await call
    except Exception as exc:
        tool_runs.append({
            "tool": tool, "status": "error",
            "input": _as_object(input), "output": None,
            "error": _as_object(str(exc), key="message"),
            "latency": round(perf_counter() - started, 4),
            "startedAt": started_at, "finishedAt": datetime.now(timezone.utc),
        })
        raise
    tool_runs.append({
        "tool": tool, "status": "success",
        "input": _as_object(input), "output": _as_object(result),
        "error": None,
        "latency": round(perf_counter() - started, 4),
        "startedAt": started_at, "finishedAt": datetime.now(timezone.utc),
    })
    return result


async def record_tool_runs(*, conversation_id: object, agent: str, tool_runs: list[dict]) -> None:
    """One insert_many for everything a request's agent node accumulated.
    No-op (no Mongo call at all) when tool_runs is empty — e.g. a request
    guardrail_entrada rejected before any agent ran. No try/except: same
    contract as record_agent_execution, relies on background_tasks.add_task
    to keep a write failure from ever reaching the client.
    """
    if not tool_runs:
        return
    db = get_mongo_db()
    await db.tool_runs.insert_many([
        {"conversationId": conversation_id, "agent": agent, **run}
        for run in tool_runs
    ])
