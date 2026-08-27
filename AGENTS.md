# Mottainai IA Layer — Convenções do Projeto

## Estrutura

- `interfaces/api/`: camada HTTP/FastAPI. Não concentra regras de agentes ou acesso direto a bancos além do necessário para health checks.
- `app/agents/`: nós LangGraph e agentes de domínio.
- `app/tools/`: integrações de domínio com PostgreSQL, Redis, visão e MCP.
- `app/memory/`: sessão, histórico e memória longa no MongoDB.
- `app/rag/`: recuperação de conhecimento e fontes externas.
- `app/guardrails/`: controles determinísticos de entrada e saída.
- `app/observability/`: métricas, erros, custo e auditoria técnica.
- `config/settings.py`: única fonte de variáveis de ambiente. `app/config.py` é compatibilidade legada.
- `tests/`: espelha responsabilidades da aplicação; testes não dependem de LLM, rede ou banco real.

## Regras obrigatórias

- Preserve o fluxo: guardrail de entrada → contexto → supervisor → agente → Juiz → guardrail de saída.
- O Juiz é fail-closed; falha na avaliação nunca libera resposta.
- Cliente/FAQ nunca acessam dados operacionais internos.
- Toda sessão pertence a `empresa_id + usuario_id`; não aceite acesso cruzado.
- Não adicione ações automáticas: qualquer operação de negócio exige confirmação explícita do usuário.
- Mantenha fontes RAG no retorno e no histórico das mensagens.
- Use `.env` apenas localmente; não versionar ou expor credenciais.

## Validação mínima

```bash
.venv/bin/python -m compileall -q app config interfaces tests scripts
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

Para teste end-to-end, subir a API e validar `/health`, `/chat`, sessões e `/metrics/summary` com dependências reais disponíveis.
