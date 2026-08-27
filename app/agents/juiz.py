"""
Judge Agent — hallucination control and response quality.
Required by the course's anti-hallucination requirement.

Sub-agents/capabilities:
  1. Grounding Check: is the response supported by the sources?
  2. Scope/Leak Check: does the response contain data the profile shouldn't see?
  3. Confidence Score: 0.0 to 1.0
  4. Fallback Handler: if rejected, rewrite or return a safe message

Runs as a graph node right after the domain agent, BEFORE responding to the user.

Note: JUDGE_PROMPT (and the evaluation input built from it) is deliberately
kept in Portuguese, like the other agents' SYSTEM_PROMPT — it's part of the
product's tuned behavior, not developer-facing code.

Note: the judge's own confidence score is not fully deterministic even at
temperature 0 — observed live, the exact same (grounded, correct) response
scored 0.35 on one evaluation and 0.86 on an identical retry. Rather than
raising the approval threshold's tolerance for that noise, a rejected
evaluation gets exactly one independent re-evaluation before falling back
to a safe message (see _run_judge_evaluation below) — this only adds a
second LLM call on the rejection path, not on every message.
"""
import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.runtime import MottainaiState, get_llm

logger = logging.getLogger(__name__)

JUDGE_PROMPT = """Você é o Agente Juiz do Mottainai — responsável pela qualidade e veracidade das respostas.

Analise a resposta do agente de domínio e avalie:

1. GROUNDING: A resposta está baseada nas fontes/dados fornecidos no contexto?
   - Se o agente inventou informações não presentes no contexto, REPROVAR.

2. ESCOPO: A resposta contém informações que o perfil do usuário não deveria ver, ou responde a um assunto fora do Mottainai?
   - Cliente não deve ver: dados de estoque, inventário, dados de outros usuários.
   - Funcionário não deve ver: dados financeiros consolidados sem autorização.
   - Qualquer pergunta sem relação com o Mottainai (cultura geral, ciência, notícias, matemática, outras empresas, etc.) deve ser REPROVADA por escopo, mesmo que a resposta esteja factualmente correta.

3. CONFIANÇA: Dê um score de 0.0 a 1.0.
   - >= 0.7: APROVADO
   - < 0.7: REFORMULAR ou REPROVAR

Responda SOMENTE em JSON:
{
  "approved": true/false,
  "confidence_score": 0.0-1.0,
  "grounding_ok": true/false,
  "scope_ok": true/false,
  "issues": ["lista de problemas encontrados"],
  "revised_response": "resposta corrigida (se reprovado) ou null (se aprovado)"
}
"""


async def _run_judge_evaluation(llm, messages: list) -> tuple[dict, bool]:
    """
    Runs a single Judge evaluation call.
    Returns (evaluation, judge_unavailable) — judge_unavailable is True only
    for a technical failure (bad JSON, provider error), never for a genuine
    rejection.
    """
    try:
        response = await llm.ainvoke(messages)
        # Extracts JSON from the response — strips markdown code block if present
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip()), False
    except Exception as e:
        # Fail-closed: without reliable validation, the response cannot be released.
        logger.error("Judge Agent hit an internal error: %s", e, exc_info=True)
        return {
            "approved": False,
            "confidence_score": 0.0,
            "grounding_ok": False,
            "scope_ok": False,
            "issues": [f"judge_unavailable: {type(e).__name__}"],
            "revised_response": None,
        }, True


def _approved(evaluation: dict) -> tuple[bool, float]:
    score = evaluation.get("confidence_score", 0.0)
    # Does not blindly trust the model's "approved" boolean — it has come back
    # inconsistent with its own score in real tests. The score decides.
    return evaluation.get("approved", False) and score >= 0.7, score


async def node_agente_juiz(state: MottainaiState) -> MottainaiState:
    """Judge Agent node in the LangGraph graph."""
    agent_response = state.get("agent_response", "")
    user_role = state["user_role"]
    sources = state.get("sources", [])

    # Builds the context for the judge (kept in Portuguese, see module docstring)
    sources_text = "\n".join(
        [f"- {s.get('type')}: {s.get('ref')}" for s in sources]
    ) or "Nenhuma fonte registrada."

    evaluation_input = f"""
PERFIL DO USUÁRIO: {user_role}
PERGUNTA: {state['sanitized_input']}

FONTES USADAS:
{sources_text}

RESPOSTA DO AGENTE ({state.get('selected_agent', '?')}):
{agent_response}

Avalie a resposta conforme as instruções.
"""

    messages = [
        SystemMessage(content=JUDGE_PROMPT),
        HumanMessage(content=evaluation_input),
    ]

    llm = get_llm(temperature=0.0)  # zero temperature for deterministic evaluation

    evaluation, judge_unavailable = await _run_judge_evaluation(llm, messages)
    approved, score = _approved(evaluation)

    # The judge's own score is not fully deterministic even at temperature 0
    # (see module docstring) — a single rejection isn't strong evidence the
    # response is actually bad. Give it one independent re-evaluation before
    # falling back to a safe message.
    if not approved:
        retry_evaluation, retry_unavailable = await _run_judge_evaluation(llm, messages)
        retry_approved, retry_score = _approved(retry_evaluation)
        if retry_approved or (judge_unavailable and not retry_unavailable):
            evaluation, judge_unavailable, approved, score = (
                retry_evaluation, retry_unavailable, retry_approved, retry_score,
            )

    scope_ok = evaluation.get("scope_ok", True)
    grounding_ok = evaluation.get("grounding_ok", True)

    # Rejected: never uses "revised_response" — the judge itself can rewrite
    # while still keeping out-of-scope or ungrounded content. Always falls back
    # to one of a few fixed, safe messages, chosen by the rejection reason
    # (fail-closed). These fallback strings are user-facing, kept in Portuguese.
    if not approved:
        if judge_unavailable:
            final_agent_response = "Não tenho informações suficientes para responder com segurança. Por favor, reformule sua pergunta."
        elif not scope_ok:
            final_agent_response = (
                "Posso ajudar apenas com assuntos do Mottainai — promoções, lojas, estoque, "
                "fidelidade, sustentabilidade e indicadores do negócio. Em que posso te ajudar?"
            )
        elif not grounding_ok:
            final_agent_response = (
                "Não encontrei essa informação com segurança nos dados disponíveis. "
                "Pode reformular a pergunta ou perguntar de outro jeito?"
            )
        else:
            final_agent_response = (
                "Não consigo confirmar essa resposta com segurança agora. "
                "Tente perguntar de outra forma."
            )
    else:
        final_agent_response = agent_response

    # Saves the evaluation to MongoDB (prompt_evaluations)
    from datetime import datetime, timezone

    from app.database.mongo import get_mongo_db
    db = get_mongo_db()
    await db.prompt_evaluations.insert_one({
        "empresaId": state["empresa_id"],
        "sessionId": state["session_id"],
        "promptVersion": "1.0",
        "agent": state.get("selected_agent", "unknown"),
        "skill": None,
        "score": score,
        "feedback": str(evaluation.get("issues", [])),
        "evaluator": "agente_juiz",
        "createdAt": datetime.now(timezone.utc),
    })

    return {
        **state,
        "agent_response": final_agent_response,
        "judge_approved": approved,
        "judge_score": score,
    }
