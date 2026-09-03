# Mottainai IA Layer

<div align="center">

```diff
+███╗   ███╗  ██████╗  ████████╗ ████████╗  █████╗  ██╗ ███╗   ██╗  █████╗  ██╗      █████╗  ██╗ 
+████╗ ████║ ██╔═══██╗ ╚══██╔══╝ ╚══██╔══╝ ██╔══██╗ ██║ ████╗  ██║ ██╔══██╗ ██║     ██╔══██╗ ██║ 
+██╔████╔██║ ██║   ██║    ██║       ██║    ███████║ ██║ ██╔██╗ ██║ ███████║ ██║     ███████║ ██║ 
+██║╚██╔╝██║ ██║   ██║    ██║       ██║    ██╔══██║ ██║ ██║╚██╗██║ ██╔══██║ ██║     ██╔══██║ ██║ 
+██║ ╚═╝ ██║ ╚██████╔╝    ██║       ██║    ██║  ██║ ██║ ██║ ╚████║ ██║  ██║ ██║     ██║  ██║ ██║ 
+╚═╝     ╚═╝  ╚═════╝     ╚═╝       ╚═╝    ╚═╝  ╚═╝ ╚═╝ ╚═╝  ╚═══╝ ╚═╝  ╚═╝ ╚═╝     ╚═╝  ╚═╝ ╚═╝
```

Assistente de IA multiagente para varejo sustentável, construído com LangChain + LangGraph.  
Cada tipo de usuário conversa com um agente especializado, todos atrás do mesmo endpoint `/chat`:  
o Supervisor roteia por perfil e intenção, o Juiz audita a resposta e os guardrails protegem entrada e saída.

![CI](https://github.com/Mottainai-One/Mottainai-IA/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/Python-3.13+-3776AB?style=flat&logo=python&logoColor=white)
![LangChain](https://img.shields.io/badge/LangChain-1.3-1C3C3C?style=flat&logo=langchain&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.2-FF6B35?style=flat)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat&logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-asyncpg-336791?style=flat&logo=postgresql&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB-motor-47A248?style=flat&logo=mongodb&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-redis--py-FF4438?style=flat&logo=redis&logoColor=white)
![Groq](https://img.shields.io/badge/LLM-Groq%20%2F%20Ollama-F55036?style=flat)

</div>

---

## Visão Geral

O Mottainai IA Layer atua como uma API de atendimento inteligente para diferentes perfis de usuário dentro do ambiente da loja. Cada perfil conversa com um agente diferente, todos atrás do mesmo endpoint `/chat`:

| Perfil (`role`) | Agente acionado | Ajuda com |
|---|---|---|
| `CLIENTE` | Cliente / FAQ (RAG) | Promoções, lojas, fidelidade, sustentabilidade |
| `ESTOQUISTA` / `GERENTE` | Funcionário | Estoque, alertas, validade, procedimentos |
| `DONO` | Dono | KPIs, faturamento, perdas, analytics, ROI |

Além desses, dois agentes atuam fora do fluxo comum de conversa:

- **Motor Preditivo** — cruza histórico de vendas com previsão do tempo (Open-Meteo) para prever demanda, detectar risco de perda e sugerir ações. É acionado de duas formas: `POST /motor-preditivo/trigger` (restrito ao `DONO`) ou quando o `DONO` faz uma pergunta de previsão no chat. Não há scheduler embutido — agendamento recorrente fica a cargo da operação (cron externo, se desejado).
- **Agente de Visão** — analisa fotos de prateleira via Gemini (ocupação, produtos detectados, vazio visual).

E dois agentes de controle rodam em toda mensagem de chat, independente do perfil:

- **Agente Juiz** — audita cada resposta antes dela sair (grounding, escopo, confidence score) em modo fail-closed.
- **Agente de Governança** — audita o sistema de forma assíncrona (não bloqueia a resposta).

O fluxo principal da aplicação segue esta sequência:

```text
guardrail de entrada → sessão/contexto → supervisor → agente especializado → juiz → guardrail de saída
```

O Juiz é configurado em modo fail-closed: se a resposta não for adequada, ela não é aprovada. Isso reduz risco de alucinação, vazamento de dados e respostas fora do escopo.

## O que o sistema faz

- **Atendimento multiagente** — roteia a conversa para o agente correto com base no perfil do usuário e na intenção da mensagem.
- **Gestão operacional** — consulta dados de estoque, eventos, alertas e indicadores, e executa ações de negócio (recebimento de mercadoria, descarte de lote) mediante confirmação explícita.
- **Motor preditivo** — gera recomendações com base em histórico de vendas e clima, antecipando compra, risco de perda e ações de reposição.
- **Análise de prateleira** — aceita imagens de prateleira para análise visual com Gemini: ocupação, produtos detectados, vazio visual e sugestões de ação.
- **Base de conhecimento (RAG)** — responde clientes com documentos da própria empresa; gerentes e donos podem subir novos documentos via API.
- **Governança e auditoria** — registra métricas, latências, execução por agente e relatórios de conformidade.

## Arquitetura

```mermaid
flowchart LR
    U(["Usuário"])
    GE["Guardrail Entrada"]
    CTX["Contexto/Sessão"]
    S["Supervisor"]
    C["Cliente"]
    FAQ["FAQ"]
    FUNC["Funcionário"]
    D["Dono"]
    MP["Motor Preditivo"]
    J["Juiz"]
    GS["Guardrail Saída"]
    E(["Fim"])

    U --> GE
    GE -->|"bloqueado"| E
    GE -->|"aprovado"| CTX
    CTX --> S
    S -->|"CLIENTE"| C
    S -->|"CLIENTE + palavra-chave FAQ"| FAQ
    S -->|"ESTOQUISTA / GERENTE"| FUNC
    S -->|"DONO"| D
    S -->|"DONO + palavra-chave previsão"| MP
    C --> J
    FAQ --> J
    FUNC --> J
    D --> J
    MP --> J
    J --> GS
    GS --> E
```

Fora do fluxo de chat:

- **Motor Preditivo** — PostgreSQL + Open-Meteo (via MCP) → previsão de demanda e risco de perda.
- **Agente de Visão** — análise de fotos de prateleira via Gemini (`POST /shelf/analyze`).
- **Agente de Governança** — auditoria contínua e assíncrona, não bloqueia a resposta.

Diagrama completo, com todos os agentes lado a lado, em [ARQUITETURA.md](ARQUITETURA.md).

## Stack Tecnológico

| Camada | Tecnologia | Versão | Papel |
|---|---|---|---|
| Linguagem | [Python](https://www.python.org/) | 3.13+ | Base do projeto |
| API | [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) | 0.115 / 0.32 | API HTTP assíncrona |
| Validação | [Pydantic](https://docs.pydantic.dev/) + [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) | 2.10 | Schemas, contratos e configuração tipada |
| Orquestração de IA | [LangChain](https://python.langchain.com/) + [LangGraph](https://langchain-ai.github.io/langgraph/) | 1.3 / 1.2 | Grafo multiagente, supervisor e nós |
| LLM principal | [Groq](https://console.groq.com/) (`llama-3.3-70b-versatile`) | groq 0.37 | Inferência de texto (plano gratuito) |
| LLM alternativo | [Ollama](https://ollama.com/) Cloud (`gpt-oss:20b`) ou local (`qwen2.5:7b-instruct`) | — | Fallback cloud ou modo 100% offline, sem enviar dados a terceiros |
| Visão computacional | [Gemini](https://ai.google.dev/) (`gemini-2.5-flash`) + [Pillow](https://python-pillow.org/) | 11.1 | Análise visual de prateleira |
| Embeddings | [Sentence Transformers](https://sbert.net/) (`all-MiniLM-L6-v2`) + [PyTorch](https://pytorch.org/) | 3.4 / 2.6 | Embeddings locais para RAG, sem custo de API |
| Dados operacionais | [PostgreSQL](https://www.postgresql.org/) via [asyncpg](https://github.com/MagicStack/asyncpg) + [SQLAlchemy async](https://www.sqlalchemy.org/) | 0.30 / 2.0 | Fonte da verdade do negócio (schema v6) |
| Dados de IA | [MongoDB](https://www.mongodb.com/) via [Motor](https://motor.readthedocs.io/) | 3.7 | Histórico, memória de longo prazo, RAG e auditoria |
| Cache e rate limit | [Redis](https://redis.io/) via [redis-py async](https://github.com/redis/redis-py) | 5.2 | Rate limit, cache de RAG e notificações |
| Clima | [Open-Meteo](https://open-meteo.com/) | — | Previsão do tempo para o Motor Preditivo (sem API key) |
| Autenticação | [PyJWT](https://pyjwt.readthedocs.io/) (HS256) | 2.10 | Tokens com `empresa_id`, `usuario_id` e `role` |
| Interoperabilidade | [MCP](https://modelcontextprotocol.io/) + [A2A](https://a2a-protocol.org/) | — | Exposição do Mottainai como serviço para outros agentes |
| Resiliência | [tenacity](https://tenacity.readthedocs.io/) + [httpx](https://www.python-httpx.org/) | 9.0 / 0.28 | Retry com backoff exponencial e HTTP assíncrono |
| Observabilidade | [structlog](https://www.structlog.org/) | 24.4 | Logs estruturados |
| Infra local | [Docker Compose](https://docs.docker.com/compose/) | — | Provisionamento de PostgreSQL e MongoDB |
| Qualidade | [Ruff](https://docs.astral.sh/ruff/) + [Coverage](https://coverage.readthedocs.io/) | 0.16 / 7.6 | Lint e cobertura mínima de 60% na CI |

## Estrutura do Projeto

```text
mottainai-ia/
├── app/
│   ├── agents/                # Supervisor, agentes e nós LangGraph
│   ├── analytics/             # KPIs, custos e ROI
│   ├── cache/                 # Cache de RAG no Redis (fail-open)
│   ├── database/              # Clientes PostgreSQL, MongoDB e Redis
│   ├── guardrails/            # Validação de entrada e saída
│   ├── integrations/          # Integrações MCP e A2A
│   ├── memory/                # Histórico, memória e sessão
│   ├── notifications/         # Notificações via Redis
│   ├── observability/         # Métricas, execução e auditoria
│   ├── rag/                   # Recuperação de conhecimento e fontes externas
│   ├── schemas/               # Schemas Pydantic compartilhados
│   ├── security/              # Autenticação JWT e controle de acesso
│   ├── tools/                 # Ferramentas de domínio (PostgreSQL)
│   ├── config.py              # Compatibilidade de importação
│   └── main.py                # Entrada compatível
├── config/
│   └── settings.py            # Configuração central por ambiente
├── interfaces/
│   └── api/
│       └── main.py            # API principal FastAPI
├── scripts/
│   ├── sql/                   # Schema v6, dataload e verificação
│   ├── mongo/                 # Schema das collections MongoDB
│   ├── windows/               # Bootstrap para Windows
│   ├── generate_dev_token.py  # Token JWT de desenvolvimento
│   ├── generate_embeddings.py # Embeddings dos chunks RAG
│   ├── preflight_postgres.py  # Validação read-only do schema
│   ├── setup_mongo.py         # Cria/atualiza collections e índices
│   └── validate_ai.py         # Validação de prompts e respostas (usado na CI)
├── skills/                    # Skills de apoio ao desenvolvimento
├── tests/                     # Autorização, isolamento de tenant e boundary checks
├── .github/workflows/ci.yml   # Pipeline de CI
├── AGENTS.md                  # Convenções do projeto
├── ARQUITETURA.md             # Arquitetura detalhada (todos os agentes)
├── Dockerfile
├── docker-compose.yml
├── docker-compose.windows.yml
├── pyproject.toml
├── requirements.txt
├── start.sh
└── .env.example               # Modelo de variáveis de ambiente
```

## Pré-requisitos

- Python 3.13+
- PostgreSQL 15+
- MongoDB 7+
- Redis 7+
- Docker e Docker Compose (opcional, recomendado para os bancos locais)
- Chaves de API: [Groq](https://console.groq.com) e [Gemini](https://aistudio.google.com/app/apikey) (ambas com plano gratuito) — ou nenhuma, usando `LLM_PROVIDER=ollama_local`

## Configuração

Copie `.env.example` para `.env` e ajuste os valores locais. Principais variáveis:

```ini
# LLM principal — groq | ollama | ollama_local
LLM_PROVIDER=groq
GROQ_API_KEY=
GROQ_MODEL=llama-3.3-70b-versatile

# Visão (análise de prateleira)
GEMINI_API_KEY=
GEMINI_VISION_MODEL=gemini-2.5-flash

# Bancos
POSTGRES_DSN=postgresql+asyncpg://mottainai:mottainai@localhost:5432/mottainai
MONGO_URI=mongodb://localhost:27017
MONGO_DB=mottainai
REDIS_URL=redis://localhost:6379/0
REDIS_PASSWORD=defina-uma-senha-forte    # obrigatório para subir a API via Docker Compose

# Autenticação e integrações
JWT_SECRET=gere-um-segredo-forte-de-32-caracteres-por-maquina
MCP_SHARED_TOKEN=
A2A_SHARED_TOKEN=
MCP_EMPRESA_ID=0                          # 0 = integração bloqueada
A2A_EMPRESA_ID=0
PUBLIC_BASE_URL=http://localhost:8000

ENV=development
LOG_LEVEL=INFO
```

> `LLM_PROVIDER=ollama_local` roda 100% offline (loopback, sem enviar conversas a terceiros). Baixe o modelo antes: `ollama pull qwen2.5:7b-instruct`.

> O projeto aceita `DATABASE_URL` e `MONGO_URL` como aliases de compatibilidade. A lista completa de variáveis (Ollama, rate limit, timeouts de Redis, custos de token) está no [.env.example](.env.example).

## Instalação

No Windows, em PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

No Linux/macOS:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> A primeira instalação baixa o PyTorch e o modelo de embeddings — reserve espaço em disco e um café.

## Subindo o ambiente

### 1. Bancos de dados

```bash
docker compose up -d postgres mongo
```

> O `docker-compose.yml` não inclui um service de Redis: o service `api` espera um Redis na rede externa `mottainai-redis-network` (provisionado à parte) e exige `REDIS_PASSWORD` no `.env`. Rodando a API fora do Docker, basta um Redis local qualquer apontado por `REDIS_URL`.

### 2. Schema e dados

```bash
# PostgreSQL — schema operacional v6 + dados de exemplo
psql -h localhost -U mottainai -d mottainai -f scripts/sql/mottainai-v6.schema.sql
psql -h localhost -U mottainai -d mottainai -f scripts/sql/mottainai-v6.dataload.sql

# valida o schema sem alterar nada (read-only)
bash scripts/setup_postgres.sh

# MongoDB — collections, índices e (opcional) dados de demonstração
python scripts/setup_mongo.py --seed-demo

# Embeddings dos documentos RAG ainda não indexados
python scripts/generate_embeddings.py
```

> O schema oficial é versionado no repositório do banco operacional; os arquivos em `scripts/sql/` são a cópia de referência para desenvolvimento local.

### 3. API

```bash
python -m uvicorn interfaces.api.main:app --host 0.0.0.0 --port 8000 --reload
```

Documentação interativa em `http://localhost:8000/docs` e `http://localhost:8000/redoc`.

### 4. Primeira conversa

Todas as rotas de negócio exigem JWT. Gere um token de desenvolvimento e converse:

```bash
python scripts/generate_dev_token.py --usuario-id 1 --empresa-id 1 --role DONO

curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Quais produtos vencem nos próximos 3 dias?",
    "session_id": "550e8400-e29b-41d4-a716-446655440000"
  }'
```

## Endpoints

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| `POST` | `/chat` | JWT (qualquer perfil) | Mensagem do usuário para o agente de IA |
| `GET` | `/chat/history/{session_id}` | JWT | Histórico da sessão (apenas do próprio usuário) |
| `GET` | `/chat/sessions` | JWT | Listagem de sessões do usuário |
| `POST` | `/chat/sessions/{session_id}/close` | JWT | Encerramento da sessão |
| `POST` | `/auth/logout` | JWT | Revogação do token atual |
| `POST` | `/motor-preditivo/trigger` | `DONO` | Execução manual do motor preditivo |
| `POST` | `/funcionario/receber-mercadoria` | `ESTOQUISTA`+ | Registro de entrada de mercadoria |
| `POST` | `/funcionario/descartar-lote` | `ESTOQUISTA`+ | Descarte de lote (com confirmação) |
| `POST` | `/shelf/analyze` | `ESTOQUISTA`+ | Análise visual de prateleira (upload de imagem) |
| `POST` | `/rag/documents` | `GERENTE`+ | Upload de documentos para a base RAG |
| `GET` | `/metrics/summary` | `DONO` | Métricas e observabilidade |
| `GET` | `/audit/report` | `DONO` | Relatório de conformidade e auditoria |
| `POST` | `/mcp` | token compartilhado | Transporte MCP, JSON-RPC: `initialize`, `tools/list`, `tools/call` |
| `POST` | `/a2a` | token compartilhado | Protocolo A2A, para outros agentes consumirem o Mottainai |
| `GET` | `/.well-known/agent-card.json` | público | Descoberta A2A (capacidades do agente) |
| `GET` | `/health` · `/livez` · `/readyz` | público | Health checks e readiness |

> `ESTOQUISTA`+ = `ESTOQUISTA`, `GERENTE` ou `DONO`. `GERENTE`+ = `GERENTE` ou `DONO`.

## Fluxo de execução da IA

O sistema segue uma abordagem defensiva e orientada a controle:

1. Recebe a mensagem do usuário.
2. Valida e sanitiza a entrada.
3. Carrega contexto e histórico da sessão.
4. Roteia para o agente mais adequado.
5. Executa a lógica de domínio.
6. Valida a resposta no Juiz.
7. Faz revisão final do guardrail de saída.
8. Responde ao cliente com rastreabilidade e métrica de execução.

## Segurança e limite de escopo

- Sessões são vinculadas a `empresa_id` e `usuario_id`.
- Acesso cruzado entre empresas ou usuários é rejeitado.
- Papel de usuário é validado antes de execução.
- Cliente e FAQ não têm acesso a dados operacionais internos.
- Ações de negócio exigem confirmação explícita do usuário.
- MCP e A2A ficam bloqueados por padrão: cada token compartilhado é restrito a uma única empresa (`MCP_EMPRESA_ID` / `A2A_EMPRESA_ID`).
- Respostas devem manter fontes e contexto de decisão quando aplicável.
- O projeto adota fail-closed para validação e reforço de segurança.

## Testes e qualidade

Mesmos passos executados pela CI:

```bash
ruff check .
python -m compileall -q app config interfaces tests scripts
coverage run -m unittest discover -s tests -p "test_*.py" -v
coverage report --fail-under=60
```

A CI ([ci.yml](.github/workflows/ci.yml)) ainda valida os prompts e o formato das respostas dos agentes com `python scripts/validate_ai.py prompts` e `python scripts/validate_ai.py responses`.

Os testes cobrem regras de autorização, isolamento de sessão e de tenant, controle de acesso e comportamento de fronteira sem depender de LLM ou de serviços externos.

## Observabilidade

`GET /metrics/summary` retorna:

- latência média, p50 e p95, por execução e por nó do grafo (guardrails, supervisor, cada agente, Juiz)
- custo estimado (tokens de entrada/saída) e projeção para 100/1.000 usuários semanais
- taxa de erro e score médio do Juiz, no geral e por agente
- ROI e custo por resolução
- relatório de conformidade/auditoria (`GET /audit/report`)

## Robustez

- **Retry automático de LLM** — toda chamada a Groq/Ollama tenta novamente com backoff exponencial + jitter em falha transitória (timeout, erro de rede, 5xx), sem alterar a resposta em caso de sucesso.
- **Cache de RAG no Redis** — a mesma pergunta na mesma empresa não recalcula embeddings/similaridade; se o Redis cair, o RAG segue funcionando normalmente, só sem o ganho de velocidade (fail-open).

## Observações importantes

- O projeto foi desenhado para funcionar em ambiente local com Docker e serviços reais.
- O módulo de RAG e as integrações externas dependem das conexões configuradas em `.env`.
- O `start.sh` e o `docker-compose.windows.yml` atendem ambientes específicos; em geral, inicie a API diretamente com `uvicorn`.
- O código mantém compatibilidade de imports legados (`app/config.py`, `app/main.py`) para estabilidade de integração.

## Licença

Projeto de uso interno/privado, não destinado a publicação como software open source.
