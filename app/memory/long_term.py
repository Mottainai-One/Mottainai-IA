"""
Memória de longo prazo — preferências e fatos sobre o usuário.
Persiste em MongoDB (coleção: memories) com chave única (empresaId, usuarioId).
Permite que os agentes personalizem respostas com base no histórico do usuário.
"""
from datetime import datetime, timezone

from app.database.mongo import get_mongo_db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def load_memory(empresa_id: int, usuario_id: int) -> dict:
    """
    Retorna a memória de longo prazo do usuário.
    Inclui: preferences, facts, lastAgent, lastSkill.
    """
    db = get_mongo_db()
    mem = await db.memories.find_one({"empresaId": empresa_id, "usuarioId": usuario_id})
    if not mem:
        return {"preferences": [], "facts": [], "lastAgent": None, "lastSkill": None}
    return {
        "preferences": mem.get("preferences") or [],
        "facts": mem.get("facts") or [],
        "lastAgent": mem.get("lastAgent"),
        "lastSkill": mem.get("lastSkill"),
    }


async def update_memory(
    empresa_id: int,
    usuario_id: int,
    *,
    new_preference: str | None = None,
    new_fact: str | None = None,
    last_agent: str | None = None,
    last_skill: str | None = None,
) -> None:
    """
    Atualiza memória do usuário.
    Usa $addToSet para não duplicar preferências/fatos.
    """
    db = get_mongo_db()
    now = _utcnow()

    update: dict = {"$set": {"updatedAt": now}}

    if last_agent:
        update["$set"]["lastAgent"] = last_agent
    if last_skill:
        update["$set"]["lastSkill"] = last_skill
    if new_preference:
        update["$addToSet"] = update.get("$addToSet", {})
        update["$addToSet"]["preferences"] = new_preference
    if new_fact:
        update["$addToSet"] = update.get("$addToSet", {})
        update["$addToSet"]["facts"] = new_fact

    await db.memories.update_one(
        {"empresaId": empresa_id, "usuarioId": usuario_id},
        {**update, "$setOnInsert": {"empresaId": empresa_id, "usuarioId": usuario_id}},
        upsert=True,
    )


def format_memory_for_prompt(memory: dict) -> str:
    """
    Formata a memória de longo prazo como texto para injetar no system prompt.
    """
    lines: list[str] = []
    if memory["preferences"]:
        lines.append(f"Preferências do usuário: {', '.join(memory['preferences'])}")
    if memory["facts"]:
        lines.append(f"Informações conhecidas sobre o usuário: {', '.join(memory['facts'])}")
    if memory["lastAgent"]:
        lines.append(f"Último agente que atendeu: {memory['lastAgent']}")
    if memory["lastSkill"]:
        lines.append(f"Última skill usada: {memory['lastSkill']}")
    return "\n".join(lines) if lines else "Nenhuma memória prévia registrada."
