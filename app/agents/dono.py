"""
Owner Agent — serves the company's owner/manager.
Skills: KPIs, reports, BI, analytics, recommendations.
Uses Postgres (analytics) + RAG. Writes memory.

Note: SYSTEM_PROMPT and the analytics context block fed to the LLM are
deliberately kept in Portuguese, same as the other agents.
"""
import json
from datetime import date

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.runtime import MottainaiState, get_llm
from app.memory.long_term import format_memory_for_prompt
from app.rag.retriever import retrieve_with_sources
from app.tools.postgres_tools import (
    get_kpis,
    get_kpis_by_store,
    get_sales_summary,
    get_stock_alerts,
)

SYSTEM_PROMPT = """Você é o Agente Dono do Mottainai — assistente estratégico para donos e gestores de varejo.

Suas responsabilidades:
- Apresentar KPIs, análises de desempenho, tendências e recomendações estratégicas.
- Usar os dados do PostgreSQL como base factual — NUNCA inventar números.
- Calcular o ROI das ações de redução de desperdício quando perguntado.
- Se houver dados de mais de uma loja, você pode compará-las (faturamento, custo com descartes, alertas ativos) e apontar qual está performando melhor ou pior, quando o usuário perguntar ou quando for relevante.
- Fazer recomendações práticas e priorizadas (ex: "3 ações para reduzir perdas esta semana").
- Tom: executivo, direto, orientado a resultado.
- Sempre indique o período dos dados apresentados (ex: "Dados dos últimos 30 dias"), sem mencionar nomes internos de sistemas, bancos de dados ou tecnologias.
- Você SÓ responde assuntos do negócio Mottainai (KPIs, vendas, estoque, estratégia). Se a pergunta for sobre qualquer outro assunto, recuse educadamente e explique que só pode ajudar com temas do negócio.
"""


async def node_agente_dono(state: MottainaiState) -> MottainaiState:
    """Owner Agent node in the LangGraph graph."""
    query = state["sanitized_input"]
    empresa_id = state["empresa_id"]

    # Analytics data from Postgres
    kpis = await get_kpis(empresa_id)
    sales = await get_sales_summary(empresa_id, days_back=30)
    alerts = await get_stock_alerts(empresa_id, limit=10)
    stores_kpis = await get_kpis_by_store(empresa_id, days_back=30)

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

    rag_context, sources = await retrieve_with_sources(query, empresa_id)
    mem_context = format_memory_for_prompt(state["memory"])

    messages = [
        SystemMessage(content=f"{SYSTEM_PROMPT}\n\n--- Memória do usuário ---\n{mem_context}\n\n--- Dados analíticos (PostgreSQL) ---\n{analytics_context}\n\n--- Base de conhecimento (RAG) ---\n{rag_context}"),
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
    }
