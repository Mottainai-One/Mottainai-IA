"""
Agente Juiz — controle de alucinações e qualidade de resposta.
Obrigatório pelo requisito de anti-alucinação da matéria.

Subagentes/capacidades:
  1. Grounding Check: a resposta é suportada pelas fontes?
  2. Escopo/Vazamento: a resposta contém dado que o perfil não deveria ver?
  3. Score de Confiança: 0.0 a 1.0
  4. Fallback Handler: se reprovado, reformula ou retorna mensagem segura

Roda como nó do grafo logo após o agente de domínio, ANTES de responder ao usuário.
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


async def node_agente_juiz(state: MottainaiState) -> MottainaiState:
    """Nó do Agente Juiz no grafo LangGraph."""
    agent_response = state.get("agent_response", "")
    user_role = state["user_role"]
    sources = state.get("sources", [])

    # Monta contexto para o juiz
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

    llm = get_llm(temperature=0.0)  # zero temperatura para avaliação determinística

    judge_unavailable = False
    try:
        response = await llm.ainvoke(messages)
        # Extrai JSON da resposta — remove markdown code block se presente
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        evaluation = json.loads(raw.strip())
    except Exception as e:
        # Fail-closed: sem validação confiável, a resposta não pode ser liberada.
        logger.error("Agente Juiz encontrou erro interno: %s", e, exc_info=True)
        judge_unavailable = True
        evaluation = {
            "approved": False,
            "confidence_score": 0.0,
            "grounding_ok": False,
            "scope_ok": False,
            "issues": [f"judge_unavailable: {type(e).__name__}"],
            "revised_response": None,
        }

    score = evaluation.get("confidence_score", 0.0)
    scope_ok = evaluation.get("scope_ok", True)
    grounding_ok = evaluation.get("grounding_ok", True)
    # Não confia cegamente no booleano "approved" do modelo — ele já veio
    # inconsistente com o próprio score em testes reais. O score decide.
    approved = evaluation.get("approved", False) and score >= 0.7

    # Reprovado: nunca usa "revised_response" — o próprio juiz pode reformular
    # mantendo conteúdo fora de escopo ou não fundamentado. Sempre cai numa
    # mensagem segura fixa, escolhida pelo motivo da reprovação (fail-closed).
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

    # Salva avaliação no MongoDB (prompt_evaluations)
    from app.database.mongo import get_mongo_db
    from datetime import datetime, timezone
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
