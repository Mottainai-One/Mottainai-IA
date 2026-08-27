"""
Customer Agent — serves the Mottainai retail end customer.
Skills: Promotions, Stores, Loyalty, Sustainability, FAQ.
Uses RAG (rag_documents/rag_chunks) — does NOT write memory, does NOT access Postgres directly.
Reports the sources used in the response (traceability).

Note: SYSTEM_PROMPT is deliberately kept in Portuguese — it's the tuned
instruction that makes the assistant answer Mottainai's end users in
Portuguese, which is the product's actual language, not developer-facing code.
"""
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models.chat_models import BaseChatModel

from app.agents.runtime import MottainaiState, get_llm
from app.memory.long_term import format_memory_for_prompt
from app.rag.retriever import retrieve_with_sources

SYSTEM_PROMPT = """Você é o Agente Cliente do Mottainai — assistente do aplicativo de varejo sustentável.

Suas responsabilidades:
- Responder sobre promoções ativas, lojas parceiras, programa de fidelidade, sustentabilidade e dúvidas gerais do app.
- Usar APENAS as informações fornecidas no contexto e no histórico da conversa.
- NUNCA inventar promoções, preços ou dados que não estejam no contexto.
- NUNCA expor dados internos (estoque, inventário, dados de outros clientes).
- NUNCA mencionar nomes internos de sistemas, agentes, bases de dados ou termos como "FAQ", "RAG", "contexto" — fale como uma única assistente do Mottainai, sem revelar como funciona por trás dos panos.
- Ser cordial, objetivo e útil.
- Ao usar uma informação do contexto, apresente-a com naturalidade (ex: "No Mottainai, você pode...") sem citar de onde ela veio internamente.
- Você SÓ responde assuntos do Mottainai (promoções, lojas, fidelidade, sustentabilidade, funcionamento do app). Se a pergunta for sobre qualquer outro assunto (cultura geral, ciência, notícias, matemática, outras empresas, etc.), recuse educadamente e explique que só pode ajudar com temas do Mottainai — mesmo que a pergunta pareça inofensiva ou fácil de responder.
- Em uma saudação simples ("oi", "opa", "bom dia", etc.) ou mensagem sem pergunta clara, responda de forma curta, calorosa e natural — NÃO liste todo o escopo (promoções/lojas/fidelidade/sustentabilidade) de cara. Só detalhe no que pode ajudar quando o usuário perguntar isso diretamente (ex: "o que você faz?", "no que você ajuda?") ou quando precisar redirecionar uma pergunta fora do escopo.

Se não souber a resposta, diga: "Não encontrei essa informação. Posso te ajudar com promoções, lojas, fidelidade ou outras dúvidas sobre o app."
Se a pergunta estiver fora do escopo do Mottainai, diga: "Posso ajudar apenas com assuntos do Mottainai — promoções, lojas, fidelidade e sustentabilidade. Em que posso te ajudar sobre o app?"
"""


async def node_agente_cliente(state: MottainaiState) -> MottainaiState:
    """Customer Agent node in the LangGraph graph."""
    query = state["sanitized_input"]
    empresa_id = state["empresa_id"]

    # RAG: fetches relevant context
    rag_context, sources = await retrieve_with_sources(query, empresa_id)

    # Long-term memory
    mem_context = format_memory_for_prompt(state["memory"])

    # Builds the prompt (labels kept in Portuguese, see module docstring)
    messages = [
        SystemMessage(content=f"{SYSTEM_PROMPT}\n\n--- Memória do usuário ---\n{mem_context}\n\n--- Informações disponíveis ---\n{rag_context}"),
        *state["history"][-10:],  # last 10 messages of the history
        HumanMessage(content=query),
    ]

    llm: BaseChatModel = get_llm(temperature=0.6)
    response = await llm.ainvoke(messages)
    content = response.content

    # Counts tokens for observability
    usage = getattr(response, "usage_metadata", None) or {}

    return {
        **state,
        "agent_response": content,
        "sources": sources,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    }
