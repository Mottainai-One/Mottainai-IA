# Mottainai IA Layer — Arquitetura de Alto Nível

```
Requisição do Usuário (HTTP POST /chat)
              │
              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    GUARDRAIL DE ENTRADA (determinístico)                │
│  ► Sanitização de prompt injection  ► Validação de payload              │
│  ► Rate-limit por usuário (Redis)   ► Remoção de PII de entrada         │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ (se bloqueado → retorna erro ao usuário)
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              LOAD CONTEXT (MongoDB)                                     │
│  ► Carrega histórico da sessão (messages)                               │
│  ► Carrega memória de longo prazo (memories) — preferências e fatos     │
└────────────────────────────────┬────────────────────────────────────────┘
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              SUPERVISOR (LangGraph — nó raiz)                           │
│  ► Detecta intenção por perfil (role) + keywords                        │
│  ► Roteia para o agente correto                                         │
│  ► NÃO usa RAG, NÃO usa tools, NÃO escreve memória                      │
│  ► Registra roteamento em routing_logs (MongoDB)                        │
└────────┬────────────┬───────────────┬──────────────────────────────────┘
         │            │               │
         ▼            ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌─────────────────────┐
│ AGENTE       │ │ AGENTE       │ │ AGENTE       │ │ MOTOR PREDITIVO     │
│ CLIENTE      │ │ FUNCIONÁRIO  │ │ DONO         │ │ (autônomo)          │
│              │ │              │ │              │ │                     │
│ Skills:      │ │ Skills:      │ │ Skills:      │ │ Subagentes:         │
│ Promoções    │ │ Estoque      │ │ KPIs         │ │ Previsão Demanda    │
│ Lojas        │ │ Inventário   │ │ Relatórios   │ │ Risco de Perda      │
│ Fidelidade   │ │ Alertas      │ │ BI           │ │ Ação Sugerida       │
│ Sustentab.   │ │ Procedimentos│ │ Analytics    │ │ Pré-Lista Repos.    │
│ FAQ          │ │ Entrada Merc.│ │ Recomendações│ │                     │
│              │ │              │ │              │ │ Fontes:             │
│ Fontes:      │ │ Fontes:      │ │ Fontes:      │ │ PostgreSQL +        │
│ RAG (MongoDB)│ │ PostgreSQL + │ │ PostgreSQL + │ │ Open-Meteo (MCP/A2A)│
│              │ │ Redis +      │ │ RAG          │ │                     │
│              │ │ RAG          │ │              │ │ Escreve em:         │
│              │ │              │ │              │ │ alert + suggested   │
│              │ │ Escreve:     │ │ Escreve:     │ │ _action (Postgres)  │
│              │ │ memories     │ │ memories     │ │                     │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘ └──────────┬──────────┘
       │                │               │                     │
       └────────────────┴───────────────┴─────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     AGENTE JUIZ (anti-alucinação)                       │
│  1. Grounding Check — resposta suportada pelas fontes?                  │
│  2. Escopo/Vazamento — dado não autorizado para o perfil?               │
│  3. Score de Confiança (0.0 – 1.0)                                      │
│  4. Fallback Handler — se < 0.7: reformula ou resposta segura           │
│  ► Registra em prompt_evaluations (MongoDB)                             │
└────────────────────────────────┬────────────────────────────────────────┘
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                GUARDRAIL DE SAÍDA (determinístico)                      │
│  ► Bloqueia PII/dado sensível residual                                  │
│  ► Bloqueia vazamento de credenciais/dados internos                     │
│  ► Trunca resposta excessivamente longa                                 │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
                     Resposta ao Usuário
                                 │
                                 ├─── (async) Métricas → MongoDB (metrics)
                                 └─── (async) Governança → Auditoria
                                                │
                               ┌────────────────┴──────────────────┐
                               │    AGENTE GOVERNANÇA/AUDITORIA    │
                               │    (assíncrono, não bloqueia)     │
                               │  Auditoria de Execuções           │
                               │  Controle de Acesso e Escopo      │
                               │  Rastreabilidade de Decisões      │
                               │  Relatório de Conformidade        │
                               └───────────────────────────────────┘
```

## Stack de Infraestrutura

```
┌────────────────────────────────────────────────────────────────┐
│                    FastAPI (porta 8000)                        │
│            Rotas: /chat  /chat/history  /health                │
│            /motor-preditivo/trigger  /metrics/summary          │
│            /audit/report                                       │
├────────────────────────────────────────────────────────────────┤
│ LangChain + LangGraph │  Groq LLM (llama-3.3-70b, gratuito)    │
├────────────────────────────────────────────────────────────────┤
│  PostgreSQL 15       │  MongoDB 7         │  Redis 7           │
│  (fonte da verdade)  │  (camada IA)       │  (notificações)    │
│  schema: mottainai   │  22 coleções       │  ZSET + HASH       │
├────────────────────────────────────────────────────────────────┤
│  Embeddings: sentence-transformers/all-MiniLM-L6-v2 (local )   │
│  API externa: Open-Meteo (gratuita, CC BY 4.0) via MCP/A2A     │
└────────────────────────────────────────────────────────────────┘
```


## Requisitos da Matéria — Mapeamento

| Requisito | Implementação |
|-----------|---------------|
| FastAPI | `app/main.py` |
| 5+ agentes | 7 agentes: Supervisor, Cliente, Funcionário, Dono, Motor Preditivo, Juiz, Governança |
| LangChain | Todos os agentes usam `ChatGroq` (LangChain) |
| LangGraph | `supervisor.py` — `StateGraph` com nós e arestas condicionais |
| Sessão por usuário | `session_id` → MongoDB `conversations` + Redis rate-limit |
| Memória de longo prazo | `memory/long_term.py` → MongoDB `memories` |
| MCP / A2A | `tools/mcp_tools.py` — Open-Meteo como agente externo MCP |
| RAG + fonte externa | `rag/retriever.py` (MongoDB) + `rag/external_source.py` (Open-Meteo) |
| Agente Juiz | `agents/juiz.py` — grounding, escopo, confidence score, fallback |
| Guardrail | `guardrails/entrada.py` + `guardrails/saida.py` |
| Observabilidade | `observability/metrics.py` → `/metrics/summary` |
| Custo 100/1000 usuários | `get_metrics_summary()` — projeção automática |
| Latência inter-agentes | Registrado em `metrics` por agente |
| Índice de erros | `indice_erros_pct` no summary |
| ROI / Custo por resolução | `roi` e `custo_por_resolucao_usd` no summary |
| Arquitetura alto nível | Este documento |
