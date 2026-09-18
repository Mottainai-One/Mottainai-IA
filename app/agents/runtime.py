"""Dependencies shared by the agents, without assembling the graph."""
from __future__ import annotations

import asyncio
from typing import Any, NotRequired, TypedDict

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from app.config import get_settings

settings = get_settings()

_http_client = httpx.Client()
_http_async_client = httpx.AsyncClient()


class MottainaiState(TypedDict):
    session_id: str
    empresa_id: int
    usuario_id: int
    store_id: NotRequired[int | None]  # scopes the Predictive Engine to one store
    user_role: str
    user_input: str
    sanitized_input: str
    history: list[BaseMessage]
    memory: dict
    conversation_id: object
    selected_agent: str
    agent_response: str
    judge_approved: bool
    judge_score: float
    final_response: str
    error: str | None
    sources: list[dict]
    input_tokens: int
    output_tokens: int
    node_latencies_ms: dict[str, float]
    tool_runs: NotRequired[list[dict]]  # accumulated by agent nodes, flushed once in main.py
    routing_log: NotRequired[dict]  # set by node_supervisor_route, flushed once in main.py


async def gather_or_raise(*coros: Any) -> list[Any]:
    """asyncio.gather(*coros, return_exceptions=True), then re-raises the
    first exception found once every coroutine has finished.

    Plain asyncio.gather(*coros) (return_exceptions=False, the default)
    propagates the first exception as soon as it happens, but — per
    asyncio's own docs — leaves every other awaitable running in the
    background, uncancelled and unawaited. Sequential `await` calls (what
    every agent node did before this) never had that failure mode: one
    call failing simply meant the ones after it never started. Fanning
    those same calls out with gather() reintroduces it unless exceptions
    are collected instead of raised immediately — this restores today's
    behavior (any one tool failing fails the whole node) without the
    extra background tasks.
    """
    results = await asyncio.gather(*coros, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results


def get_llm_model_label() -> str:
    """Safe provider/model identifier for metrics."""
    return settings.llm_model_label


def _build_llm(temperature: float) -> BaseChatModel:
    if settings.llm_provider == "ollama_local":
        return ChatOpenAI(
            api_key="",
            base_url=settings.ollama_local_base_url.rstrip("/"),
            model=settings.ollama_local_model,
            temperature=temperature,
            max_tokens=settings.llm_max_output_tokens,
            http_client=_http_client,
            http_async_client=_http_async_client,
        )

    if settings.llm_provider == "ollama":
        if not settings.ollama_api_key:
            raise RuntimeError("OLLAMA_API_KEY not configured")
        return ChatOpenAI(
            api_key=settings.ollama_api_key,
            base_url=settings.ollama_base_url.rstrip("/"),
            model=settings.ollama_model,
            temperature=temperature,
            max_tokens=settings.llm_max_output_tokens,
            http_client=_http_client,
            http_async_client=_http_async_client,
        )

    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY not configured")
    return ChatGroq(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        temperature=temperature,
        max_tokens=settings.llm_max_output_tokens,
        http_client=_http_client,
        http_async_client=_http_async_client,
    )


async def run_agent_with_tools(
    tools: list[BaseTool],
    messages: list[BaseMessage],
    *,
    temperature: float = 0.3,
    max_iterations: int | None = None,
) -> AIMessage:
    """
    Native tool calling: binds `tools` to a fresh chat model, then loops —
    send the conversation, run whatever tools the model asks for, feed
    their results back as ToolMessages, ask again — until the model
    answers without requesting another tool call, or `max_iterations`
    rounds of tool calls have happened
    (settings.agent_tool_calling_max_iterations by default).

    Builds its own model rather than taking one from get_llm(): bind_tools
    must run on the raw chat model, and get_llm() returns a RunnableRetry
    wrapper that doesn't expose it (verified directly — RunnableRetry has
    no bind_tools). Retry is applied after binding instead, once here,
    with the exact same settings get_llm() uses.

    A tool call that raises is not re-raised here: its error is fed back
    to the model as that tool's result (same as any other tool-calling
    loop — LangGraph's own ToolNode does the same by default), so the
    model can tell the user the lookup failed instead of the whole
    request 500ing over one flaky call. It is still recorded in
    tool_runs as status="error" by timed_tool_call before the exception
    reaches here.

    If max_iterations is exhausted, makes one final call with no tools
    bound, forcing a direct answer from whatever was gathered so far
    instead of silently returning a tool-call request as if it were the
    answer.
    """
    settings = get_settings()
    if max_iterations is None:
        max_iterations = settings.agent_tool_calling_max_iterations

    raw_llm = _build_llm(temperature)
    retry_kwargs = {"stop_after_attempt": settings.llm_max_retries, "wait_exponential_jitter": True}
    bound_llm = raw_llm.bind_tools(tools).with_retry(**retry_kwargs)
    plain_llm = raw_llm.with_retry(**retry_kwargs)

    tools_by_name = {tool.name: tool for tool in tools}
    conversation = list(messages)

    for _ in range(max_iterations):
        response = await bound_llm.ainvoke(conversation)
        if not response.tool_calls:
            return response
        conversation.append(response)
        for call in response.tool_calls:
            tool = tools_by_name.get(call["name"])
            if tool is None:
                content = f"Ferramenta desconhecida: {call['name']}"
            else:
                try:
                    content = await tool.ainvoke(call["args"])
                except Exception as exc:
                    content = f"Erro ao executar {call['name']}: {exc}"
            conversation.append(ToolMessage(content=str(content), tool_call_id=call["id"]))

    return await plain_llm.ainvoke(conversation)


def get_llm(temperature: float = 0.3) -> Runnable:
    """
    Returns the configured text provider, with automatic retries
    (exponential backoff + jitter) on transient provider failure — timeout,
    connection error or 5xx. Does not change behavior on success: same
    response, same prompt, it only avoids failing on a temporary hiccup of
    the external provider.
    """
    llm = _build_llm(temperature)
    return llm.with_retry(stop_after_attempt=settings.llm_max_retries, wait_exponential_jitter=True)
