"""
Typed, per-request LangChain tools wrapping this app's own read-only data
access, so an agent's LLM call can decide which of them to make (native
tool calling / function calling) instead of the node always fetching a
fixed set before ever talking to the model.

Read-only, on purpose, and not negotiable: discard_batch and
receive_inventory (app/tools/postgres_tools.py) are writes and are
deliberately never wrapped here. They stay behind their own explicit,
role-gated endpoints (POST /funcionario/descartar-lote,
POST /funcionario/receber-mercadoria) — a model choosing to call a tool
is not the same authorization event as an authenticated user submitting
a form, and conflating the two would let a prompt (the user's message,
or text injected through RAG content) trigger a real write.

Tenant safety: every tool below is built per-request by build_toolkit(),
which closes over `empresa_id` from the authenticated session
(MottainaiState) — empresa_id is never a field in any tool's args_schema,
so it is not part of what the model's tool call can express in the first
place. If a model's tool call JSON includes an "empresa_id" key anyway
(hallucinated, or echoed back from injected text), Pydantic silently
drops it as an unrecognized field when validating against the schema
below (BaseModel's default `extra="ignore"`) — verified directly:
StructuredTool.ainvoke({"limit": 3, "empresa_id": 999}) against a
schema with only `limit` calls the wrapped function with limit=3 alone.
This is not a runtime filter that could have a gap; the function that
touches the database is never given a parameter to receive a tenant id
from the model even if validation didn't strip it.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from app.observability.tool_runs import timed_tool_call
from app.rag.retriever import retrieve_with_sources
from app.tools.postgres_tools import (
    get_expiring_batches,
    get_inventory_status,
    get_kpis,
    get_kpis_by_store,
    get_sales_summary,
    get_stock_alerts,
    guarded,
)
from config.settings import get_settings


def _to_llm_content(value: Any) -> str:
    """Same lossy-but-safe round trip already trusted elsewhere in this
    codebase (app/observability/tool_runs.py's _as_object) for turning a
    Postgres result (Decimal, date) into something JSON-safe — here it
    becomes the literal text handed back to the model as the tool's
    result, not a Mongo document, so it stays a string rather than a
    dict/None-shaped object."""
    return json.dumps(value, default=str, ensure_ascii=False)


class _StockAlertsArgs(BaseModel):
    limit: int = Field(default=10, ge=1, le=50, description="Máximo de alertas a retornar.")
    store_id: int | None = Field(default=None, description="Filtra por uma loja específica, se informado.")


class _InventoryStatusArgs(BaseModel):
    store_id: int | None = Field(default=None, description="Filtra por uma loja específica, se informado.")


class _ExpiringBatchesArgs(BaseModel):
    days_ahead: int = Field(default=7, ge=1, le=90, description="Janela de dias até o vencimento.")
    store_id: int | None = Field(default=None, description="Filtra por uma loja específica, se informado.")


class _KpisArgs(BaseModel):
    """get_kpis takes no caller-controlled arguments at all."""


class _KpisByStoreArgs(BaseModel):
    days_back: int = Field(default=30, ge=1, le=365, description="Janela de dias para o cálculo dos KPIs.")


class _SalesSummaryArgs(BaseModel):
    days_back: int = Field(default=30, ge=1, le=365, description="Janela de dias para o resumo de vendas.")
    store_id: int | None = Field(default=None, description="Filtra por uma loja específica, se informado.")


class _RetrieveKnowledgeArgs(BaseModel):
    query: str = Field(description="Pergunta ou termo a buscar na base de conhecimento.")


def _build_stock_alerts_tool(empresa_id: int, tool_runs: list[dict]) -> BaseTool:
    async def _run(limit: int = 10, store_id: int | None = None) -> str:
        result = await timed_tool_call(
            tool_runs, "get_stock_alerts",
            guarded(get_stock_alerts(empresa_id, limit=limit, store_id=store_id)),
            input={"empresa_id": empresa_id, "limit": limit, "store_id": store_id},
        )
        return _to_llm_content(result)

    return StructuredTool.from_function(
        coroutine=_run, name="get_stock_alerts", args_schema=_StockAlertsArgs,
        description="Lista os alertas de estoque ativos (ruptura, abaixo do mínimo, excesso).",
    )


def _build_inventory_status_tool(empresa_id: int, tool_runs: list[dict]) -> BaseTool:
    async def _run(store_id: int | None = None) -> str:
        result = await timed_tool_call(
            tool_runs, "get_inventory_status",
            guarded(get_inventory_status(empresa_id, store_id=store_id)),
            input={"empresa_id": empresa_id, "store_id": store_id},
        )
        return _to_llm_content(result)

    return StructuredTool.from_function(
        coroutine=_run, name="get_inventory_status", args_schema=_InventoryStatusArgs,
        description="Situação atual do estoque (quantidade vs. mínimo/máximo) por produto e loja.",
    )


def _build_expiring_batches_tool(empresa_id: int, tool_runs: list[dict]) -> BaseTool:
    async def _run(days_ahead: int = 7, store_id: int | None = None) -> str:
        result = await timed_tool_call(
            tool_runs, "get_expiring_batches",
            guarded(get_expiring_batches(empresa_id, days_ahead=days_ahead, store_id=store_id)),
            input={"empresa_id": empresa_id, "days_ahead": days_ahead, "store_id": store_id},
        )
        return _to_llm_content(result)

    return StructuredTool.from_function(
        coroutine=_run, name="get_expiring_batches", args_schema=_ExpiringBatchesArgs,
        description="Lotes com vencimento próximo, ordenados pelo mais urgente primeiro.",
    )


def _build_kpis_tool(empresa_id: int, tool_runs: list[dict]) -> BaseTool:
    async def _run() -> str:
        result = await timed_tool_call(
            tool_runs, "get_kpis", guarded(get_kpis(empresa_id)), input={"empresa_id": empresa_id},
        )
        return _to_llm_content(result)

    return StructuredTool.from_function(
        coroutine=_run, name="get_kpis", args_schema=_KpisArgs,
        description="KPIs consolidados da empresa: faturamento, custo com descartes e alertas ativos (últimos 30 dias).",
    )


def _build_kpis_by_store_tool(empresa_id: int, tool_runs: list[dict]) -> BaseTool:
    async def _run(days_back: int = 30) -> str:
        result = await timed_tool_call(
            tool_runs, "get_kpis_by_store",
            guarded(get_kpis_by_store(empresa_id, days_back=days_back)),
            input={"empresa_id": empresa_id, "days_back": days_back},
        )
        return _to_llm_content(result)

    return StructuredTool.from_function(
        coroutine=_run, name="get_kpis_by_store", args_schema=_KpisByStoreArgs,
        description="KPIs por loja (faturamento, custo com descartes, alertas), para comparar lojas entre si.",
    )


def _build_sales_summary_tool(empresa_id: int, tool_runs: list[dict]) -> BaseTool:
    async def _run(days_back: int = 30, store_id: int | None = None) -> str:
        result = await timed_tool_call(
            tool_runs, "get_sales_summary",
            guarded(get_sales_summary(empresa_id, days_back=days_back, store_id=store_id)),
            input={"empresa_id": empresa_id, "days_back": days_back, "store_id": store_id},
        )
        return _to_llm_content(result)

    return StructuredTool.from_function(
        coroutine=_run, name="get_sales_summary", args_schema=_SalesSummaryArgs,
        description="Resumo de vendas por produto (quantidade, transações, preço médio) em uma janela de dias.",
    )


def _build_retrieve_knowledge_tool(
    empresa_id: int, tool_runs: list[dict], sources_acc: list[dict],
) -> BaseTool:
    async def _run(query: str) -> str:
        context, sources = await timed_tool_call(
            tool_runs, "retrieve_with_sources", retrieve_with_sources(query, empresa_id),
            input={"query": query, "empresa_id": empresa_id},
        )
        sources_acc.extend(sources)
        return context

    return StructuredTool.from_function(
        coroutine=_run, name="retrieve_with_sources", args_schema=_RetrieveKnowledgeArgs,
        description="Busca trechos relevantes na base de conhecimento (manuais, procedimentos, FAQ) para uma pergunta.",
    )


# Every builder above, keyed by the name an agent passes to build_toolkit().
# Not every agent gets every tool — see funcionario.py/dono.py for which
# subset each one binds; this registry just keeps one builder per tool
# instead of duplicating the closures in each agent module.
_TOOL_BUILDERS: dict[str, Callable[..., BaseTool]] = {
    "get_stock_alerts": _build_stock_alerts_tool,
    "get_inventory_status": _build_inventory_status_tool,
    "get_expiring_batches": _build_expiring_batches_tool,
    "get_kpis": _build_kpis_tool,
    "get_kpis_by_store": _build_kpis_by_store_tool,
    "get_sales_summary": _build_sales_summary_tool,
    "retrieve_with_sources": _build_retrieve_knowledge_tool,
}

# retrieve_with_sources also needs sources_acc — every other builder takes
# only (empresa_id, tool_runs).
_NEEDS_SOURCES_ACC = {"retrieve_with_sources"}


def build_toolkit(
    names: list[str], *, empresa_id: int, tool_runs: list[dict], sources_acc: list[dict],
) -> list[BaseTool]:
    """Builds this request's tool list for one agent. `empresa_id` is
    baked into each tool's closure here, once, for every tool it binds —
    see the module docstring for why that (not a runtime check) is what
    actually makes this tenant-safe."""
    settings = get_settings()
    if len(names) > settings.agent_tool_calling_max_tools:
        raise ValueError(
            f"{len(names)} tools requested, exceeds agent_tool_calling_max_tools "
            f"({settings.agent_tool_calling_max_tools}) — this is a deliberate ceiling on "
            "how many tools one agent call binds, review before raising it."
        )
    tools = []
    for name in names:
        builder = _TOOL_BUILDERS[name]
        if name in _NEEDS_SOURCES_ACC:
            tools.append(builder(empresa_id, tool_runs, sources_acc))
        else:
            tools.append(builder(empresa_id, tool_runs))
    return tools
