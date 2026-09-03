---
name: Mottainai Contratos Mongo
description: Os validadores $jsonSchema das collections do MongoDB e a classe de bug que eles causam — inserts que passam nos testes mockados e retornam 500 contra o banco real. Use ao escrever qualquer documento em messages, rag_documents, agent_executions ou ai_results.
metadata:
  category: reference
  version: "1.0.0"
---

# Contratos do MongoDB

Use esta skill antes de escrever qualquer documento novo no Mongo, ou ao
depurar um `500` que só acontece com o banco de verdade.

## O problema que esta skill existe para evitar

As collections do Mottainai têm **validadores `$jsonSchema`**. Um documento
fora do schema é rejeitado pelo Mongo, e a exceção sobe como `500`.

Os testes do projeto mockam o Mongo. Um mock aceita qualquer dicionário. Então
**um insert inválido passa em 100% dos testes e falha em 100% das requisições
reais**. Já aconteceu quatro vezes neste repositório:

| Bug | O que foi escrito | O que o schema exige |
|---|---|---|
| `sources[].type` inválido | `"calc"`, `"vision"` | enum `rag, sql, api, manual, url, other` |
| `agent_executions.error` | uma string | `object` ou `null` |
| `rag_documents` sem `category`/`version` | 5 campos | `empresaId, title, category, version, createdAt` |
| slug único global | — | (índice, não validador — ver adiante) |

Os três primeiros derrubaram funcionalidades inteiras em produção enquanto a
suíte de testes seguia verde.

## Regra prática

> Escreveu um documento novo no Mongo? **Rode contra o banco real antes de
> abrir PR.** Compilação, lint e teste unitário não validam `$jsonSchema`.

## Campos obrigatórios por collection

Extraídos do banco real (`db.list_collections()`), não do `setup_mongo.py`:

| Collection | `required` |
|---|---|
| `conversations` | `sessionId, empresaId, usuarioId, agent, status, startedAt` |
| `messages` | `conversationId, role, content, createdAt` |
| `memories` | `usuarioId, empresaId, updatedAt` |
| `rag_documents` | `empresaId, title, category, version, createdAt` |
| `rag_chunks` | `documentId, chunk, text` |
| `agent_executions` | `agent, status, startedAt` |
| `metrics` | `agent, createdAt` |
| `ai_results` | `conversationId, agent, type, result, createdAt` |
| `prompt_evaluations` | `promptVersion, agent, score, createdAt` |
| `conversation_events` | `conversationId, type, createdAt` |
| `agent_policies` | `agent, scope, active, createdAt` |

## Os dois contratos que mais quebram

### `messages.sources[].type` — enum fechado

```
enum: ["rag", "sql", "api", "manual", "url", "other"]
```

Cada item de `sources` exige `type` **e** `ref`. Qualquer outro valor de `type`
faz o `save_message()` do `guardrail_saida` estourar — ou seja, a resposta já
foi aprovada pelo Juiz e some no 500.

A constante canônica está em `app/database/mongo_schema.py::SOURCE_TYPES`.
**Importe de lá, não redigite a lista.** Um agente que produz um tipo de fonte
novo usa `"other"` com o detalhe no `ref`:

```python
{"type": "other", "ref": "app.agents.visao (ai_results)", "score": None}
```

O CI valida isso estaticamente: `python scripts/validate_ai.py sources` varre
`app/agents/*.py` procurando dicts com as chaves `type` e `ref` e compara o
literal contra o enum.

### `agent_executions.error` — objeto ou null, nunca string

```python
# errado: derruba o registro do erro exatamente quando ele importa
await record_agent_execution(..., error="TimeoutError")

# certo
await record_agent_execution(..., error={"message": "TimeoutError"})
```

`app/observability/executions.py` já envolve strings automaticamente, mas
qualquer novo caminho de escrita precisa respeitar o tipo.

## Armadilha: o banco real diverge do `setup_mongo.py`

**O `scripts/setup_mongo.py` não foi o que provisionou este banco.**

- O banco real tem validadores `$jsonSchema`; o script **não cria validador nenhum**.
- Os índices do banco real se chamam `ix_*`/`ux_*`; o script cria com os nomes padrão do driver.
- O banco tem collections que o script não menciona (`prompts`, `intent_catalog`, `agent_contexts`, `agent_fallbacks`, `cache_queries`, `embeddings_cache`, `response_explanations`).

Consequência prática: **um ambiente novo criado pelo `setup_mongo.py` não
reproduz a produção**. Sem os validadores, os quatro bugs da tabela acima
passariam despercebidos ali. Se você for depurar "funciona na minha máquina",
comece conferindo se o seu Mongo tem validador:

```python
cur = await db.list_collections(filter={"name": "messages"})
async for c in cur:
    print(c.get("options", {}).get("validator"))
```

## Índices que importam

`rag_documents` tem unicidade em **`(empresaId, slug)`**, não em `slug` sozinho
— um slug óbvio como `politica-troca-devolucao` precisa funcionar para todos os
tenants. `ingest_document()` depende dessa constraint de banco em vez de fazer
check-then-insert, que teria corrida em upload concorrente; o
`DuplicateSlugError` nasce do `DuplicateKeyError`.
