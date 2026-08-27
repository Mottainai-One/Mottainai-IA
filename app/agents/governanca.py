"""
Governance/Audit Agent — runs asynchronously/periodically.
Does NOT block the response to the user.

Sub-agents/capabilities:
  1. Execution Audit (agent_executions + tool_runs)
  2. Access and Scope Control (agent_policies)
  3. Decision Traceability (response_explanations)
  4. Compliance Report (feeds the Owner Agent)

Note: the returned dict keys/values (e.g. "total_execucoes", "periodo",
"custo_estimado_usd") are the API's data contract, asserted by tests and
consumed by GET /audit/report — they are kept in Portuguese, not translated
as part of this pass.
"""
from datetime import datetime, timezone, timedelta
from typing import Any

from app.database.mongo import get_mongo_db
from config.settings import get_settings


async def _tenant_conversation_references(db: Any, empresa_id: int) -> tuple[list[Any], list[str]]:
    """Gets references that allow safely attributing legacy data to the tenant."""
    conversation_ids: list[Any] = []
    session_ids: list[str] = []
    cursor = db.conversations.find(
        {"empresaId": empresa_id},
        {"_id": 1, "sessionId": 1},
    )
    async for conversation in cursor:
        if "_id" in conversation:
            conversation_ids.append(conversation["_id"])
        if isinstance(conversation.get("sessionId"), str):
            session_ids.append(conversation["sessionId"])
    return conversation_ids, session_ids


def _tenant_scoped_filter(
    *,
    empresa_id: int,
    since: datetime,
    timestamp_field: str,
    conversation_ids: list[Any],
    session_ids: list[str],
) -> dict[str, Any]:
    """Filters tenant data, with a safe fallback for linked legacy records."""
    legacy_links: list[dict[str, Any]] = []
    if conversation_ids:
        legacy_links.append({"conversationId": {"$in": conversation_ids}})
    if session_ids:
        legacy_links.append({"sessionId": {"$in": session_ids}})

    tenant_or_legacy: list[dict[str, Any]] = [{"empresaId": empresa_id}]
    if legacy_links:
        tenant_or_legacy.append({
            "$and": [
                {"$or": [
                    {"empresaId": {"$exists": False}},
                    {"empresaId": 0},
                ]},
                {"$or": legacy_links},
            ]
        })

    return {
        timestamp_field: {"$gte": since},
        "$or": tenant_or_legacy,
    }


async def run_auditoria_execucoes(empresa_id: int) -> dict[str, Any]:
    """
    Audits agent executions in the last 24h.
    Detects: high latency, error rate, agents not approved by the Judge.
    """
    db = get_mongo_db()
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    conversation_ids, session_ids = await _tenant_conversation_references(db, empresa_id)
    execution_filter = _tenant_scoped_filter(
        empresa_id=empresa_id,
        since=since,
        timestamp_field="startedAt",
        conversation_ids=conversation_ids,
        session_ids=session_ids,
    )

    total = await db.agent_executions.count_documents(execution_filter)
    errors = await db.agent_executions.count_documents({**execution_filter, "status": "error"})

    # Average latency
    pipeline = [
        {"$match": {**execution_filter, "latency": {"$ne": None}}},
        {"$group": {"_id": "$agent", "avg_latency": {"$avg": "$latency"}, "count": {"$sum": 1}}},
    ]
    latency_by_agent = []
    async for doc in db.agent_executions.aggregate(pipeline):
        latency_by_agent.append(doc)

    # Judge evaluations below 0.7
    low_confidence = await db.prompt_evaluations.count_documents(
        {
            "empresaId": empresa_id,
            "createdAt": {"$gte": since},
            "score": {"$lt": 0.7},
        }
    )

    error_rate = (errors / total * 100) if total > 0 else 0.0

    return {
        "periodo": "últimas 24h",
        "total_execucoes": total,
        "execucoes_com_erro": errors,
        "taxa_erro_pct": round(error_rate, 2),
        "latencia_por_agente": latency_by_agent,
        "respostas_baixa_confianca": low_confidence,
        "status": "ALERTA" if error_rate > 10 or low_confidence > 5 else "OK",
    }


async def run_controle_acesso(empresa_id: int, agent: str, action: str) -> dict[str, Any]:
    """
    Checks whether the agent respected its agent_policy registered in MongoDB.
    """
    db = get_mongo_db()
    policy = await db.agent_policies.find_one({"agent": agent, "empresaId": empresa_id})
    if not policy:
        return {"checked": False, "reason": f"Nenhuma política encontrada para '{agent}'"}

    allowed_domains = policy.get("scope", {}).get("allowedDomains", [])
    forbidden_domains = policy.get("scope", {}).get("forbiddenDomains", [])

    violation = any(fd in action.lower() for fd in forbidden_domains) if forbidden_domains else False

    if violation:
        # Records the violation
        await db.conversation_events.insert_one({
            "empresaId": empresa_id,
            "conversationId": None,
            "type": "error",
            "payload": {
                "agent": agent,
                "action": action,
                "violation": "acesso a domínio proibido",
                "policy_id": str(policy.get("_id")),
            },
            "createdAt": datetime.now(timezone.utc),
        })

    return {
        "agent": agent,
        "policy_checked": True,
        "violation_detected": violation,
        "allowed_domains": allowed_domains,
    }


async def run_relatorio_conformidade(empresa_id: int) -> dict[str, Any]:
    """
    Generates the compliance report for the Owner Agent.
    """
    db = get_mongo_db()
    since_7d = datetime.now(timezone.utc) - timedelta(days=7)
    conversation_ids, session_ids = await _tenant_conversation_references(db, empresa_id)
    report_filter = _tenant_scoped_filter(
        empresa_id=empresa_id,
        since=since_7d,
        timestamp_field="createdAt",
        conversation_ids=conversation_ids,
        session_ids=session_ids,
    )

    total_conversations = await db.conversations.count_documents({
        "empresaId": empresa_id,
        "startedAt": {"$gte": since_7d},
    })
    total_messages = await db.messages.count_documents(report_filter)

    positive_feedbacks = await db.feedbacks.count_documents({
        **report_filter,
        "rating": "positive",
    })
    negative_feedbacks = await db.feedbacks.count_documents({
        **report_filter,
        "rating": "negative",
    })

    metrics = await db.metrics.find(report_filter).to_list(length=10000)
    total_input_tokens = sum(m.get("inputTokens") or 0 for m in metrics)
    total_output_tokens = sum(m.get("outputTokens") or 0 for m in metrics)
    settings = get_settings()
    estimated_cost_usd = (
        total_input_tokens / 1_000_000 * settings.llm_input_cost_per_million_usd
        + total_output_tokens / 1_000_000 * settings.llm_output_cost_per_million_usd
    )

    return {
        "periodo": "últimos 7 dias",
        "empresa_id": empresa_id,
        "total_conversas": total_conversations,
        "total_mensagens": total_messages,
        "feedbacks": {"positive": positive_feedbacks, "negative": negative_feedbacks},
        "tokens": {"input": total_input_tokens, "output": total_output_tokens},
        "custo_estimado_usd": round(estimated_cost_usd, 4),
        "metodologia_custo": "groq_free_tier" if estimated_cost_usd == 0 else "projecao_configurada",
        "gerado_em": datetime.now(timezone.utc).isoformat(),
    }
