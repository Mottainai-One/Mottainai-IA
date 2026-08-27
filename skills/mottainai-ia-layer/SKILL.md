---
name: Mottainai IA Layer
description: Desenvolva, revise e valide a IA Layer do Mottainai (FastAPI, LangChain/LangGraph, MongoDB, RAG, agentes, MCP/A2A, guardrails e SRE) sem quebrar os controles de segurança e rastreabilidade.
metadata:
  author: Arthur Silva
  category: development
  version: "1.0.0"
---

# Mottainai IA Layer

Use esta skill ao implementar, revisar, depurar ou documentar a IA Layer neste repositório.

## Princípios obrigatórios

- Leia o código real antes de alterar. `ARQUITETURA.md` pode estar defasado.
- Não exponha, copie ou registre chaves, URIs com senha, tokens ou credenciais.
- Não declare a API funcional sem smoke test real: compilação e OpenAPI não validam Mongo, Redis, PostgreSQL ou LLM.
- PostgreSQL é a fonte da verdade operacional; MongoDB é a camada de IA.
- Nunca permita que o agente Cliente/FAQ acesse dados internos, estoque ou dados de terceiros.
- Toda resposta de agente deve atravessar: guardrail de entrada → domínio → Juiz → guardrail de saída.
- O Juiz deve ser fail-closed: erro na avaliação não pode liberar uma resposta.

## Arquitetura atual

### Stack

- API: FastAPI (`app/main.py`)
- Orquestração: LangGraph (`app/agents/supervisor.py`)
- LLM: LangChain `ChatGroq`; Gemini apenas para visão computacional
- PostgreSQL: operações de negócio e analytics
- MongoDB: sessões, mensagens, memórias, RAG, avaliações, métricas e auditoria
- Redis: rate limit e notificações
- Embeddings locais: `sentence-transformers/all-MiniLM-L6-v2`

### Agentes no fluxo de chat

| Agente | Perfil/uso | Fontes permitidas |
|---|---|---|
| Supervisor | roteamento por role/intenção | nenhuma ferramenta |
| Cliente | promoções, lojas e fidelidade | RAG + memória permitida |
| FAQ | dúvidas gerais do app | RAG + histórico; sem fonte transacional |
| Funcionário | estoquista e gerente | PostgreSQL, Redis e RAG |
| Dono | KPIs e recomendações | PostgreSQL e RAG |
| Motor Preditivo | demanda, perdas e reposição | PostgreSQL + Open-Meteo |
| Juiz | grounding, escopo e confiança | fontes registradas + resposta |

Serviços auxiliares: Governança/Auditoria e Visão Computacional.

### Roles aceitos

- `ESTOQUISTA`, `GERENTE` → `funcionario`
- `DONO` → `dono`; intenção preditiva → `motor_preditivo`
- `CLIENTE` → `cliente`; dúvidas/ajuda/fidelidade/FAQ → `faq`

## Sessões e memória

- A sessão é identificada por `session_id` e pertence obrigatoriamente a `empresa_id + usuario_id`.
- Sessões expiram por inatividade conforme `SESSION_TIMEOUT_MINUTES` e podem ser fechadas explicitamente.
- Endpoints: `GET /chat/sessions`, `GET /chat/history/{session_id}`, `POST /chat/sessions/{session_id}/close`.
- Memória longa fica em `memories`; somente fatos/preferências explícitos, curtos e sem PII podem ser persistidos.
- Nunca armazene CPF, cartão, e-mail, telefone, senha, token ou segredo em memória.

## Anti-alucinação e RAG

- RAG interno: `rag_documents` e `rag_chunks` no MongoDB.
- Fonte externa obrigatória para análise preditiva: Open-Meteo, com fonte adicionada ao campo `sources`.
- Todo agente que usar RAG deve retornar `sources`; `save_message` deve persistir as fontes.
- O Juiz avalia grounding e escopo. Score menor que `0.7` exige resposta revisada ou fallback seguro.
- Guardrail de entrada bloqueia prompt injection/rate limit e remove PII. Guardrail de saída remove PII residual e bloqueia vazamento de credenciais.

## MCP e A2A

- MCP HTTP JSON-RPC: `POST /mcp`; suporta `initialize`, `tools/list`, `tools/call`.
- A2A: `GET /.well-known/agent-card.json` e `POST /a2a`.
- Ambos exigem `MCP_SHARED_TOKEN`/`A2A_SHARED_TOKEN` via Bearer token e têm allowlist somente leitura:
  - `get_active_alerts`
  - `get_company_kpis`
- Nunca adicione ferramenta MCP de escrita sem autenticação forte, autorização por tenant e confirmação explícita.

## Observabilidade / SRE

Em cada `/chat`, registrar em Mongo:

- status (`completed`/`error`), agente e erro sanitizado;
- latência total e por nó do LangGraph;
- tokens de entrada/saída e custo estimado;
- score do Juiz e fontes.

`GET /metrics/summary` deve fornecer custo para 100/1000 usuários, latência total e interagentes, índice de erro, custo por resolução aprovada e ROI com premissas explícitas.

## Configuração segura

Use apenas `.env` local e `.env.example` com placeholders.

- `GROQ_API_KEY`, `GEMINI_API_KEY`
- `POSTGRES_DSN` ou `DATABASE_URL`
- `MONGO_URI` ou `MONGO_URL`, `MONGO_DB`, `REDIS_URL`
- `SESSION_TIMEOUT_MINUTES`
- `MCP_SHARED_TOKEN`, `A2A_SHARED_TOKEN`, `PUBLIC_BASE_URL`

Se um provedor externo for bloqueado pela rede, devolva `503` seguro e registre a indisponibilidade; nunca tente burlar proxy corporativo.

## Checklist antes de concluir

1. `python -m compileall -q app scripts`
2. Subir a API e consultar `GET /health`.
3. Testar `/chat` por role: `CLIENTE`, `ESTOQUISTA`, `DONO`.
4. Testar criação, leitura, ownership, expiração e encerramento de sessão.
5. Confirmar fontes RAG no retorno/persistência da mensagem.
6. Testar reprovação/falha do Juiz e validar fallback seguro.
7. Testar MCP/A2A sem token (nega) e com token válido (allowlist).
8. Consultar `/metrics/summary` após conversas reais.
