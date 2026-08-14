# Mottainai IA Layer

Camada de IA multiagente para gestão preditiva de estoque e suporte operacional em varejo. O projeto combina FastAPI, LangGraph, LLMs, visão computacional, PostgreSQL, MongoDB e Redis para entregar respostas contextualizadas, controle de acesso e rastreabilidade de decisões.


<div align="center">



```
███╗   ███╗  ██████╗  ████████╗ ████████╗  █████╗  ██╗ ███╗   ██╗  █████╗  ██╗      █████╗  ██╗ 
████╗ ████║ ██╔═══██╗ ╚══██╔══╝ ╚══██╔══╝ ██╔══██╗ ██║ ████╗  ██║ ██╔══██╗ ██║     ██╔══██╗ ██║ 
██╔████╔██║ ██║   ██║    ██║       ██║    ███████║ ██║ ██╔██╗ ██║ ███████║ ██║     ███████║ ██║ 
██║╚██╔╝██║ ██║   ██║    ██║       ██║    ██╔══██║ ██║ ██║╚██╗██║ ██╔══██║ ██║     ██╔══██║ ██║ 
██║ ╚═╝ ██║ ╚██████╔╝    ██║       ██║    ██║  ██║ ██║ ██║ ╚████║ ██║  ██║ ██║     ██║  ██║ ██║ 
╚═╝     ╚═╝  ╚═════╝     ╚═╝       ╚═╝    ╚═╝  ╚═╝ ╚═╝ ╚═╝  ╚═══╝ ╚═╝  ╚═╝ ╚═╝     ╚═╝  ╚═╝ ╚═╝
```


Assistente pessoal de **finanças e agenda** construído com LangChain + LangGraph.  
O sistema usa uma arquitetura multi-agente onde cada agente tem uma responsabilidade bem definida:  
classificar a intenção, processar o domínio correto e formatar a resposta final para o usuário.

![Python](https://img.shields.io/badge/Python-3.13+-3776AB?style=flat&logo=python&logoColor=white)
![LangChain](https://img.shields.io/badge/LangChain-1.2-1C3C3C?style=flat&logo=langchain&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.1-FF6B35?style=flat)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-psycopg2-336791?style=flat&logo=postgresql&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB-pymongo-47A248?style=flat&logo=mongodb&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-pyredis-FF4438?style=flat&logo=redis&logoColor=white)
![Qdrant](https://img.shields.io/badge/Qdrant-qdrant--client-DC244C?style=flat&logo=qdrant&logoColor=white)

</div>

---

## Visão Geral
O Mottainai IA Layer atua como uma API de atendimento inteligente para diferentes perfis de usuário dentro do ambiente da loja:

- `ESTOQUISTA`: acompanhamento de estoque, vencimentos e alertas operacionais.
- `GERENTE`: consultas operacionais mais amplas, sugestões e acompanhamento de KPI.
- `DONO`: visão estratégica, faturamento, perdas e diagnósticos executivos.
- `CLIENTE`: suporte de vendas, promoções, loja e FAQ.

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
Requisição do usuário
        │
        ▼
┌──────────────────────────────────────────────┐
│ Guardrail de entrada                         │
│ - sanitização e validação                    │
│ - rate limit                                 │
│ - bloqueio de prompt injection              │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│ Contexto e sessão                            │
│ - histórico em MongoDB                       │
│ - memória e perfil do usuário                │
│ - controle de empresa/usuário                │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│ Supervisor (LangGraph)                       │
│ - roteia para agente certo                  │
│ - considera role + intenção                 │
└───────┬───────────────┬──────────────────────┘
        │               │
        ▼               ▼
┌───────────────┐ ┌───────────────┐
│ Agente        │ │ Agente        │
│ Funcionário   │ │ Dono / Cliente│
│ - loja        │ │ - KPI         │
│ - estoque     │ │ - faturamento │
│ - alertas     │ │ - estratégias │
└───────┬───────┘ └───────┬───────┘
        │                   │
        └───────────┬───────┘
                    ▼
        ┌────────────────────┐
        │ Juiz / Validação   │
        │ - grounding        │
        │ - escopo           │
        │ - confiança        │
        └──────────┬─────────┘
                   ▼
        ┌────────────────────┐
        │ Guardrail de saída │
        │ - revisão final    │
        │ - segurança        │
        └────────────────────┘
```

## Stack Tecnológico

- Python 3.13+
- FastAPI para API HTTP
- LangChain + LangGraph para orquestração multiagente
- Groq / Llama 3.3 para inferência principal
- Gemini para análise visual da prateleira
- PostgreSQL para dados operacionais
- MongoDB para histórico, memória e sessões
- Redis para rate limit e apoio em tempo real
- Sentence Transformers para embeddings locais
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
GROQ_API_KEY=
GROQ_MODEL=llama-3.3-70b-versatile
GEMINI_API_KEY=
GEMINI_VISION_MODEL=gemini-1.5-flash
POSTGRES_DSN=postgresql+asyncpg://mottainai:mottainai@localhost:5432/mottainai
MONGO_URI=mongodb://localhost:27017
MONGO_DB=mottainai
REDIS_URL=redis://localhost:6379/0
MCP_SHARED_TOKEN=
A2A_SHARED_TOKEN=
PUBLIC_BASE_URL=http://localhost:8000
ENV=development
LOG_LEVEL=INFO
```

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
- `POST /mcp` — transport HTTP para integrações MCP
- `POST /a2a` — integração com agentes A2A
- `GET /.well-known/agent-card.json` — descoberta de agentes
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

O projeto registra:

- latência por execução
- latência por nó do grafo
- status de cada etapa
- métricas de custo e performance
- relatório de conformidade/auditoria
- execução de agentes e resultados do Juiz

Esses dados podem ser consultados via endpoints de métricas e auditoria.

## Observações importantes

- O projeto foi desenhado para funcionar em ambiente local com Docker e serviços reais.
- O módulo de RAG e integrações externas dependem de fábricas e conexões configuradas corretamente em `.env`.
- O `start.sh` é específico para alguns ambientes, mas a API principal deve ser iniciada diretamente com `uvicorn` em geral.
- O código já inclui compatibilidade de imports legado para manter estabilidade de integração.

## Licença

Este projeto é de uso interno/privado e não foi destinado a publicação pública como software open source.

## Contato

O projeto é estruturado para atuar como camada de inteligência de negócio em ambiente de varejo e gestão operacional, com foco em produtividade, previsibilidade e governança de decisões de IA.
