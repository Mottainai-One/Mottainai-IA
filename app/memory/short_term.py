"""Persistência e ciclo de vida das sessões de conversa."""
from datetime import datetime, timedelta, timezone

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.config import get_settings
from app.database.mongo import get_mongo_db


class SessionExpiredError(Exception):
    """A sessão existe, mas excedeu o período permitido de inatividade."""


class SessionOwnershipError(Exception):
    """A sessão não pertence à empresa ou usuário informado."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _expiration_cutoff() -> datetime:
    settings = get_settings()
    return _utcnow() - timedelta(minutes=settings.session_timeout_minutes)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def load_history(session_id: str, limit: int = 20) -> list[BaseMessage]:
    """Carrega as últimas mensagens de uma sessão como mensagens LangChain."""
    db = get_mongo_db()
    conv = await db.conversations.find_one({"sessionId": session_id})
    if not conv:
        return []

    cursor = db.messages.find(
        {"conversationId": conv["_id"]}, sort=[("createdAt", 1)]
    ).limit(limit)
    messages: list[BaseMessage] = []
    async for doc in cursor:
        content = doc.get("content", "")
        if doc.get("role") == "user":
            messages.append(HumanMessage(content=content))
        elif doc.get("role") == "assistant":
            messages.append(AIMessage(content=content))
        elif doc.get("role") == "system":
            messages.append(SystemMessage(content=content))
    return messages


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
    """Persiste mensagem e renova a última interação da sessão ativa."""
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
    """Cria ou retoma uma sessão ativa, validando ownership e inatividade."""
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
    """Lista as sessões mais recentes do usuário, sem expor mensagens."""
    db = get_mongo_db()
    cursor = db.conversations.find(
        {"empresaId": empresa_id, "usuarioId": usuario_id},
        {"_id": 0, "sessionId": 1, "agent": 1, "status": 1, "title": 1, "startedAt": 1, "lastInteraction": 1, "endedAt": 1},
    ).sort("lastInteraction", -1).limit(min(limit, 100))
    return [doc async for doc in cursor]


async def get_conversation(session_id: str, empresa_id: int, usuario_id: int) -> dict | None:
    """Obtém sessão para o dono legítimo, inclusive se já encerrada."""
    db = get_mongo_db()
    conv = await db.conversations.find_one({"sessionId": session_id})
    if not conv:
        return None
    if conv["empresaId"] != empresa_id or conv["usuarioId"] != usuario_id:
        raise SessionOwnershipError("A sessão não pertence ao usuário ou empresa informados.")
    return conv


async def close_conversation(session_id: str, empresa_id: int, usuario_id: int) -> bool:
    """Encerra uma sessão do próprio usuário; mensagens permanecem para auditoria."""
    db = get_mongo_db()
    result = await db.conversations.update_one(
        {"sessionId": session_id, "empresaId": empresa_id, "usuarioId": usuario_id, "status": "active"},
        {"$set": {"status": "closed", "endedAt": _utcnow()}},
    )
    return result.modified_count == 1
