"""
Agente Funcionário — atende operadores, estoquistas e gerentes.
Skills: Manual do Sistema, Procedimentos, Estoque, Inventário, Entrada de Mercadorias, Alertas.
Usa Postgres (leitura) + Redis (notificações) + RAG local. Escreve memória.
"""
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models.chat_models import BaseChatModel

from app.agents.runtime import MottainaiState, get_llm
from app.memory.long_term import format_memory_for_prompt
from app.rag.retriever import retrieve_with_sources
from app.tools.postgres_tools import get_stock_alerts, get_inventory_status, get_expiring_batches
from app.tools.redis_tools import get_inbox, format_notifications_for_agent
import json

SYSTEM_PROMPT = """Você é o Agente Funcionário do Mottainai — assistente operacional para estoquistas e gerentes.

Suas responsabilidades:
- Responder com precisão técnica sobre estoque, inventário, alertas e entrada de mercadorias.
- Usar os dados do PostgreSQL (fornecidos no contexto) como fonte da verdade.
- Apresentar dados de forma objetiva: números exatos, prioridades claras.
- NÃO inventar dados de estoque, quantidades ou validades.
- Para ações críticas (descartes, transferências), orientar sobre o procedimento correto.
- Alertas críticos devem sempre ser destacados no início da resposta.
- NUNCA mencionar nomes internos de sistemas, agentes, bases de dados ou tecnologias (ex: "PostgreSQL", "RAG", "contexto") — fale como uma única assistente operacional do Mottainai.
- Você SÓ responde assuntos operacionais do Mottainai (estoque, inventário, alertas, procedimentos). Se a pergunta for sobre qualquer outro assunto, recuse educadamente e explique que só pode ajudar com temas operacionais do Mottainai.
"""


async def node_agente_funcionario(state: MottainaiState) -> MottainaiState:
    """Nó do Agente Funcionário no grafo LangGraph."""
    query = state["sanitized_input"]
    empresa_id = state["empresa_id"]
    usuario_id = state["usuario_id"]

    # Consultas operacionais e notificações isoladas pelo contexto autenticado.
    alerts_data = await get_stock_alerts(empresa_id, limit=5)
    inventory_data = await get_inventory_status(empresa_id)
    expiring_data = await get_expiring_batches(empresa_id, days_ahead=7)
    notifications = await get_inbox(empresa_id, usuario_id, limit=5)

    # RAG: manual/procedimentos
    rag_context, sources = await retrieve_with_sources(query, empresa_id)

    # Formata contexto operacional
    ops_context = f"""
ALERTAS ATIVOS ({len(alerts_data)}):
{json.dumps(alerts_data, default=str, ensure_ascii=False, indent=2)}

ESTOQUE (situação crítica primeiro):
{json.dumps(inventory_data[:10], default=str, ensure_ascii=False, indent=2)}

LOTES VENCENDO EM 7 DIAS ({len(expiring_data)}):
{json.dumps(expiring_data, default=str, ensure_ascii=False, indent=2)}

NOTIFICAÇÕES:
{await format_notifications_for_agent(notifications)}
"""

    mem_context = format_memory_for_prompt(state["memory"])

    messages = [
        SystemMessage(content=f"{SYSTEM_PROMPT}\n\n--- Memória do usuário ---\n{mem_context}\n\n--- Dados operacionais (PostgreSQL) ---\n{ops_context}\n\n--- Base de conhecimento (RAG) ---\n{rag_context}"),
        *state["history"][-8:],
        HumanMessage(content=query),
    ]

    llm: BaseChatModel = get_llm(temperature=0.2)
    response = await llm.ainvoke(messages)
    content = response.content

    usage = response.usage_metadata or {}
    return {
        **state,
        "agent_response": content,
        "sources": sources + [{"type": "sql", "ref": "mottainai.alert + inventory + batch", "score": None}],
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    }
