"""
Mottainai IA Layer — FastAPI main.py
API entry point. Defines all routes.

Note: HTTPException detail strings in this file are user/staff-facing (the
outermost API boundary of the product) and are deliberately kept in
Portuguese, like the agents' SYSTEM_PROMPT — the one exception is the /a2a
"unauthorized" message, kept in English for consistency with the rest of
app/integrations/mcp_a2a.py, which targets external systems, not end users.
"""
import asyncio
import time
from contextlib import asynccontextmanager
from decimal import Decimal

# SSL fix for macOS (Homebrew Python doesn't use the native Keychain by default)
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from typing import Annotated

import httpx
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError
from groq import APIConnectionError, APIError
from openai import OpenAIError
from pydantic import BaseModel, Field

from app.agents.governanca import run_auditoria_execucoes, run_relatorio_conformidade
from app.agents.runtime import get_llm_model_label
from app.agents.supervisor import MottainaiState, mottainai_graph, node_guardrail_saida
from app.database.mongo import get_mongo_db
from app.database.operational_schema import OPERATIONAL_SCHEMA_READY_QUERY
from app.integrations.mcp_a2a import a2a_agent_card, dispatch_a2a, dispatch_mcp
from app.memory.short_term import (
    SessionExpiredError,
    SessionOwnershipError,
    close_conversation,
    get_conversation,
    list_conversations,
    load_history,
)
from app.observability.executions import record_agent_execution
from app.observability.metrics import get_metrics_summary, record_execution_metrics
from app.security.auth import AuthContext, require_auth, require_roles
from config.settings import get_settings

# ─────────────────────────────────────────────
# Lifespan (startup/shutdown)
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Offline mode is only safe once the embedding model is already cached.
    if settings.transformers_offline:
        import os
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")

    # Startup: warm up embedding model
    from app.rag.retriever import get_embedding_model
    await asyncio.to_thread(get_embedding_model)
    try:
        yield
    finally:
        from app.database.redis_client import close_redis_pool
        await close_redis_pool()


settings = get_settings()

app = FastAPI(
    title="Mottainai IA Layer",
    description="""
## Mottainai — Multi-Agent Predictive Inventory Management API

Intelligent system combining LangGraph, LLMs (Groq/Llama 3.3) and computer
vision to reduce waste and optimize stock in small and mid-sized retailers.

### Available agents
| Role | Agent triggered | Capabilities |
|------|----------------|-------------|
| `ESTOQUISTA` | Employee Agent | Stock, alerts, expiration dates, damages |
| `GERENTE` | Employee Agent | Everything from Estoquista + AI suggestions |
| `DONO` | Owner Agent | KPIs, revenue, losses, multi-store, analytics |
| `CLIENTE` | Customer Agent / FAQ | Promotions, stores, loyalty and help |

### Message flow
```
Input guardrail → Supervisor → Domain agent → Judge → Output guardrail
```

### Authentication
All business routes require a JWT Bearer. `sub`, `empresa_id` and `role`
are verified claims; the API does not accept identity in the payload.
""",
    version="1.0.0",
    contact={"name": "Mottainai Team"},
    license_info={"name": "Private"},
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Chat", "description": "Interaction with the AI agents"},
        {"name": "Motor Preditivo", "description": "Automatic stock analysis and suggestion generation"},
        {"name": "Prateleira", "description": "Computer vision for shelf analysis"},
        {"name": "Métricas", "description": "Observability, cost and performance of the agents"},
        {"name": "Auditoria", "description": "Compliance and governance of AI decisions"},
        {"name": "Infra", "description": "Health check and infrastructure status"},
    ],
)


# ─────────────────────────────────────────────
# Request/Response schemas
# ─────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4096, description="Message for the AI agent")
    session_id: str = Field(..., description="Unique session ID to reuse throughout the conversation")

    model_config = {"extra": "forbid", "json_schema_extra": {"examples": [{"value": {
        "message": "Quais produtos vencem nos próximos 3 dias?",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
    }}]}}


class ChatResponse(BaseModel):
    session_id: str = Field(..., description="Session ID")
    response: str = Field(..., description="Response generated by the AI agent")
    agent: str = Field(..., description="Agent that processed the message", examples=["funcionario"])
    judge_approved: bool = Field(..., description="Whether the Judge approved the response (score >= 0.7)")
    judge_score: float = Field(..., description="Judge score (0.0 to 1.0)", ge=0.0, le=1.0)
    sources: list[dict] = Field(..., description="Data sources consulted by the agent")
    latency_s: float = Field(..., description="Total request latency in seconds")


class MetricsResponse(BaseModel):
    data: dict = Field(..., description="Observability and cost metrics")


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

async def _dependency_checks() -> dict[str, str]:
    """Checks dependencies without exposing internal connection details."""
    checks: dict[str, str] = {}

    try:
        db = get_mongo_db()
        await db.command("ping")
        checks["mongodb"] = "ok"
    except Exception:
        checks["mongodb"] = "unavailable"

    try:
        from app.database.redis_client import get_redis
        redis = get_redis()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"

    try:
        from sqlalchemy import text

        from app.database.postgres import get_pg_session

        async with get_pg_session() as session:
            result = await session.execute(
                text(OPERATIONAL_SCHEMA_READY_QUERY)
            )
            if not result.scalar():
                raise RuntimeError("Mottainai operational schema unavailable.")
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "unavailable"

    return checks


@app.get("/livez", tags=["Infra"])
async def live_check():
    """Indicates the HTTP process is running, without checking dependencies."""
    return {"status": "alive"}


@app.get("/readyz", tags=["Infra"])
async def readiness_check():
    """Indicates whether the API can receive data-dependent traffic."""
    checks = await _dependency_checks()
    ready = all(value == "ok" for value in checks.values())
    payload = {"status": "ready" if ready else "unavailable", "checks": checks}
    return JSONResponse(status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE, content=payload)


@app.get("/health", tags=["Infra"])
async def health_check():
    """Compatibility endpoint with the sanitized dependency status."""
    checks = await _dependency_checks()
    healthy = all(value == "ok" for value in checks.values())
    return {"status": "healthy" if healthy else "degraded", "checks": checks}


@app.get("/.well-known/agent-card.json", tags=["Integrações"])
async def agent_card(request: Request):
    """Agent Card for A2A discovery; does not expose business data."""
    return a2a_agent_card(str(request.base_url).rstrip("/"))


@app.post("/a2a", tags=["Integrações"])
async def a2a_message(payload: dict, authorization: str | None = Header(default=None)):
    """Receives authenticated A2A requests for allowed read actions."""
    result = await dispatch_a2a(payload, authorization)
    if (result.get("error") or {}).get("code") == "unauthorized":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized.")
    return result


@app.post("/mcp", tags=["Integrações"])
async def mcp_rpc(payload: dict, authorization: str | None = Header(default=None)):
    """HTTP transport for the MCP initialize, tools/list and tools/call methods."""
    return await dispatch_mcp(payload, authorization)


@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    principal: Annotated[AuthContext, Depends(require_auth)],
):
    """
    Main multi-agent chat endpoint.
    Runs the full LangGraph graph: guardrail → supervisor → agent → judge → output guardrail.
    """
    start_time = time.time()

    initial_state: MottainaiState = {
        "session_id": request.session_id,
        "empresa_id": principal.empresa_id,
        "usuario_id": principal.usuario_id,
        "user_role": principal.role,
        "user_input": request.message,
        "sanitized_input": "",
        "history": [],
        "memory": {},
        "selected_agent": "",
        "agent_response": "",
        "judge_approved": False,
        "judge_score": 0.0,
        "final_response": "",
        "error": None,
        "sources": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "node_latencies_ms": {},
    }

    try:
        result = await mottainai_graph.ainvoke(initial_state)
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except SessionExpiredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (APIConnectionError, APIError, OpenAIError) as exc:
        latency = time.time() - start_time
        await record_agent_execution(
            empresa_id=principal.empresa_id,
            session_id=request.session_id,
            agent="unavailable",
            status="error",
            latency_s=latency,
            error=type(exc).__name__,
        )
        await record_execution_metrics(
            session_id=request.session_id,
            conversation_id=None,
            agent="unavailable",
            skill=None,
            model=get_llm_model_label(),
            input_tokens=0,
            output_tokens=0,
            latency_s=latency,
            empresa_id=principal.empresa_id,
            status="error",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="O modelo de IA configurado está indisponível. Verifique a disponibilidade do provedor ou do modelo e tente novamente.",
        ) from exc
    latency = time.time() - start_time

    execution_status = "error" if result.get("error") else "completed"
    if result.get("error") and not result.get("final_response"):
        # BackgroundTasks doesn't run when the route raises an exception; persist the error now.
        await record_agent_execution(
            empresa_id=principal.empresa_id,
            session_id=request.session_id,
            conversation_id=result.get("conversation_id"),
            agent=result.get("selected_agent", "unknown"),
            status="error",
            latency_s=latency,
            node_latencies_ms=result.get("node_latencies_ms", {}),
            error=result["error"],
        )
        await record_execution_metrics(
            session_id=request.session_id,
            conversation_id=result.get("conversation_id"),
            agent=result.get("selected_agent", "unknown"),
            skill=None,
            model=get_llm_model_label(),
            input_tokens=result.get("input_tokens", 0),
            output_tokens=result.get("output_tokens", 0),
            latency_s=latency,
            judge_score=result.get("judge_score"),
            empresa_id=principal.empresa_id,
            status="error",
            node_latencies_ms=result.get("node_latencies_ms", {}),
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["error"])

    background_tasks.add_task(
        record_agent_execution,
        empresa_id=principal.empresa_id,
        session_id=request.session_id,
        conversation_id=result.get("conversation_id"),
        agent=result.get("selected_agent", "unknown"),
        status="completed",
        latency_s=latency,
        node_latencies_ms=result.get("node_latencies_ms", {}),
    )

    # Metrics in the background (does not block the response)
    background_tasks.add_task(
        record_execution_metrics,
        session_id=request.session_id,
        conversation_id=result.get("conversation_id"),
        agent=result.get("selected_agent", "unknown"),
        skill=None,
        model=get_llm_model_label(),
        input_tokens=result.get("input_tokens", 0),
        output_tokens=result.get("output_tokens", 0),
        latency_s=latency,
        judge_score=result.get("judge_score"),
        empresa_id=principal.empresa_id,
        status=execution_status,
        node_latencies_ms=result.get("node_latencies_ms", {}),
    )

    # Audit in the background (does not block)
    background_tasks.add_task(
        _run_governanca_async,
        principal.empresa_id,
        result.get("selected_agent", ""),
        request.message,
    )

    return ChatResponse(
        session_id=request.session_id,
        response=result.get("final_response", ""),
        agent=result.get("selected_agent", "unknown"),
        judge_approved=result.get("judge_approved", False),
        judge_score=result.get("judge_score", 0.0),
        sources=result.get("sources", []),
        latency_s=round(latency, 3),
    )


@app.get("/chat/history/{session_id}", tags=["Chat"])
async def get_chat_history(session_id: str, principal: Annotated[AuthContext, Depends(require_auth)]):
    """Returns only history belonging to the authenticated principal."""
    try:
        conversation = await get_conversation(session_id, principal.empresa_id, principal.usuario_id)
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sessão não encontrada.")
    history = await load_history(session_id, limit=50)
    return {"session_id": session_id, "status": conversation["status"], "messages": [
        {"role": m.__class__.__name__.replace("Message", "").lower(), "content": m.content} for m in history
    ]}


@app.get("/chat/sessions", tags=["Chat"])
async def get_chat_sessions(
    principal: Annotated[AuthContext, Depends(require_auth)], limit: int = 20,
):
    """Lists only sessions belonging to the authenticated principal."""
    return {"sessions": await list_conversations(principal.empresa_id, principal.usuario_id, limit)}


@app.post("/chat/sessions/{session_id}/close", tags=["Chat"])
async def close_chat_session(session_id: str, principal: Annotated[AuthContext, Depends(require_auth)]):
    """Closes a session belonging to the authenticated principal."""
    closed = await close_conversation(session_id, principal.empresa_id, principal.usuario_id)
    if not closed:
        try:
            conversation = await get_conversation(session_id, principal.empresa_id, principal.usuario_id)
        except SessionOwnershipError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if not conversation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sessão não encontrada.")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A sessão não está ativa.")
    return {"session_id": session_id, "status": "closed"}


@app.post("/motor-preditivo/trigger", tags=["Motor Preditivo"])
async def trigger_motor_preditivo(principal: Annotated[AuthContext, Depends(require_roles("DONO"))]):
    """Triggers the predictive engine for the authenticated owner's company."""
    state: MottainaiState = {
        "session_id": f"motor-{principal.empresa_id}-{int(time.time())}",
        "empresa_id": principal.empresa_id,
        "usuario_id": principal.usuario_id,
        "user_role": principal.role,
        "user_input": "trigger automático",
        "sanitized_input": "trigger automático",
        "history": [],
        "memory": {},
        "selected_agent": "motor_preditivo",
        "agent_response": "",
        "judge_approved": False,
        "judge_score": 0.0,
        "final_response": "",
        "error": None,
        "sources": [],
        "input_tokens": 0,
        "output_tokens": 0,
    }

    from app.agents.juiz import node_agente_juiz
    from app.agents.motor_preditivo import node_motor_preditivo

    try:
        result = await node_motor_preditivo(state)
        result = await node_agente_juiz(result)
        result = await node_guardrail_saida(result)
    except (APIConnectionError, APIError, OpenAIError, httpx.HTTPError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="O modelo de IA configurado está indisponível. Verifique a disponibilidade do provedor ou do modelo e tente novamente.",
        ) from exc

    return {
        "empresa_id": principal.empresa_id,
        "analysis": result.get("final_response", ""),
        "judge_score": result.get("judge_score"),
        "sources": result.get("sources", []),
    }


class DescartarLoteRequest(BaseModel):
    store_id: int = Field(..., gt=0)
    batch_id: int = Field(..., gt=0)
    quantity: Decimal = Field(..., gt=0, description="Quantidade descartada")
    reason: str = Field(..., min_length=1, max_length=100)
    observation: str | None = Field(None, max_length=2000)

    model_config = {"extra": "forbid"}


class ReceberMercadoriaRequest(BaseModel):
    store_id: int = Field(..., gt=0)
    batch_id: int = Field(..., gt=0)
    quantity: Decimal = Field(..., gt=0, description="Quantidade recebida")
    observation: str | None = Field(None, max_length=2000)

    model_config = {"extra": "forbid"}


@app.post("/funcionario/descartar-lote", tags=["Funcionário"])
async def descartar_lote(
    body: DescartarLoteRequest,
    principal: Annotated[AuthContext, Depends(require_roles("ESTOQUISTA", "GERENTE", "DONO"))],
):
    """
    Registers a batch disposal: writes the disposal/disposal_item audit
    rows and atomically decrements the matching inventory row (with its own
    inventory_movement audit trail).
    """
    from app.tools.postgres_tools import discard_batch

    try:
        result = await discard_batch(
            empresa_id=principal.empresa_id,
            store_id=body.store_id,
            batch_id=body.batch_id,
            employee_id=principal.usuario_id,
            quantity=body.quantity,
            reason=body.reason,
            observation=body.observation,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return result


@app.post("/funcionario/receber-mercadoria", tags=["Funcionário"])
async def receber_mercadoria(
    body: ReceberMercadoriaRequest,
    principal: Annotated[AuthContext, Depends(require_roles("ESTOQUISTA", "GERENTE", "DONO"))],
):
    """
    Registers receipt of goods for a batch already tracked in inventory at
    that store (e.g. confirming a restock). Does not create new products or
    batches — that's a separate, bigger feature intentionally left out of
    scope here.
    """
    from app.tools.postgres_tools import receive_inventory

    try:
        result = await receive_inventory(
            empresa_id=principal.empresa_id,
            store_id=body.store_id,
            batch_id=body.batch_id,
            employee_id=principal.usuario_id,
            quantity=body.quantity,
            observation=body.observation,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return result


@app.get("/metrics/summary", tags=["Métricas"])
async def metrics_summary(
    principal: Annotated[AuthContext, Depends(require_roles("DONO"))], days: int = 7,
):
    """Observability dashboard for the authenticated owner's company."""
    return MetricsResponse(data=await get_metrics_summary(principal.empresa_id, days))


@app.get("/audit/report", tags=["Auditoria"])
async def audit_report(principal: Annotated[AuthContext, Depends(require_roles("DONO"))]):
    """Compliance report for the authenticated owner's company."""
    audit = await run_auditoria_execucoes(principal.empresa_id)
    report = await run_relatorio_conformidade(principal.empresa_id)
    return {"auditoria": audit, "relatorio": report}


@app.post("/shelf/analyze", tags=["Prateleira"])
async def analyze_shelf_image(
    principal: Annotated[AuthContext, Depends(require_roles("ESTOQUISTA", "GERENTE", "DONO"))],
    image: UploadFile = File(..., description="Foto da prateleira (jpg/png/webp)"),
    store_id: int | None = None,
    session_id: str | None = None,
):
    """
    Analyzes a shelf photo with Gemini computer vision.

    Returns:
      - Identified products with estimated quantity and position
      - Empty slots (visual stockout)
      - Shelf occupancy %
      - Cross-check against PostgreSQL inventory
      - Recommended actions for the employee
      - Readable text report

    Format: multipart/form-data
      - image: image file
      - empresa_id: int
      - store_id: int (optional)
      - session_id: str (optional — for traceability in MongoDB)
    """
    # Validates the file type
    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if image.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Tipo de arquivo não suportado: {image.content_type}. Use jpg, png ou webp.",
        )
    if store_id is not None and (isinstance(store_id, bool) or store_id < 1):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="store_id deve ser um inteiro positivo.",
        )

    conversation_id = None
    if session_id:
        try:
            conversation = await get_conversation(session_id, principal.empresa_id, principal.usuario_id)
        except SessionOwnershipError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if not conversation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sessão não encontrada.")
        conversation_id = conversation["_id"]

    # 10MB limit
    MAX_SIZE = 10 * 1024 * 1024
    image_bytes = await image.read()
    if len(image_bytes) > MAX_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Imagem muito grande. Máximo: 10MB.",
        )

    from app.agents.visao import analyze_shelf
    try:
        result = await analyze_shelf(
            image_bytes=image_bytes,
            image_mime_type=image.content_type,
            empresa_id=principal.empresa_id,
            usuario_id=principal.usuario_id,
            store_id=store_id,
            session_id=session_id,
            conversation_id=conversation_id,
        )
    except (httpx.HTTPError, GoogleAPICallError, GoogleAuthError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="O provedor de visão está indisponível. Tente novamente mais tarde.",
        ) from exc

    return {
        "empresa_id": principal.empresa_id,
        "store_id": store_id,
        "estado_geral": result.get("estado_geral"),
        "ocupacao_pct": result.get("ocupacao_pct"),
        "confianca_analise": result.get("confianca_analise"),
        "produtos_detectados": result.get("produtos_detectados", []),
        "slots_vazios": result.get("slots_vazios", {}),
        "cruzamento_inventario": result.get("cruzamento_inventario", {}),
        "acoes_sugeridas": result.get("acoes_sugeridas", []),
        "relatorio_texto": result.get("relatorio_texto", ""),
    }


# ─────────────────────────────────────────────
# Background helpers
# ─────────────────────────────────────────────

async def _run_governanca_async(empresa_id: int, agent: str, action: str) -> None:
    """Runs access audit in the background."""
    from app.agents.governanca import run_controle_acesso
    await run_controle_acesso(empresa_id, agent, action)
