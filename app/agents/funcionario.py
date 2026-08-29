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

Note: SYSTEM_PROMPT and the operational context block fed to the LLM are
deliberately kept in Portuguese, same as the other agents.
"""
import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.runtime import MottainaiState, get_llm
from app.memory.long_term import format_memory_for_prompt
from app.rag.retriever import retrieve_with_sources
from app.tools.postgres_tools import get_expiring_batches, get_inventory_status, get_stock_alerts
from app.tools.redis_tools import format_notifications_for_agent, get_inbox

SYSTEM_PROMPT = """Você é o Agente Funcionário do Mottainai — assistente operacional para estoquistas e gerentes.

Suas responsabilidades:
- Responder com precisão técnica sobre estoque, inventário, alertas e entrada de mercadorias.
- Usar os dados do PostgreSQL (fornecidos no contexto) como fonte da verdade.
- Apresentar dados de forma objetiva: números exatos, prioridades claras.
- NÃO inventar dados de estoque, quantidades ou validades.
- Para ações críticas (descartes, transferências), orientar sobre o procedimento correto.
- Você NÃO registra recebimentos ou descartes diretamente pelo chat. Se o usuário quiser fazer isso, informe que a ação está disponível nas telas/rotas dedicadas do sistema para receber mercadoria ou descartar um lote, e explique quais dados serão necessários (loja, lote, quantidade e, no caso de descarte, o motivo).
- Alertas críticos devem sempre ser destacados no início da resposta.
- NUNCA mencionar nomes internos de sistemas, agentes, bases de dados ou tecnologias (ex: "PostgreSQL", "RAG", "contexto") — fale como uma única assistente operacional do Mottainai.
- Você SÓ responde assuntos operacionais do Mottainai (estoque, inventário, alertas, procedimentos). Se a pergunta for sobre qualquer outro assunto, recuse educadamente e explique que só pode ajudar com temas operacionais do Mottainai.
"""


async def node_agente_funcionario(state: MottainaiState) -> MottainaiState:
    """Employee Agent node in the LangGraph graph."""
    query = state["sanitized_input"]
    empresa_id = state["empresa_id"]
    usuario_id = state["usuario_id"]

    # Operational queries and notifications scoped by the authenticated context.
    alerts_data = await get_stock_alerts(empresa_id, limit=5)
    inventory_data = await get_inventory_status(empresa_id)
    expiring_data = await get_expiring_batches(empresa_id, days_ahead=7)
    notifications = await get_inbox(empresa_id, usuario_id, limit=5)

    # RAG: manual/procedures
    rag_context, sources = await retrieve_with_sources(query, empresa_id)

    # Formats the operational context (kept in Portuguese, see module docstring)
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
