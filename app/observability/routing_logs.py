"""Persistence of the supervisor's routing decision — one document per chat
request, complementing tool_runs (per tool call) and agent_executions (per
whole node) with which intent actually fired and how confident the match
was.

node_supervisor_route builds the routing_log dict and returns it in state;
interfaces/api/main.py flushes it once via background_tasks.add_task,
alongside record_agent_execution/record_execution_metrics/record_tool_runs.
"""
from datetime import datetime, timezone

from app.database.mongo import get_mongo_db


async def record_routing_log(
    *, conversation_id: object, intent: str, selected_agent: str,
    selected_skill: str | None = None, confidence: float | None = None,
) -> None:
    """One insert_one to routing_logs. No try/except: same contract as
    record_agent_execution, relies on background_tasks.add_task to keep a
    write failure from ever reaching the client."""
    db = get_mongo_db()
    await db.routing_logs.insert_one({
        "conversationId": conversation_id,
        "intent": intent,
        "selectedAgent": selected_agent,
        "selectedSkill": selected_skill,
        "confidence": confidence,
        "createdAt": datetime.now(timezone.utc),
    })
