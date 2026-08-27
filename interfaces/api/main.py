"""
Mottainai IA Layer — FastAPI main.py
Ponto de entrada da API. Define todas as rotas.
"""
import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

# Fix SSL no macOS (Python do Homebrew não usa o Keychain nativo por padrão)
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse
from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError
from groq import APIConnectionError, APIError
from openai import OpenAIError
import httpx
from pydantic import BaseModel, Field

from app.agents.governanca import run_auditoria_execucoes, run_relatorio_conformidade
from app.agents.runtime import get_llm_model_label
from app.agents.supervisor import MottainaiState, mottainai_graph, node_guardrail_saida
from app.database.mongo import get_mongo_db
from app.database.operational_schema import OPERATIONAL_SCHEMA_READY_QUERY
from app.memory.short_term import (
    SessionExpiredError,
    SessionOwnershipError,
    close_conversation,
    get_conversation,
    list_conversations,
    load_history,
)
from app.integrations.mcp_a2a import a2a_agent_card, dispatch_a2a, dispatch_mcp
from app.observability.executions import record_agent_execution
from app.observability.metrics import get_metrics_summary, record_execution_metrics
from app.security.auth import AuthContext, require_auth, require_roles
from config.settings import get_settings


# ─────────────────────────────────────────────
# Lifespan (startup/shutdown)
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # O modo offline só é seguro quando o modelo de embeddings já está em cache.
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
## Mottainai — API Multiagente de Gestão Preditiva de Estoque

Sistema inteligente que combina LangGraph, LLMs (Groq/Llama 3.3) e visão computacional
para reduzir desperdício e otimizar estoques em pequenos e médios varejos.

### Agentes disponíveis
| Role | Agente acionado | Capacidades |
|------|----------------|-------------|
| `ESTOQUISTA` | Agente Funcionário | Estoque, alertas, vencimentos, avarias |
| `GERENTE` | Agente Funcionário | Tudo do Estoquista + sugestões da IA |
| `DONO` | Agente Dono | KPIs, faturamento, perdas, multi-loja, analytics |
| `CLIENTE` | Agente Cliente / FAQ | Promoções, lojas, fidelidade e ajuda |

### Fluxo de uma mensagem
```
Guardrail entrada → Supervisor → Agente de domínio → Juiz → Guardrail saída
```

### Autenticação
Todas as rotas de negócio exigem JWT Bearer. `sub`, `empresa_id` e `role` são
claims verificadas; a API não aceita identidade no payload.
""",
    version="1.0.0",
    contact={"name": "Mottainai Team"},
    license_info={"name": "Privado"},
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Chat", "description": "Interação com os agentes de IA"},
        {"name": "Motor Preditivo", "description": "Análise automática de estoque e geração de sugestões"},
        {"name": "Prateleira", "description": "Visão computacional para análise de prateleiras"},
        {"name": "Métricas", "description": "Observabilidade, custo e performance dos agentes"},
        {"name": "Auditoria", "description": "Conformidade e governança das decisões da IA"},
        {"name": "Infra", "description": "Health check e status da infraestrutura"},
    ],
)


# ─────────────────────────────────────────────
# Schemas de Request/Response
# ─────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4096, description="Mensagem para o agente de IA")
    session_id: str = Field(..., description="ID único da sessão a ser reutilizado durante a conversa")

    model_config = {"extra": "forbid", "json_schema_extra": {"examples": [{"value": {
        "message": "Quais produtos vencem nos próximos 3 dias?",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
    }}]}}


class ChatResponse(BaseModel):
    session_id: str = Field(..., description="ID da sessão")
    response: str = Field(..., description="Resposta gerada pelo agente de IA")
    agent: str = Field(..., description="Agente que processou a mensagem", examples=["funcionario"])
    judge_approved: bool = Field(..., description="Se o Juiz aprovou a resposta (score >= 0.7)")
    judge_score: float = Field(..., description="Pontuação do Juiz (0.0 a 1.0)", ge=0.0, le=1.0)
    sources: list[dict] = Field(..., description="Fontes de dados consultadas pelo agente")
    latency_s: float = Field(..., description="Latência total da requisição em segundos")


class MetricsResponse(BaseModel):
    data: dict = Field(..., description="Métricas de observabilidade e custo")


# ─────────────────────────────────────────────
# Rotas
# ─────────────────────────────────────────────

async def _dependency_checks() -> dict[str, str]:
    """Verifica dependências sem expor detalhes internos de conexão."""
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
        from app.database.postgres import get_pg_session
        from sqlalchemy import text

        async with get_pg_session() as session:
            result = await session.execute(
                text(OPERATIONAL_SCHEMA_READY_QUERY)
            )
            if not result.scalar():
                raise RuntimeError("Schema operacional Mottainai indisponível.")
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "unavailable"

    return checks


@app.get("/livez", tags=["Infra"])
async def live_check():
    """Indica que o processo HTTP está em execução, sem consultar dependências."""
    return {"status": "alive"}


@app.get("/readyz", tags=["Infra"])
async def readiness_check():
    """Indica se a API pode receber tráfego dependente de dados."""
    checks = await _dependency_checks()
    ready = all(value == "ok" for value in checks.values())
    payload = {"status": "ready" if ready else "unavailable", "checks": checks}
    return JSONResponse(status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE, content=payload)


@app.get("/health", tags=["Infra"])
async def health_check():
    """Endpoint de compatibilidade com o status sanitizado das dependências."""
    checks = await _dependency_checks()
    healthy = all(value == "ok" for value in checks.values())
    return {"status": "healthy" if healthy else "degraded", "checks": checks}


@app.get("/.well-known/agent-card.json", tags=["Integrações"])
async def agent_card(request: Request):
    """Agent Card para descoberta A2A; não expõe dados de negócio."""
    return a2a_agent_card(str(request.base_url).rstrip("/"))


@app.post("/a2a", tags=["Integrações"])
async def a2a_message(payload: dict, authorization: str | None = Header(default=None)):
    """Recebe solicitações A2A autenticadas para ações de leitura permitidas."""
    result = await dispatch_a2a(payload, authorization)
    if (result.get("error") or {}).get("code") == "unauthorized":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Não autorizado.")
    return result


@app.post("/mcp", tags=["Integrações"])
async def mcp_rpc(payload: dict, authorization: str | None = Header(default=None)):
    """Transport HTTP para os métodos MCP initialize, tools/list e tools/call."""
    return await dispatch_mcp(payload, authorization)


@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    principal: Annotated[AuthContext, Depends(require_auth)],
):
    """
    Endpoint principal de chat multiagente.
    Executa o grafo LangGraph completo: guardrail → supervisor → agente → juiz → guardrail saída.
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
        # BackgroundTasks não roda quando a rota devolve exceção; persiste o erro agora.
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

    # Métricas em background (não bloqueia resposta)
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

    # Auditoria em background (não bloqueia)
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
    """Retorna apenas histórico pertencente ao principal autenticado."""
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
    """Lista apenas sessões do principal autenticado."""
    return {"sessions": await list_conversations(principal.empresa_id, principal.usuario_id, limit)}


@app.post("/chat/sessions/{session_id}/close", tags=["Chat"])
async def close_chat_session(session_id: str, principal: Annotated[AuthContext, Depends(require_auth)]):
    """Encerra uma sessão pertencente ao principal autenticado."""
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
    """Aciona o motor preditivo para a empresa do dono autenticado."""
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

    from app.agents.motor_preditivo import node_motor_preditivo
    from app.agents.juiz import node_agente_juiz

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


@app.get("/metrics/summary", tags=["Métricas"])
async def metrics_summary(
    principal: Annotated[AuthContext, Depends(require_roles("DONO"))], days: int = 7,
):
    """Dashboard de observabilidade da empresa do dono autenticado."""
    return MetricsResponse(data=await get_metrics_summary(principal.empresa_id, days))


@app.get("/audit/report", tags=["Auditoria"])
async def audit_report(principal: Annotated[AuthContext, Depends(require_roles("DONO"))]):
    """Relatório de conformidade da empresa do dono autenticado."""
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
    Analisa uma foto de prateleira com visão computacional Gemini.

    Retorna:
      - Produtos identificados com quantidade estimada e posição
      - Slots vazios (ruptura visual)
      - % de ocupação da prateleira
      - Cruzamento com inventário do PostgreSQL
      - Ações recomendadas ao funcionário
      - Relatório em texto legível

    Formato: multipart/form-data
      - image: arquivo de imagem
      - empresa_id: int
      - store_id: int (opcional)
      - session_id: str (opcional — para rastreabilidade no MongoDB)
    """
    # Valida tipo de arquivo
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

    # Limite de 10MB
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
    """Roda auditoria de acesso em background."""
    from app.agents.governanca import run_controle_acesso
    await run_controle_acesso(empresa_id, agent, action)
