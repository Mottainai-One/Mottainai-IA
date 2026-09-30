"""
Owner Agent — serves the company's owner/manager.
Skills: KPIs, reports, BI, analytics, recommendations.
Uses Postgres (analytics) + RAG. Writes memory.

Note: SYSTEM_PROMPT and the analytics context block fed to the LLM are
deliberately kept in Portuguese, same as the other agents.

Two code paths, chosen by settings.native_tool_calling_enabled (off by
default — see config/settings.py and app/agents/funcionario.py's module
docstring for the fuller rationale):
- _legacy (today's behavior): always fetches all five data sources, then
  hands the LLM one finished prompt with everything inlined.
- _native: exposes the same five as LangChain tools
  (app/agents/tools_bridge.py) and lets the model decide which it needs.
"""
import json
from datetime import date

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.runtime import MottainaiState, gather_or_raise, get_llm, run_agent_with_tools
from app.agents.tools_bridge import build_toolkit
from app.memory.long_term import format_memory_for_prompt
from app.observability.tool_runs import timed_tool_call
from app.rag.retriever import retrieve_with_sources
from app.tools.postgres_tools import (
    get_kpis,
    get_kpis_by_store,
    get_sales_summary,
    get_stock_alerts,
    guarded,
)
from config.settings import get_settings

SYSTEM_PROMPT = """Você é o Agente Dono do Mottainai — assistente estratégico para donos e gestores de varejo.

Suas responsabilidades:
- Apresentar KPIs, análises de desempenho, tendências e recomendações estratégicas.
- Usar os dados analíticos fornecidos como base factual — NUNCA inventar números.
- Calcular o ROI das ações de redução de desperdício quando perguntado.
- Se os dados incluírem mais de uma loja, você pode comparar as lojas entre si (faturamento, custo com descartes, alertas ativos) e apontar qual está performando melhor ou pior, quando o usuário perguntar ou quando for relevante.
- Fazer recomendações práticas e priorizadas (ex: "3 ações para reduzir perdas esta semana").
- Tom: executivo, direto, orientado a resultado.
- Sempre indique o período dos dados apresentados (ex: "Dados dos últimos 30 dias"), sem mencionar nomes internos de sistemas, bancos de dados ou tecnologias.
- Você SÓ responde assuntos do negócio Mottainai (KPIs, vendas, estoque, estratégia). Se a pergunta for sobre qualquer outro assunto, recuse educadamente e explique que só pode ajudar com temas do negócio.
"""

# Appended to SYSTEM_PROMPT only in the native tool-calling path.
NATIVE_TOOL_GUIDANCE = """
Você tem ferramentas para consultar KPIs, vendas, alertas e a base de conhecimento em tempo real. Use-as sempre que precisar de números concretos — nunca invente dados. Pode chamar mais de uma ferramenta na mesma resposta se precisar."""


async def node_agente_dono(state: MottainaiState) -> MottainaiState:
    """Owner Agent node in the LangGraph graph."""
    if get_settings().native_tool_calling_enabled:
        return await _node_agente_dono_native(state)
    return await _node_agente_dono_legacy(state)


async def _node_agente_dono_legacy(state: MottainaiState) -> MottainaiState:
    """Today's behavior: always fetches every data source, then hands the
    LLM one finished prompt with all of it inlined — see module docstring."""
    query = state["sanitized_input"]
    empresa_id = state["empresa_id"]
    tool_runs: list[dict] = []

    # Analytics data from Postgres plus RAG — none of these five consumes
    # another's result, so they all run concurrently. guarded() (app/tools/
    # postgres_tools.py) bounds how many of this node's own Postgres calls
    # can be in flight at once; retrieve_with_sources (Mongo) has its own
    # separate connection pool and doesn't need it.
    kpis, sales, alerts, stores_kpis, (rag_context, sources) = await gather_or_raise(
        timed_tool_call(
            tool_runs, "get_kpis", guarded(get_kpis(empresa_id)), input={"empresa_id": empresa_id},
        ),
        timed_tool_call(
            tool_runs, "get_sales_summary", guarded(get_sales_summary(empresa_id, days_back=30)),
            input={"empresa_id": empresa_id, "days_back": 30},
        ),
        timed_tool_call(
            tool_runs, "get_stock_alerts", guarded(get_stock_alerts(empresa_id, limit=10)),
            input={"empresa_id": empresa_id, "limit": 10},
        ),
        timed_tool_call(
            tool_runs, "get_kpis_by_store", guarded(get_kpis_by_store(empresa_id, days_back=30)),
            input={"empresa_id": empresa_id, "days_back": 30},
        ),
        timed_tool_call(
            tool_runs, "retrieve_with_sources", retrieve_with_sources(query, empresa_id),
            input={"query": query, "empresa_id": empresa_id},
        ),
    )

    analytics_context = f"""Data atual: {date.today().isoformat()}

KPIs (últimos 30 dias):
- Faturamento: R$ {kpis.get('revenue_30d', 0):,.2f}
- Custo com descartes: R$ {kpis.get('disposal_cost_30d', 0):,.2f}
- Alertas ativos: {kpis.get('active_alerts', 0)}

KPIs POR LOJA (últimos 30 dias, para comparação entre lojas):
{json.dumps(stores_kpis, default=str, ensure_ascii=False, indent=2)}

TOP PRODUTOS VENDIDOS (30 dias):
{json.dumps(sales[:10], default=str, ensure_ascii=False, indent=2)}

ALERTAS PENDENTES:
{json.dumps(alerts[:5], default=str, ensure_ascii=False, indent=2)}
"""

    mem_context = format_memory_for_prompt(state["memory"])

    messages = [
        SystemMessage(content=f"{SYSTEM_PROMPT}\n\n--- Memória do usuário ---\n{mem_context}\n\n--- Dados analíticos ---\n{analytics_context}\n\n--- Base de conhecimento ---\n{rag_context}"),
        *state["history"][-8:],
        HumanMessage(content=query),
    ]

    llm = get_llm(temperature=0.3)
    response = await llm.ainvoke(messages)
    content = response.content

    usage = response.usage_metadata or {}

    return {
        **state,
        "agent_response": content,
        "sources": sources + [{"type": "sql", "ref": "mottainai.sales_transaction + alert + disposal + retail_store", "score": None}],
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "tool_runs": state.get("tool_runs", []) + tool_runs,
    }


async def _node_agente_dono_native(state: MottainaiState) -> MottainaiState:
    """Native tool-calling path — see module docstring."""
    query = state["sanitized_input"]
    empresa_id = state["empresa_id"]
    tool_runs: list[dict] = []
    sources: list[dict] = []

    tools = build_toolkit(
        ["get_kpis", "get_sales_summary", "get_stock_alerts", "get_kpis_by_store", "retrieve_with_sources"],
        empresa_id=empresa_id, tool_runs=tool_runs, sources_acc=sources,
    )

    mem_context = format_memory_for_prompt(state["memory"])
    messages = [
        SystemMessage(
            content=(
                f"{SYSTEM_PROMPT}\n{NATIVE_TOOL_GUIDANCE}"
                f"\n\nData atual: {date.today().isoformat()}"
                f"\n\n--- Memória do usuário ---\n{mem_context}"
            )
        ),
        *state["history"][-8:],
        HumanMessage(content=query),
    ]

    response = await run_agent_with_tools(tools, messages, temperature=0.3)
    content = response.content

    usage = response.usage_metadata or {}

    return {
        **state,
        "agent_response": content,
        "sources": sources + [{"type": "sql", "ref": "mottainai.sales_transaction + alert + disposal + retail_store", "score": None}],
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "tool_runs": state.get("tool_runs", []) + tool_runs,
    }
