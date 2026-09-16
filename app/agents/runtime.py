"""Dependencies shared by the agents, without assembling the graph."""
from __future__ import annotations

import asyncio
from typing import Any, NotRequired, TypedDict

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
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
