"""
Supervisor — Orquestrador LangGraph.
É o nó raiz do grafo. Roteia a mensagem do usuário para o agente correto
com base no perfil (role) e na intenção detectada.

Fluxo:
  start → guardrail_entrada → supervisor_route → [agente] → juiz → guardrail_saida → end
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable

from langgraph.graph import END, StateGraph

from app.agents.runtime import MottainaiState, get_llm
from app.guardrails.entrada import guardrail_entrada
from app.guardrails.saida import guardrail_saida
from app.memory.extractor import extract_memories
from app.memory.long_term import load_memory, update_memory
from app.memory.short_term import get_or_create_conversation, load_history, save_message

# ─────────────────────────────────────────────
# Nós do grafo
# ─────────────────────────────────────────────

async def node_guardrail_entrada(state: MottainaiState) -> MottainaiState:
    """Valida e sanitiza a entrada do usuário."""
    result = await guardrail_entrada(
        state["user_input"],
        state["usuario_id"],
        state["empresa_id"],
    )
    if not result.allowed:
        return {
            **state,
            "error": result.reason,
            "final_response": result.reason,
            "agent_response": "",
        }
    return {
        **state,
        "sanitized_input": result.sanitized_input or state["user_input"],
        "error": None,
    }


async def node_load_context(state: MottainaiState) -> MottainaiState:
    """Carrega histórico e memória de longo prazo."""
    if state.get("error"):
        return state

    conversation = await get_or_create_conversation(
        state["session_id"],
        state["empresa_id"],
        state["usuario_id"],
        agent="pending",  # agente ainda não foi selecionado neste ponto
    )
    history = await load_history(state["session_id"])
    memory = await load_memory(state["empresa_id"], state["usuario_id"])

    return {**state, "history": history, "memory": memory, "conversation_id": conversation["_id"]}


async def node_supervisor_route(state: MottainaiState) -> MottainaiState:
    """
    Supervisor: detecta intenção e seleciona o agente.
    NÃO usa RAG, NÃO usa tools, NÃO escreve memória.

    Regras de roteamento por role:
      - ESTOQUISTA    → agente_funcionario
      - GERENTE       → agente_funcionario
      - DONO          → agente_dono (motor_preditivo se pedir previsão)
      - CLIENTE       → agente_cliente ou agente_faq para dúvidas gerais
    """
    if state.get("error"):
        return state

    role = state["user_role"].upper()
    text = state["sanitized_input"].lower()

    # Motor preditivo: só aciona para perguntas analíticas/preditivas
    KEYWORDS_PREDITIVO = {
        "previsão", "previsao", "prever", "preve", "prevê",
        "demanda", "abastecimento", "tendência", "tendencia",
        "vai acabar", "quando acaba", "risco de falta", "risco de perda",
        "projeção", "projecao", "próxima semana", "proxima semana",
        "próximo mês", "proximo mes",
    }

    if role == "DONO":
        if any(kw in text for kw in KEYWORDS_PREDITIVO):
            return {**state, "selected_agent": "motor_preditivo"}

    FAQ_KEYWORDS = {"faq", "dúvida", "duvida", "como funciona", "ajuda", "suporte", "fidelidade", "pontos", "sustentabilidade"}
    if role == "CLIENTE":
        selected = "faq" if any(keyword in text for keyword in FAQ_KEYWORDS) else "cliente"
        return {**state, "selected_agent": selected}

    role_to_agent = {
        "ESTOQUISTA": "funcionario", "GERENTE": "funcionario",
        "DONO": "dono",
    }
    selected = role_to_agent.get(role, "funcionario")
    return {**state, "selected_agent": selected}


async def node_guardrail_saida(state: MottainaiState) -> MottainaiState:
    """Filtra a resposta aprovada pelo Juiz antes de retornar ao usuário."""
    if state.get("error"):
        return state

    result = guardrail_saida(state["agent_response"], state["user_role"])
    final = result.output if result.safe else "Não consigo fornecer essa informação no momento."

    # Persiste mensagens no histórico
    await save_message(
        state["session_id"],
        role="user",
        content=state["sanitized_input"],
        agent=state["selected_agent"],
    )
    await save_message(
        state["session_id"],
        role="assistant",
        content=final,
        agent=state["selected_agent"],
        input_tokens=state.get("input_tokens"),
        output_tokens=state.get("output_tokens"),
        sources=state.get("sources", []),
    )

    # Atualiza memória de longo prazo com dados explícitos e não sensíveis.
    await update_memory(
        state["empresa_id"], state["usuario_id"], last_agent=state["selected_agent"]
    )
    extracted = extract_memories(state["sanitized_input"])
    for preference in extracted["preferences"]:
        await update_memory(state["empresa_id"], state["usuario_id"], new_preference=preference)
    for fact in extracted["facts"]:
        await update_memory(state["empresa_id"], state["usuario_id"], new_fact=fact)

    return {**state, "final_response": final}


def _route_after_guardrail(state: MottainaiState) -> str:
    """Decide para onde ir após o guardrail de entrada."""
    if state.get("error"):
        return "end"
    return "load_context"


def _route_agent(state: MottainaiState) -> str:
    """Rota para o agente correto após o supervisor."""
    return state.get("selected_agent", "cliente")


def _route_after_judge(state: MottainaiState) -> str:
    """Após o juiz, vai sempre para o guardrail de saída."""
    return "guardrail_saida"


def _instrument_node(name: str, node: Callable[[MottainaiState], Awaitable[MottainaiState]]):
    """Mede cada etapa do grafo sem alterar o comportamento do agente."""
    async def instrumented(state: MottainaiState) -> MottainaiState:
        started = time.perf_counter()
        result = await node(state)
        timings = dict(result.get("node_latencies_ms", state.get("node_latencies_ms", {})))
        timings[name] = round((time.perf_counter() - started) * 1000, 2)
        return {**result, "node_latencies_ms": timings}
    return instrumented


# ─────────────────────────────────────────────
# Construção do grafo
# ─────────────────────────────────────────────

def build_graph() -> StateGraph:
    """
    Monta o grafo LangGraph do Mottainai.
    Os nós dos agentes são injetados externamente para evitar circular imports.
    """
    from app.agents.cliente import node_agente_cliente
    from app.agents.faq import node_agente_faq
    from app.agents.funcionario import node_agente_funcionario
    from app.agents.dono import node_agente_dono
    from app.agents.motor_preditivo import node_motor_preditivo
    from app.agents.juiz import node_agente_juiz

    graph = StateGraph(MottainaiState)

    # Nós
    graph.add_node("guardrail_entrada", _instrument_node("guardrail_entrada", node_guardrail_entrada))
    graph.add_node("load_context", _instrument_node("load_context", node_load_context))
    graph.add_node("supervisor", _instrument_node("supervisor", node_supervisor_route))
    graph.add_node("cliente", _instrument_node("cliente", node_agente_cliente))
    graph.add_node("faq", _instrument_node("faq", node_agente_faq))
    graph.add_node("funcionario", _instrument_node("funcionario", node_agente_funcionario))
    graph.add_node("dono", _instrument_node("dono", node_agente_dono))
    graph.add_node("motor_preditivo", _instrument_node("motor_preditivo", node_motor_preditivo))
    graph.add_node("juiz", _instrument_node("juiz", node_agente_juiz))
    graph.add_node("guardrail_saida", _instrument_node("guardrail_saida", node_guardrail_saida))

    # Arestas
    graph.set_entry_point("guardrail_entrada")
    graph.add_conditional_edges("guardrail_entrada", _route_after_guardrail, {"end": END, "load_context": "load_context"})
    graph.add_edge("load_context", "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        _route_agent,
        {
            "cliente": "cliente",
            "faq": "faq",
            "funcionario": "funcionario",
            "dono": "dono",
            "motor_preditivo": "motor_preditivo",
        },
    )
    graph.add_edge("cliente", "juiz")
    graph.add_edge("faq", "juiz")
    graph.add_edge("funcionario", "juiz")
    graph.add_edge("dono", "juiz")
    graph.add_edge("motor_preditivo", "juiz")
    graph.add_edge("juiz", "guardrail_saida")  # edge simples — sem condição desnecessária
    graph.add_edge("guardrail_saida", END)

    return graph.compile()


# Instância global compilada
mottainai_graph = build_graph()
