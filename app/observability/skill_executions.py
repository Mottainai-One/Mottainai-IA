"""Persistence of skill execution traces (app/skills/executor.py) — one
document per call through the skill contract: skill, version, tenant,
user, session, inputs/outputs, status, latency. Sits alongside tool_runs
(per data-access call inside a node) and agent_executions (per whole
node) at the same level of detail, for calls that went through a skill
instead of a direct tool call — same shape, not a new one, per this
project's own convention for these observability collections.

Reuses tool_runs.as_object for the same reason it exists there: a
skill's inputs/outputs can carry types BSON can't encode natively
(Decimal, datetime).
"""
from datetime import datetime

from app.database.mongo import get_mongo_db
from app.observability.tool_runs import as_object


async def record_skill_execution(
    *,
    empresa_id: int,
    usuario_id: int,
    session_id: str,
    skill: str,
    version: str,
    status: str,
    latency: float,
    started_at: datetime,
    finished_at: datetime,
    conversation_id: object = None,
    inputs: dict | None = None,
    outputs: object = None,
    error: str | dict | None = None,
) -> None:
    """No try/except: same contract as record_agent_execution/
    record_tool_runs — a write failure here must not be this function's
    problem to hide. app/skills/executor.py is the one place that calls
    this, and it wraps every call in its own try/except instead (see
    that module) so that a failure to *record* an execution never
    prevents the skill's actual result or error from reaching its
    caller — the two failure modes matter differently enough that they
    don't belong in the same try/except.
    """
    db = get_mongo_db()
    if isinstance(error, str):
        error = {"message": error}
    await db.skill_executions.insert_one({
        "empresaId": empresa_id,
        "usuarioId": usuario_id,
        "sessionId": session_id,
        "conversationId": conversation_id,
        "skill": skill,
        "version": version,
        "status": status,
        "input": as_object(inputs),
        "output": as_object(outputs),
        "error": error,
        "latency": round(latency, 4),
        "startedAt": started_at,
        "finishedAt": finished_at,
    })
