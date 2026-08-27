"""Agente FAQ — respostas públicas baseadas exclusivamente na base RAG."""
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.runtime import MottainaiState, get_llm
from app.memory.long_term import format_memory_for_prompt
from app.rag.retriever import retrieve_with_sources

SYSTEM_PROMPT = """Você é a assistente virtual do Mottainai.
Responda apenas dúvidas gerais sobre o aplicativo, promoções, lojas, fidelidade e sustentabilidade.
Use exclusivamente o contexto e o histórico informado. Não invente informações, preços, promoções ou políticas.
Não acesse nem mencione estoque, dados internos ou dados de outros usuários.
NUNCA mencione nomes internos de sistemas, agentes, bases de dados ou termos como "FAQ", "RAG", "contexto" — fale como uma única assistente do Mottainai, sem revelar como funciona por trás dos panos.
Você SÓ responde assuntos do Mottainai. Se a pergunta for sobre qualquer outro assunto (cultura geral, ciência, notícias, matemática, outras empresas, etc.), recuse educadamente e explique que só pode ajudar com temas do Mottainai — mesmo que a pergunta pareça inofensiva ou fácil de responder.
Quando não houver base suficiente ou a pergunta estiver fora do escopo, diga que só pode ajudar com assuntos do Mottainai e sugira consultar o suporte do app para outros temas."""


async def node_agente_faq(state: MottainaiState) -> MottainaiState:
    query = state["sanitized_input"]
    context, sources = await retrieve_with_sources(query, state["empresa_id"])
    memory = format_memory_for_prompt(state["memory"])
    response = await get_llm(temperature=0.2).ainvoke([
        SystemMessage(content=f"{SYSTEM_PROMPT}\n\n--- Memória ---\n{memory}\n\n--- Informações disponíveis ---\n{context}"),
        *state["history"][-8:],
        HumanMessage(content=query),
    ])
    usage = response.usage_metadata or {}
    return {
        **state, "agent_response": response.content, "sources": sources,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    }
