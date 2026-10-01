"""Persistence and lifecycle of conversation sessions.

Note: the SessionExpiredError/SessionOwnershipError messages below are
user-facing — interfaces/api/main.py returns str(exc) directly as the HTTP
error detail, so they are kept in Portuguese like the rest of the chat UX.
"""
from datetime import datetime, timedelta, timezone

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.config import get_settings
from app.database.mongo import get_mongo_db


class SessionExpiredError(Exception):
    """The session exists, but exceeded the allowed inactivity period."""


class SessionOwnershipError(Exception):
    """The session does not belong to the given company or user."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _expiration_cutoff() -> datetime:
    settings = get_settings()
    return _utcnow() - timedelta(minutes=settings.session_timeout_minutes)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def load_history(session_id: str, limit: int = 20) -> list[BaseMessage]:
    """
    Loads the latest messages of a session as LangChain messages, oldest first.

    Sorts descending and reverses, rather than sorting ascending: `limit` is
    applied by the database, so an ascending sort would hand back the *first*
    `limit` messages of the conversation and never the recent ones. Past that
    many messages the agent's context froze at the opening of the conversation
    and nothing said afterwards could reach it — the caller asks for the latest
    (supervisor.py uses 20, GET /chat/history uses 50), so the newest end is
    what has to survive the limit. The reverse restores chronological order,
    which is what the agents and the history endpoint render.
    """
    db = get_mongo_db()
    conv = await db.conversations.find_one({"sessionId": session_id})
    if not conv:
        return []

    cursor = db.messages.find(
        {"conversationId": conv["_id"]}, sort=[("createdAt", -1)]
    ).limit(limit)
    messages: list[BaseMessage] = []
    for doc in reversed([doc async for doc in cursor]):
        content = doc.get("content", "")
        if doc.get("role") == "user":
            messages.append(HumanMessage(content=content))
        elif doc.get("role") == "assistant":
            messages.append(AIMessage(content=content))
        elif doc.get("role") == "system":
            messages.append(SystemMessage(content=content))
    return messages


async def get_recent_vision_analyses(session_id: str, limit: int = 3) -> list[dict]:
    """
    Compact summary of the most recent shelf-photo analyses
    (POST /shelf/analyze, app/agents/visao.py) taken during this session,
    newest first. Lets the Employee Agent's chat responses reference a
    photo the user just took without re-describing the whole raw
    vision_result blob (produtos_detectados, cruzamento_inventario, etc.)
    back into every prompt.
    """
    db = get_mongo_db()
    cursor = db.ai_results.find(
        {"sessionId": session_id, "agent": "visao"},
        sort=[("createdAt", -1)],
    ).limit(limit)

    summaries = []
    async for doc in cursor:
        result = doc.get("result", {})
        summaries.append({
            "quando": doc.get("createdAt"),
            "estado_geral": result.get("estado_geral"),
            "ocupacao_pct": result.get("ocupacao_pct"),
            "produtos_detectados": len(result.get("produtos_detectados") or []),
            "acoes_sugeridas": result.get("acoes_sugeridas", []),
        })
    return summaries


async def save_message(
    session_id: str,
    role: str,
    content: str,
    agent: str | None = None,
    skill: str | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    sources: list[dict] | None = None,
) -> None:
    """Persists the message and renews the active session's last interaction."""
    db = get_mongo_db()
    conv = await db.conversations.find_one({"sessionId": session_id, "status": "active"})
    if not conv:
        return

    now = _utcnow()
    await db.messages.insert_one(
        {
            "empresaId": conv["empresaId"], "conversationId": conv["_id"], "role": role, "content": content,
            "agent": agent, "skill": skill, "model": model, "sources": sources or [],
            "inputTokens": input_tokens, "outputTokens": output_tokens, "createdAt": now,
        }
    )
    await db.conversations.update_one(
        {"_id": conv["_id"]}, {"$set": {"lastInteraction": now, "agent": agent or conv.get("agent")}}
    )


async def get_or_create_conversation(
    session_id: str, empresa_id: int, usuario_id: int, agent: str = "pending"
) -> dict:
    """Creates or resumes an active session, validating ownership and inactivity."""
    db = get_mongo_db()
    now = _utcnow()
    conv = await db.conversations.find_one({"sessionId": session_id})

    if conv:
        if conv["empresaId"] != empresa_id or conv["usuarioId"] != usuario_id:
            raise SessionOwnershipError("A sessão não pertence ao usuário ou empresa informados.")
        if conv.get("status") != "active":
            raise SessionExpiredError("A sessão foi encerrada. Inicie uma nova conversa.")
        if _as_utc(conv.get("lastInteraction", conv["startedAt"])) < _expiration_cutoff():
            await db.conversations.update_one(
                {"_id": conv["_id"]}, {"$set": {"status": "expired", "endedAt": now}}
            )
            raise SessionExpiredError("A sessão expirou por inatividade. Inicie uma nova conversa.")
        return conv

    document = {
        "sessionId": session_id, "empresaId": empresa_id, "usuarioId": usuario_id,
        "agent": agent, "skill": None, "status": "active", "title": None,
        "startedAt": now, "lastInteraction": now, "endedAt": None,
    }
    await db.conversations.insert_one(document)
    return await db.conversations.find_one({"sessionId": session_id})


async def list_conversations(empresa_id: int, usuario_id: int, limit: int = 20) -> list[dict]:
    """Lists the user's most recent sessions, without exposing messages."""
    db = get_mongo_db()
    cursor = db.conversations.find(
        {"empresaId": empresa_id, "usuarioId": usuario_id},
        {"_id": 0, "sessionId": 1, "agent": 1, "status": 1, "title": 1, "startedAt": 1, "lastInteraction": 1, "endedAt": 1},
    ).sort("lastInteraction", -1).limit(min(limit, 100))
    return [doc async for doc in cursor]


async def get_conversation(session_id: str, empresa_id: int, usuario_id: int) -> dict | None:
    """Gets the session for its legitimate owner, even if already closed."""
    db = get_mongo_db()
    conv = await db.conversations.find_one({"sessionId": session_id})
    if not conv:
        return None
    if conv["empresaId"] != empresa_id or conv["usuarioId"] != usuario_id:
        raise SessionOwnershipError("A sessão não pertence ao usuário ou empresa informados.")
    return conv


async def close_conversation(session_id: str, empresa_id: int, usuario_id: int) -> bool:
    """Closes a session belonging to the user; messages remain for audit purposes."""
    db = get_mongo_db()
    result = await db.conversations.update_one(
        {"sessionId": session_id, "empresaId": empresa_id, "usuarioId": usuario_id, "status": "active"},
        {"$set": {"status": "closed", "endedAt": _utcnow()}},
    )
    return result.modified_count == 1
