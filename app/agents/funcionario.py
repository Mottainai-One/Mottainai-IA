"""
Employee Agent — serves operators, stock clerks and managers.
Skills: System manual, procedures, stock, inventory, goods receiving, alerts.
Uses Postgres (read) + Redis (notifications) + local RAG. Writes memory.

Registering a receipt or a disposal is a write action, so it is deliberately
NOT something this chat node decides to do on its own from free text — it
goes through the dedicated, role-gated endpoints
(POST /funcionario/receber-mercadoria, POST /funcionario/descartar-lote,
see interfaces/api/main.py), which call app.tools.postgres_tools directly.
The agent's job here is only to tell the employee that these actions exist
and how to use them, matching every other agent's node in the graph, which
gathers data and narrates — it doesn't independently take action against
the database.

Also reads recent shelf-photo analyses (POST /shelf/analyze,
app/agents/visao.py) taken during the same session — that endpoint has
always persisted its result tagged with sessionId (see visao.py's own
docstring: "The result feeds the Employee Agent"), but until now nothing
ever actually read it back. This is the one node in the graph that talks
to the employee in text, so it is the natural place to surface it.

Note: SYSTEM_PROMPT and the operational context block fed to the LLM are
deliberately kept in Portuguese, same as the other agents.

Two code paths, chosen by settings.native_tool_calling_enabled (off by
default — see config/settings.py):
- _legacy (today's behavior): always fetches every data source below,
  then hands the LLM one finished prompt with all of it inlined.
- _native: exposes the same read-only queries as LangChain tools
  (app/agents/tools_bridge.py) and lets the model decide which ones it
  actually needs for a given question. get_inbox and the vision recap
  are NOT in that toolkit (see tools_bridge.py) and stay always-fetched
  context in both paths — the plan this shipped from names exactly
  eight read-only tools across both agents, and neither of those two
  is among them.
"""
import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.runtime import MottainaiState, gather_or_raise, get_llm, run_agent_with_tools
from app.agents.tools_bridge import build_toolkit
from app.memory.long_term import format_memory_for_prompt
from app.memory.short_term import get_recent_vision_analyses
from app.observability.tool_runs import timed_tool_call
from app.rag.retriever import retrieve_with_sources
from app.tools.postgres_tools import (
    get_expiring_batches,
    get_inventory_status,
    get_stock_alerts,
    guarded,
)
from app.tools.redis_tools import format_notifications_for_agent, get_inbox
from config.settings import get_settings

# Every other block in the operational context is bounded — alerts and
# notifications by their query's `limit`, vision analyses by limit=3,
# inventory by a [:10] slice — but the expiring batches were dumped whole,
# so the prompt grew with the number of batches near expiry. At 20 batches
# that block alone was ~1.4k tokens and pushed the request to 8120 against
# Groq's 8000 TPM ceiling, so the agent answered HTTP 503 to *every*
# question, not just ones about batches. get_expiring_batches() already
# sorts by expiration_date ASC, so the head of the list is the urgent end.
# Same cap, same reason, as the Predictive Engine (motor_preditivo.py).
EXPIRING_BATCHES_IN_PROMPT = 10

SYSTEM_PROMPT = """Você é o Agente Funcionário do Mottainai — assistente operacional para estoquistas e gerentes.

Suas responsabilidades:
- Responder com precisão técnica sobre estoque, inventário, alertas e entrada de mercadorias.
- Usar os dados operacionais fornecidos como fonte da verdade.
- Apresentar dados de forma objetiva: números exatos, prioridades claras.
- NÃO inventar dados de estoque, quantidades ou validades.
- Para ações críticas (descartes, transferências), orientar sobre o procedimento correto.
- Você NÃO registra recebimentos ou descartes diretamente pelo chat. Se o usuário quiser fazer isso, informe que a ação está disponível nas telas/rotas dedicadas do sistema para receber mercadoria ou descartar um lote, e explique quais dados serão necessários (loja, lote, quantidade e, no caso de descarte, o motivo).
- Alertas críticos devem sempre ser destacados no início da resposta.
- Se houver uma análise de prateleira recente nesta conversa, você pode referenciá-la naturalmente (ex: "na foto que você enviou, a prateleira está com X% de ocupação") quando for relevante para a pergunta do usuário.
- NUNCA mencionar nomes internos de sistemas, agentes, bases de dados ou tecnologias (ex: "PostgreSQL", "RAG", "contexto") — fale como uma única assistente operacional do Mottainai.
- Você SÓ responde assuntos operacionais do Mottainai (estoque, inventário, alertas, procedimentos). Se a pergunta for sobre qualquer outro assunto, recuse educadamente e explique que só pode ajudar com temas operacionais do Mottainai.
"""

# Appended to SYSTEM_PROMPT only in the native tool-calling path — the
# legacy path already hands the model finished data, so it never needs
# to be told tools exist.
NATIVE_TOOL_GUIDANCE = """
Você tem ferramentas para consultar dados operacionais em tempo real (alertas de estoque, situação do inventário, lotes vencendo, base de conhecimento). Use-as sempre que precisar de números concretos para responder — nunca invente dados. Pode chamar mais de uma ferramenta na mesma resposta se precisar."""


async def node_agente_funcionario(state: MottainaiState) -> MottainaiState:
    """Employee Agent node in the LangGraph graph."""
    if get_settings().native_tool_calling_enabled:
        return await _node_agente_funcionario_native(state)
    return await _node_agente_funcionario_legacy(state)


async def _node_agente_funcionario_legacy(state: MottainaiState) -> MottainaiState:
    """Today's behavior: always fetches every data source, then hands the
    LLM one finished prompt with all of it inlined — see module docstring."""
    query = state["sanitized_input"]
    empresa_id = state["empresa_id"]
    usuario_id = state["usuario_id"]
    tool_runs: list[dict] = []

    # Operational queries, notifications and RAG are independent of each
    # other (none consumes another's result), so they run concurrently.
    # format_notifications_for_agent below is the one exception — it
    # needs `notifications` already resolved, so it stays sequential,
    # after this gather. get_stock_alerts/get_inventory_status/
    # get_expiring_batches go through guarded() (app/tools/
    # postgres_tools.py) to bound how many of this node's own Postgres
    # calls can be in flight at once; get_inbox (Redis), the vision
    # lookup and retrieve_with_sources (Mongo) have their own separate
    # connection pools and don't need it.
    (
        alerts_data, inventory_data, expiring_data, notifications, vision_analyses, (rag_context, sources),
    ) = await gather_or_raise(
        timed_tool_call(
            tool_runs, "get_stock_alerts", guarded(get_stock_alerts(empresa_id, limit=5)),
            input={"empresa_id": empresa_id, "limit": 5},
        ),
        timed_tool_call(
            tool_runs, "get_inventory_status", guarded(get_inventory_status(empresa_id)),
            input={"empresa_id": empresa_id},
        ),
        timed_tool_call(
            tool_runs, "get_expiring_batches", guarded(get_expiring_batches(empresa_id, days_ahead=7)),
            input={"empresa_id": empresa_id, "days_ahead": 7},
        ),
        timed_tool_call(
            tool_runs, "get_inbox", get_inbox(empresa_id, usuario_id, limit=5),
            input={"empresa_id": empresa_id, "usuario_id": usuario_id, "limit": 5},
        ),
        timed_tool_call(
            tool_runs, "get_recent_vision_analyses",
            get_recent_vision_analyses(state["session_id"], limit=3),
            input={"session_id": state["session_id"], "limit": 3},
        ),
        timed_tool_call(
            tool_runs, "retrieve_with_sources", retrieve_with_sources(query, empresa_id),
            input={"query": query, "empresa_id": empresa_id},
        ),
    )

    # Formats the operational context (kept in Portuguese, see module docstring).
    # The batch list is truncated, so the heading carries the real total as
    # well — otherwise the agent would report the slice as the whole picture.
    expiring_shown = expiring_data[:EXPIRING_BATCHES_IN_PROMPT]
    notifications_text = await timed_tool_call(
        tool_runs, "format_notifications_for_agent",
        format_notifications_for_agent(notifications), input=None,
    )
    ops_context = f"""
ALERTAS ATIVOS ({len(alerts_data)}):
{json.dumps(alerts_data, default=str, ensure_ascii=False, indent=2)}

ESTOQUE (situação crítica primeiro):
{json.dumps(inventory_data[:10], default=str, ensure_ascii=False, indent=2)}

LOTES VENCENDO EM 7 DIAS (total {len(expiring_data)}, listando os {len(expiring_shown)} mais urgentes):
{json.dumps(expiring_shown, default=str, ensure_ascii=False, indent=2)}

ANÁLISES DE PRATELEIRA RECENTES NESTA CONVERSA ({len(vision_analyses)}):
{json.dumps(vision_analyses, default=str, ensure_ascii=False, indent=2)}

NOTIFICAÇÕES:
{notifications_text}
"""

    mem_context = format_memory_for_prompt(state["memory"])

    messages = [
        SystemMessage(content=f"{SYSTEM_PROMPT}\n\n--- Memória do usuário ---\n{mem_context}\n\n--- Dados operacionais ---\n{ops_context}\n\n--- Base de conhecimento ---\n{rag_context}"),
        *state["history"][-8:],
        HumanMessage(content=query),
    ]

    llm = get_llm(temperature=0.2)
    response = await llm.ainvoke(messages)
    content = response.content

    usage = response.usage_metadata or {}
    extra_sources = [{"type": "sql", "ref": "mottainai.alert + inventory + batch", "score": None}]
    if vision_analyses:
        extra_sources.append({"type": "other", "ref": "app.agents.visao (ai_results)", "score": None})

    return {
        **state,
        "agent_response": content,
        "sources": sources + extra_sources,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "tool_runs": state.get("tool_runs", []) + tool_runs,
    }


async def _node_agente_funcionario_native(state: MottainaiState) -> MottainaiState:
    """Native tool-calling path — see module docstring."""
    query = state["sanitized_input"]
    empresa_id = state["empresa_id"]
    usuario_id = state["usuario_id"]
    tool_runs: list[dict] = []
    sources: list[dict] = []

    # Not in the exposed toolkit (app/agents/tools_bridge.py) — these stay
    # always-fetched context in both paths, not something the model
    # decides whether to check.
    notifications, vision_analyses = await gather_or_raise(
        timed_tool_call(
            tool_runs, "get_inbox", get_inbox(empresa_id, usuario_id, limit=5),
            input={"empresa_id": empresa_id, "usuario_id": usuario_id, "limit": 5},
        ),
        timed_tool_call(
            tool_runs, "get_recent_vision_analyses",
            get_recent_vision_analyses(state["session_id"], limit=3),
            input={"session_id": state["session_id"], "limit": 3},
        ),
    )
    notifications_text = await timed_tool_call(
        tool_runs, "format_notifications_for_agent",
        format_notifications_for_agent(notifications), input=None,
    )

    tools = build_toolkit(
        ["get_stock_alerts", "get_inventory_status", "get_expiring_batches", "retrieve_with_sources"],
        empresa_id=empresa_id, tool_runs=tool_runs, sources_acc=sources,
    )

    mem_context = format_memory_for_prompt(state["memory"])
    vision_text = json.dumps(vision_analyses, default=str, ensure_ascii=False, indent=2)
    messages = [
        SystemMessage(
            content=(
                f"{SYSTEM_PROMPT}\n{NATIVE_TOOL_GUIDANCE}"
                f"\n\n--- Memória do usuário ---\n{mem_context}"
                f"\n\n--- Notificações ---\n{notifications_text}"
                f"\n\n--- Análises de prateleira recentes nesta conversa ---\n{vision_text}"
            )
        ),
        *state["history"][-8:],
        HumanMessage(content=query),
    ]

    response = await run_agent_with_tools(tools, messages, temperature=0.2)
    content = response.content

    usage = response.usage_metadata or {}
    extra_sources = [{"type": "sql", "ref": "mottainai.alert + inventory + batch", "score": None}]
    if vision_analyses:
        extra_sources.append({"type": "other", "ref": "app.agents.visao (ai_results)", "score": None})

    return {
        **state,
        "agent_response": content,
        "sources": sources + extra_sources,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "tool_runs": state.get("tool_runs", []) + tool_runs,
    }
