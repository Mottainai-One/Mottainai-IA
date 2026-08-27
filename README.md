# Mottainai IA Layer

API multiagente (FastAPI + LangChain + LangGraph) que orquestra agentes de IA sobre o banco operacional do Mottainai — um sistema de gestão preditiva de estoque para varejo, com foco em reduzir desperdício e perdas.

PostgreSQL é a fonte da verdade do negócio (vendas, estoque, lotes, alertas). MongoDB é a camada de IA (sessões, memória, RAG, auditoria). Redis cuida de rate limit e notificações. O LLM de conversa roda via Groq (gratuito) por padrão, com Ollama local como alternativa 100% offline.

## Sobre o projeto

Cada tipo de usuário do Mottainai conversa com um agente diferente, todos atrás do mesmo endpoint `/chat`:

| Papel | Agente acionado | Ajuda com |
|---|---|---|
| `CLIENTE` | Cliente / FAQ | Promoções, lojas, fidelidade, sustentabilidade |
| `ESTOQUISTA` / `GERENTE` | Funcionário | Estoque, alertas, validade, procedimentos |
| `DONO` | Dono | KPIs, faturamento, perdas, analytics, ROI |

Um Motor Preditivo autônomo (não reativo — roda por trigger/schedule) cruza histórico de vendas com previsão do tempo (Open-Meteo) para prever demanda, detectar risco de perda e sugerir ações (promoção relâmpago, transferência, doação, descarte). Um Agente de Visão analisa fotos de prateleira via Gemini. Um Agente Juiz audita toda resposta antes dela sair. Um Agente de Governança audita o sistema de forma assíncrona.

## Requisitos do projeto — o que está implementado

| Requisito | Onde |
|---|---|
| API FastAPI | `interfaces/api/main.py` |
| LLM sem custo (Groq/Ollama, sem gasto da escola) | `config/settings.py` — `LLM_PROVIDER=groq\|ollama\|ollama_local` |
| 5+ agentes multiagente | 7: Cliente, FAQ, Funcionário, Dono, Motor Preditivo, Visão, Juiz — orquestrados pelo Supervisor |
| LangChain | Todos os agentes usam `ChatGroq`/`ChatOpenAI` (`app/agents/runtime.py`) |
| LangGraph | `app/agents/supervisor.py` — `StateGraph` com nós e arestas condicionais |
| Sessão por usuário | JWT (`sub`/`empresa_id`/`role`) + `conversations` no MongoDB, ownership validado |
| Memória de longo prazo | `app/memory/long_term.py` → coleção `memories` |
| MCP | `POST /mcp` — `initialize`, `tools/list`, `tools/call` (`app/integrations/mcp_a2a.py`) |
| A2A | `POST /a2a` + `/.well-known/agent-card.json` (descoberta) |
| RAG com fontes indicadas | `app/rag/retriever.py` (MongoDB) — toda resposta traz `sources` |
| Fonte externa consumida | Open-Meteo (API pública de clima) via MCP no Motor Preditivo |
| Agente Juiz anti-alucinação | `app/agents/juiz.py` — grounding, escopo, confidence score, fail-closed de verdade |
| Guardrail de entrada/saída | `app/guardrails/entrada.py` + `saida.py` |
| Observabilidade: custo, latência, erros, ROI, custo/resolução, projeção 100/1000 usuários | `GET /metrics/summary` (`app/observability/metrics.py`) |
| Arquitetura de alto nível | Seção abaixo e [ARQUITETURA.md](ARQUITETURA.md) (diagrama completo) |

## Arquitetura

Fluxo fixo de toda mensagem de chat (não muda entre agentes) — versão completa com diagrama de todos os agentes lado a lado em [ARQUITETURA.md](ARQUITETURA.md):

```
Requisição (POST /chat)
   │
   ▼
Guardrail de entrada  ──(bloqueado)──▶ retorna erro ao usuário
   │  sanitização, anti prompt-injection, rate limit (Redis)
   ▼
Load Context (MongoDB)
   │  histórico da sessão + memória de longo prazo
   ▼
Supervisor (LangGraph)
   │  roteia por perfil + intenção → não usa RAG nem tools
   ├──▶ Agente Cliente / FAQ  (RAG)
   ├──▶ Agente Funcionário    (PostgreSQL + Redis + RAG)
   ├──▶ Agente Dono           (PostgreSQL analytics + RAG)
   └──▶ Motor Preditivo       (PostgreSQL + Open-Meteo via MCP)
   │
   ▼
Agente Juiz
   │  grounding check, escopo, confidence score (0.0–1.0)
   │  reprovado (<0.7) → mensagem segura fixa, nunca o texto original
   ▼
Guardrail de saída
   │  bloqueia PII/dado sensível residual, trunca resposta longa
   ▼
Resposta ao usuário
   │
   ├─ (async) Métricas → MongoDB (metrics)
   └─ (async) Agente de Governança → auditoria, controle de acesso
```

### Stack

| Camada | Tecnologia |
|---|---|
| API | FastAPI, porta 8000 (ou outra, configurável) |
| Orquestração | LangChain + LangGraph |
| LLM de conversa | Groq (`openai/gpt-oss-120b`, grátis) — ou Ollama local/cloud |
| Visão computacional | Gemini (`gemini-2.5-flash`) |
| Banco operacional | PostgreSQL 15.7 — schema `mottainai` (fonte da verdade) |
| Camada de IA | MongoDB 7 — sessões, memória, RAG, auditoria (22 coleções) |
| Rate limit / notificações | Redis 7 |
| Embeddings (RAG) | `sentence-transformers/all-MiniLM-L6-v2`, local |
| Fonte externa | Open-Meteo (gratuita, CC BY 4.0) |

## Como rodar

Pré-requisitos: Python 3.13 x64, Docker Desktop (ou Postgres/Mongo/Redis nativos), e uma chave da Groq (grátis em [console.groq.com](https://console.groq.com)) ou Ollama instalado para rodar 100% local.

```powershell
# 1. Ambiente Python
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. Configuração
Copy-Item .env.example .env
notepad .env   # preencha GROQ_API_KEY, JWT_SECRET (32+ chars), senhas dos bancos

# 3. Bancos de dados (Postgres, Mongo, Redis — via Docker ou nativos)
#    Aplique o schema oficial em Postgres novo e vazio:
psql -h 127.0.0.1 -d mottainai --single-transaction --set ON_ERROR_STOP=1 -f scripts/sql/mottainai-v6.schema.sql
#    Prepare o MongoDB (índices; --seed-demo para dados de exemplo de RAG):
.\.venv\Scripts\python.exe scripts\setup_mongo.py --seed-demo

# 4. Cache de embeddings (uma vez, precisa de internet na primeira execução)
.\.venv\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer as S; S('all-MiniLM-L6-v2')"
.\.venv\Scripts\python.exe scripts\generate_embeddings.py   # se usou --seed-demo

# 5. Subir a API
.\.venv\Scripts\python.exe -m uvicorn interfaces.api.main:app --host 127.0.0.1 --port 8000
```

Scripts prontos em `scripts/windows/*.ps1` automatizam os passos acima (subir dependências, aplicar schema, gerar token de teste, chat interativo pelo terminal). Rode-os de dentro do PowerShell, na raiz do projeto — nunca por duplo clique no arquivo.

Validar:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/livez     # {"status":"alive"}
Invoke-RestMethod http://127.0.0.1:8000/readyz    # postgres/mongo/redis "ok"
```

Conversar direto pelo terminal:

```powershell
.\scripts\windows\Chat-Interactive.ps1 -Role CLIENTE
```

### Regras de segurança

- Gere segredos **novos** por máquina (`JWT_SECRET`, `MCP_SHARED_TOKEN`, `A2A_SHARED_TOKEN`, senhas de banco). Nunca copie `.env` de outra máquina nem versione credenciais.
- `MCP_EMPRESA_ID` e `A2A_EMPRESA_ID` em `0` mantêm essas integrações bloqueadas por padrão — só habilite com necessidade real, e com token forte configurado.
- Os bancos em Docker devem escutar apenas em `127.0.0.1`, nunca expostos na rede local.

## Endpoints principais

| Rota | Descrição |
|---|---|
| `POST /chat` | Chat multiagente (autenticado) |
| `GET /chat/sessions` / `GET /chat/history/{id}` | Sessões e histórico do usuário autenticado |
| `POST /chat/sessions/{id}/close` | Encerra uma sessão |
| `POST /motor-preditivo/trigger` | Aciona o motor preditivo (role `DONO`) |
| `POST /shelf/analyze` | Análise de prateleira por foto (Visão) |
| `GET /metrics/summary` | Observabilidade: custo, latência, erros, ROI (role `DONO`) |
| `GET /audit/report` | Relatório de conformidade e auditoria (role `DONO`) |
| `POST /mcp` | Transporte MCP (JSON-RPC) |
| `POST /a2a` + `GET /.well-known/agent-card.json` | Protocolo A2A |
| `GET /livez` / `GET /readyz` | Health checks |

## Testes

Suíte não depende de banco, rede ou LLM — roda em qualquer máquina:

```powershell
.\.venv\Scripts\python.exe -m compileall -q app config interfaces tests scripts
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

## Convenções do projeto

Regras obrigatórias de arquitetura e contribuição estão em [AGENTS.md](AGENTS.md).





# Cliente (fala com o Agente Cliente/FAQ — promoções, lojas, fidelidade)
.\scripts\windows\Chat-Interactive.ps1 -Role CLIENTE

# Estoquista (fala com o Agente Funcionário — estoque, alertas, procedimentos)
.\scripts\windows\Chat-Interactive.ps1 -Role ESTOQUISTA

# Gerente (mesmo Agente Funcionário, com permissões de gerente)
.\scripts\windows\Chat-Interactive.ps1 -Role GERENTE

# Dono (fala com o Agente Dono — KPIs, faturamento, analytics, ROI)
.\scripts\windows\Chat-Interactive.ps1 -Role DONO