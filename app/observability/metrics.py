"""
Observability — per-execution metrics collection.
Writes to MongoDB (metrics) and computes cost estimates.

Note: the dict keys/values returned by get_metrics_summary() (e.g.
"periodo_dias", "custo_total_usd", "qualidade", "escalamento") are the
GET /metrics/summary API contract, asserted by tests — they are kept in
Portuguese, not translated as part of this pass.
"""
import time
from datetime import datetime, timezone
from typing import Any

from app.database.mongo import get_mongo_db
from config.settings import get_settings


async def record_execution_metrics(
    session_id: str,
    conversation_id: Any,
    agent: str,
    skill: str | None,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_s: float,
    judge_score: float | None = None,
    empresa_id: int | None = None,
    status: str = "completed",
    node_latencies_ms: dict[str, float] | None = None,
) -> None:
    """
    Persists execution metrics to MongoDB.
    """
    if isinstance(empresa_id, bool) or not isinstance(empresa_id, int) or empresa_id < 1:
        raise ValueError("empresa_id must be a positive integer.")

    db = get_mongo_db()
    settings = get_settings()

    # On the Groq free tier, default rates are zero. For academic comparison,
    # the team can configure explicit rates in the environment without changing the code.
    cost_input = (input_tokens / 1_000_000) * settings.llm_input_cost_per_million_usd
    cost_output = (output_tokens / 1_000_000) * settings.llm_output_cost_per_million_usd
    estimated_cost = round(cost_input + cost_output, 6)

    await db.metrics.insert_one({
        "empresaId": empresa_id,
        "sessionId": session_id,
        "conversationId": conversation_id,
        "agent": agent,
        "skill": skill,
        "model": model,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "latency": round(latency_s, 4),
        "estimatedCost": estimated_cost,
        "costReference": {
            "inputPerMillionUsd": settings.llm_input_cost_per_million_usd,
            "outputPerMillionUsd": settings.llm_output_cost_per_million_usd,
        },
        "judgeScore": judge_score,
        "status": status,
        "nodeLatenciesMs": node_latencies_ms or {},
        "createdAt": datetime.now(timezone.utc),
    })


async def get_metrics_summary(empresa_id: int, days: int = 7) -> dict:
    """
    Returns a metrics summary + cost estimate for 100/1000 weekly users.
    Required by the course's observability/SRE requirement.
    """
    from datetime import timedelta
    db = get_mongo_db()
    since = datetime.now(timezone.utc) - timedelta(days=days)

    metrics = await db.metrics.find({"empresaId": empresa_id, "createdAt": {"$gte": since}}).to_list(length=100000)
    if not metrics:
        return _empty_summary()

    total_requests = len(metrics)
    total_input = sum(m.get("inputTokens") or 0 for m in metrics)
    total_output = sum(m.get("outputTokens") or 0 for m in metrics)
    total_cost = sum(m.get("estimatedCost") or 0 for m in metrics)
    latencies = [m["latency"] for m in metrics if m.get("latency")]
    judge_scores = [m["judgeScore"] for m in metrics if m.get("judgeScore") is not None]

    # Per agent: individualized volume, latency, errors and quality (Judge score) —
    # lets you pinpoint which agent concentrates slowness, failures or rejected responses.
    by_agent: dict[str, dict] = {}
    for m in metrics:
        agent = m.get("agent", "unknown")
        bucket = by_agent.setdefault(
            agent, {"requests": 0, "total_latency": 0.0, "errors": 0, "judge_scores": []}
        )
        bucket["requests"] += 1
        bucket["total_latency"] += m.get("latency") or 0
        if m.get("status") == "error":
            bucket["errors"] += 1
        judge_score = m.get("judgeScore")
        if judge_score is not None:
            bucket["judge_scores"].append(judge_score)
            if judge_score < 0.7:
                bucket["errors"] += 1

    agent_stats = {
        agent: {
            "requests": v["requests"],
            "avg_latency_s": round(v["total_latency"] / v["requests"], 3) if v["requests"] else 0,
            "error_rate_pct": round((v["errors"] / v["requests"]) * 100, 2) if v["requests"] else 0,
            "judge_avg_score": round(sum(v["judge_scores"]) / len(v["judge_scores"]), 3)
            if v["judge_scores"] else None,
        }
        for agent, v in by_agent.items()
    }

    # Error: pipeline exceptions/rejections or a response rejected by the Judge.
    low_quality = sum(1 for s in judge_scores if s < 0.7)
    failed_requests = sum(1 for m in metrics if m.get("status") == "error")
    error_count = failed_requests + low_quality
    error_rate_pct = round((error_count / total_requests * 100), 2) if total_requests else 0

    node_totals: dict[str, list[float]] = {}
    for metric in metrics:
        for node, duration in (metric.get("nodeLatenciesMs") or {}).items():
            node_totals.setdefault(node, []).append(duration)
    interagent_latency = {node: round(sum(values) / len(values), 2) for node, values in node_totals.items()}

    # A resolution is a completed response approved by the Judge.
    resolved_requests = sum(
        1 for m in metrics
        if m.get("status") == "completed" and (m.get("judgeScore") or 0) >= 0.7
    )
    cost_per_resolution = total_cost / resolved_requests if resolved_requests else None

    # Scaling projection for 100/1000 weekly users
    cost_per_request = total_cost / total_requests if total_requests else 0
    avg_sessions_per_user = max(1, total_requests // max(1, days * 10))

    scale_100_users = {
        "weekly_requests": 100 * avg_sessions_per_user,
        "estimated_cost_usd": round(cost_per_request * 100 * avg_sessions_per_user, 4),
    }
    scale_1000_users = {
        "weekly_requests": 1000 * avg_sessions_per_user,
        "estimated_cost_usd": round(cost_per_request * 1000 * avg_sessions_per_user, 4),
    }

    # ROI: assuming each avoided disposal action = R$ 15 average savings
    alerts_resolved = await db.agent_executions.count_documents({
        "empresaId": empresa_id,
        "createdAt": {"$gte": since},
        "agent": "motor_preditivo",
        "status": "completed",
    })
    roi_estimate = alerts_resolved * 15.0  # R$ per avoided action

    return {
        "periodo_dias": days,
        "total_requests": total_requests,
        "tokens": {"input": total_input, "output": total_output},
        "custo_total_usd": round(total_cost, 4),
        "resolucoes_aprovadas": resolved_requests,
        "custo_por_resolucao_usd": round(cost_per_resolution, 6) if cost_per_resolution is not None else None,
        "latencia": {
            "avg_s": round(sum(latencies) / len(latencies), 3) if latencies else 0,
            "p50_s": _percentile(latencies, 50),
            "p95_s": _percentile(latencies, 95),
            "max_s": round(max(latencies), 3) if latencies else 0,
            "min_s": round(min(latencies), 3) if latencies else 0,
        },
        "qualidade": {
            "judge_avg_score": round(sum(judge_scores) / len(judge_scores), 3) if judge_scores else None,
            "respostas_baixa_qualidade": low_quality,
            "execucoes_com_erro": failed_requests,
            "indice_erros_pct": error_rate_pct,
        },
        "latencia_interagentes_ms": interagent_latency,
        "agentes": agent_stats,
        "escalamento": {
            "100_usuarios_semanais": scale_100_users,
            "1000_usuarios_semanais": scale_1000_users,
        },
        "roi": {
            "acoes_motor_preditivo": alerts_resolved,
            "economia_estimada_brl": roi_estimate,
            "custo_operacao_usd": round(total_cost, 4),
        },
    }


def _percentile(values: list[float], pct: int) -> float:
    """Percentile via linear interpolation (simplified nearest-rank), no extra dependencies."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (pct / 100) * (len(ordered) - 1)
    lower, upper = int(rank), min(int(rank) + 1, len(ordered) - 1)
    weight = rank - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * weight, 3)


def _empty_summary() -> dict:
    return {
        "periodo_dias": 7,
        "total_requests": 0,
        "message": "Nenhuma métrica registrada ainda.",
    }
