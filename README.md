# Mottainai IA Layer

Camada de IA multiagente para gestão preditiva de estoque e suporte operacional em varejo. O projeto combina FastAPI, LangGraph, LLMs, visão computacional, PostgreSQL, MongoDB e Redis para entregar respostas contextualizadas, controle de acesso e rastreabilidade de decisões.


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

![Python](https://img.shields.io/badge/Python-3.13+-3776AB?style=flat&logo=python&logoColor=white)
![LangChain](https://img.shields.io/badge/LangChain-1.3-1C3C3C?style=flat&logo=langchain&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.2-FF6B35?style=flat)
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

Além desses, dois agentes atuam de forma independente do fluxo de chat:

- **Motor Preditivo** — autônomo (roda por trigger/schedule, não por mensagem do usuário): cruza histórico de vendas com previsão do tempo (Open-Meteo) para prever demanda, detectar risco de perda e sugerir ações.
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

### Atendimento multiagente

A infraestrutura roteia a conversa para o agente correto com base no perfil do usuário e na intenção da mensagem.

### Gestão operacional

O sistema consulta e manipula dados de estoque, eventos, alertas e indicadores relevantes para a operação da loja.

### Motor preditivo

O módulo de predição gera recomendações com base em históricos e contexto do negócio, ajudando na antecipação de compra, risco de perda e ações de reposição.

### Análise de prateleira

A API também aceita imagens de prateleira para análise visual com Gemini, identificando ocupação, produtos detectados, vazio visual e sugestões de ação.

### Governança e auditoria

A aplicação registra métricas, latências, execução por agente e relatórios de conformidade para apoiar observabilidade e controle operacional.

## Arquitetura

```text
Requisição do usuário (POST /chat)
        │
        ▼
┌──────────────────────────────────────────────┐
│ Guardrail de entrada                         │
│ - sanitização e validação                    │
│ - rate limit (Redis)                         │
│ - bloqueio de prompt injection               │
└──────────────────────┬───────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────┐
│ Contexto e sessão (MongoDB)                  │
│ - histórico da sessão                        │
│ - memória de longo prazo do usuário          │
│ - controle de empresa/usuário                │
└──────────────────────┬───────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────┐
│ Supervisor (LangGraph)                       │
│ - roteia para o agente certo                 │
│ - considera role + intenção                  │
└───────┬───────────┬───────────┬──────────────┘
        │           │           │
        ▼           ▼           ▼
┌────────────┐ ┌────────────┐ ┌────────────┐
│  Cliente/  │ │Funcionário │ │    Dono    │
│    FAQ     │ │ - estoque  │ │ - KPIs     │
│   (RAG)    │ │ - alertas  │ │ - ROI      │
└─────┬──────┘ └─────┬──────┘ └─────┬──────┘
      │              │              │
      └──────────────┴──────┬───────┘
                             ▼
                  ┌────────────────────┐
                  │ Juiz (fail-closed) │
                  │ - grounding        │
                  │ - escopo           │
                  │ - confidence score │
                  └──────────┬─────────┘
                             ▼
                  ┌────────────────────┐
                  │ Guardrail de saída │
                  │ - revisão final    │
                  │ - segurança        │
                  └──────────┬─────────┘
                             ▼
                     Resposta ao usuário
```

Fora do fluxo de chat (assíncronos, acionados por trigger/schedule ou por upload):

- **Motor Preditivo** — PostgreSQL + Open-Meteo (MCP) → previsão de demanda e risco de perda.
- **Agente de Visão** — análise de fotos de prateleira via Gemini.
- **Agente de Governança** — auditoria contínua, não bloqueia a resposta.

Diagrama completo, com todos os agentes lado a lado, em [ARQUITETURA.md](ARQUITETURA.md).

## Stack Tecnológico

- Python 3.13+
- FastAPI para API HTTP
- LangChain + LangGraph para orquestração multiagente
- Groq (`openai/gpt-oss-120b`, gratuito) para inferência principal — Ollama (local ou cloud) como alternativa
- Gemini (`gemini-2.5-flash`) para análise visual da prateleira
- PostgreSQL (via `asyncpg`) para dados operacionais — fonte da verdade do negócio
- MongoDB para histórico, memória de longo prazo, RAG e auditoria
- Redis para rate limit, cache de RAG e notificações
- Sentence Transformers (`all-MiniLM-L6-v2`) para embeddings locais, sem custo de API
- Open-Meteo como fonte externa de dados (previsão do tempo, usada pelo Motor Preditivo via MCP)
- JWT (HS256) para autenticação e controle de sessão
- MCP e A2A para integração e descoberta entre agentes
- Pydantic para validação de schemas
- Docker Compose para provisionamento local

## Estrutura do Projeto

```text
mottainai-ia/
├── app/
│   ├── agents/               # Supervisores, agentes e nós LangGraph
│   ├── database/             # Clientes PostgreSQL, MongoDB e Redis
│   ├── guardrails/           # Validação de entrada e saída
│   ├── integrations/         # Integrações MCP e A2A
│   ├── memory/               # Histórico, memória e sessão
│   ├── observability/        # Métricas, execução e auditoria
│   ├── rag/                  # Recuperação de conhecimento e fontes externas
│   ├── tools/                # Ferramentas de domínio
│   ├── __init__.py
│   ├── config.py             # Compatibilidade de importação
│   └── main.py               # Entrada compatível
├── config/
│   └── settings.py           # Configuração central por ambiente
├── interfaces/
│   └── api/
│       └── main.py           # API principal FastAPI
├── tests/
│   └── ...                   # Testes de boundary checks e comportamento
├── scripts/
│   └── ...                   # Scripts de manutenção e integração
├── AGENTS.md                 # Convenções do projeto
├── ARQUITETURA.md            # Arquitetura de alto nível do sistema
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── requirements.txt
├── start.sh
├── README.md
└── .env.example             # Modelo de variáveis de ambiente
```

## Pré-requisitos

Antes de rodar a aplicação, você precisa ter instalado:

- Python 3.13+
- PostgreSQL 15+
- MongoDB 7+
- Redis 7+
- Docker e Docker Compose (opcional, mas recomendado para bancos locais)
- Chaves de API para os provedores de IA utilizados

## Configuração

Crie um arquivo `.env` a partir do modelo do projeto e ajuste os valores locais:

```env
LLM_PROVIDER=groq
GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-120b
GEMINI_API_KEY=
GEMINI_VISION_MODEL=gemini-2.5-flash
POSTGRES_DSN=postgresql+asyncpg://mottainai:mottainai@localhost:5432/mottainai
MONGO_URI=mongodb://localhost:27017
MONGO_DB=mottainai
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=gere-um-segredo-forte-de-32-caracteres-por-maquina
MCP_SHARED_TOKEN=
A2A_SHARED_TOKEN=
PUBLIC_BASE_URL=http://localhost:8000
ENV=development
LOG_LEVEL=INFO
```

> `LLM_PROVIDER` também aceita `ollama` (Ollama Cloud) ou `ollama_local` (100% offline, sem enviar dados a terceiros).

> O projeto também aceita `DATABASE_URL` e `MONGO_URL` como aliases de compatibilidade.

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

## Execução

### Iniciar a API

```bash
python -m uvicorn interfaces.api.main:app --host 0.0.0.0 --port 8000 --reload
```

### Iniciar bancos com Docker Compose

```bash
docker compose up -d postgres mongo redis
```

### Endpoints principais

Acesse a documentação interativa em:

- `http://localhost:8000/docs`
- `http://localhost:8000/redoc`

Principais rotas:

- `POST /chat` — mensagem do usuário para o agente de IA
- `GET /chat/history/{session_id}` — histórico da sessão
- `GET /chat/sessions` — listagem de sessões do usuário
- `POST /chat/sessions/{session_id}/close` — encerramento da sessão
- `POST /motor-preditivo/trigger` — execução manual do motor preditivo
- `POST /shelf/analyze` — análise visual de prateleira
- `GET /metrics/summary` — métricas e observabilidade
- `GET /audit/report` — relatório de conformidade e auditoria
- `POST /mcp` — transporte MCP (Model Context Protocol), JSON-RPC: `initialize`, `tools/list`, `tools/call`
- `POST /a2a` — protocolo A2A (Agent-to-Agent), para outros agentes consumirem o Mottainai como serviço
- `GET /.well-known/agent-card.json` — descoberta A2A (capacidades do agente)
- `GET /health`, `GET /livez`, `GET /readyz` — health checks e readiness

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
- Respostas devem manter fontes e contexto de decisão quando aplicável.
- O projeto adota fail-closed para validação e reforço de segurança.

## Testes

Executar validação básica de compilação e testes:

```powershell
python -m compileall -q app config interfaces tests scripts
python -m unittest discover -s tests -p "test_*.py" -v
```

Os testes cobrem regras de autorização, isolamento de sessão, controle de acesso e comportamento de fronteira sem depender de LLM ou de serviços externos.

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
- O módulo de RAG e integrações externas dependem de fábricas e conexões configuradas corretamente em `.env`.
- O `start.sh` é específico para alguns ambientes, mas a API principal deve ser iniciada diretamente com `uvicorn` em geral.
- O código já inclui compatibilidade de imports legado para manter estabilidade de integração.

## Licença

Este projeto é de uso interno/privado e não foi destinado a publicação pública como software open source.

## Contato

O projeto é estruturado para atuar como camada de inteligência de negócio em ambiente de varejo e gestão operacional, com foco em produtividade, previsibilidade e governança de decisões de IA.
