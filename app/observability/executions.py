"""Persistence of execution traces for audit and SRE."""
from datetime import datetime, timezone

from app.database.mongo import get_mongo_db


async def record_agent_execution(
    *, empresa_id: int, session_id: str, agent: str, status: str, latency_s: float,
    conversation_id: object = None,
    node_latencies_ms: dict[str, float] | None = None, error: str | dict | None = None,
) -> None:
    db = get_mongo_db()
    # The collection's $jsonSchema types `error` as object|null, so a plain
    # string (an exception class name, a guardrail message) fails validation
    # and makes recording a failure itself raise — losing the trace exactly
    # when it matters. Wrap it instead.
    if isinstance(error, str):
        error = {"message": error}
    await db.agent_executions.insert_one({
        "empresaId": empresa_id, "sessionId": session_id, "conversationId": conversation_id, "agent": agent,
        "status": status, "latency": round(latency_s, 4),
        "nodeLatenciesMs": node_latencies_ms or {}, "error": error,
        "startedAt": datetime.now(timezone.utc), "createdAt": datetime.now(timezone.utc),
    })
