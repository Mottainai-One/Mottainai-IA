"""Shared plumbing for the two RAG-only, Postgres-free chat agents (Cliente, FAQ).

Both agents follow the exact same shape — retrieve RAG context, format
memory, build the message list, call the LLM, shape the result — and only
differ in their SYSTEM_PROMPT wording, temperature and history window. This
factors that shape out once so the two prompts can keep diverging (they are
tuned independently) without the surrounding plumbing drifting out of sync.
"""
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.runtime import MottainaiState, get_llm
from app.memory.long_term import format_memory_for_prompt
from app.rag.retriever import retrieve_with_sources


async def run_rag_chat_agent(
    state: MottainaiState,
    *,
    system_prompt: str,
    temperature: float,
    history_window: int,
) -> MottainaiState:
    query = state["sanitized_input"]
    empresa_id = state["empresa_id"]

    rag_context, sources = await retrieve_with_sources(query, empresa_id)
    mem_context = format_memory_for_prompt(state["memory"])

    messages = [
        SystemMessage(content=f"{system_prompt}\n\n--- Memória do usuário ---\n{mem_context}\n\n--- Informações disponíveis ---\n{rag_context}"),
        *state["history"][-history_window:],
        HumanMessage(content=query),
    ]

    response = await get_llm(temperature=temperature).ainvoke(messages)
    usage = getattr(response, "usage_metadata", None) or {}

    return {
        **state,
        "agent_response": response.content,
        "sources": sources,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    }
