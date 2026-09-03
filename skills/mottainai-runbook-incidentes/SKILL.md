---
name: Mottainai Runbook de Incidentes
description: Sintoma → causa → o que checar, para os incidentes que já aconteceram neste repositório. Use quando algo parar de funcionar e você precisar da primeira hipótese certa em vez da terceira.
metadata:
  category: operations
  version: "1.0.0"
---

# Runbook de incidentes

Cada entrada aqui aconteceu de verdade neste repositório. Use quando algo
quebrar, antes de investigar do zero.

## Antes de tudo

```bash
curl -s http://127.0.0.1:8000/health
```

`{"status":"healthy","checks":{"mongodb":"ok","redis":"ok","postgres":"ok"}}`

**O `/health` não cobre os provedores de LLM, visão nem clima.** Groq, Gemini e
Open-Meteo só falham na hora da requisição. Health verde não significa chat
funcionando.

---

## O chat responde 503 para tudo

**Causa mais provável:** orçamento de tokens, não provedor fora do ar.

```bash
grep -oE "Limit 8000, Requested [0-9]+" <log> | tail
```

`Requested` acima de 8000 → algum bloco de contexto cresceu além do teto. Já
aconteceu com a lista de lotes do Funcionário, que era serializada inteira:
20 lotes = 1,4k tokens só naquele bloco, request a 8120, agente 503 em
**qualquer** pergunta.

Detalhe crucial: `max_tokens` é **reservado** contra o orçamento. Ver
`mottainai-orcamento-llm`.

---

## Resposta cortada no meio

`finish_reason: "length"`, não `"stop"`. É truncamento de saída.

Já derrubou o Motor Preditivo: o padrão implícito de 3072 tokens cortava o JSON
estruturado no meio, e o Juiz reprovava — corretamente — por malformado. O
sintoma parecia "o Juiz está implicando".

---

## Latência de 20 a 120 segundos

Normalmente é `429` do free tier com o retry/backoff funcionando como
projetado (`llm_max_retries = 3`).

```bash
grep -c "429 Too Many Requests" <log>
```

Não trate como bug antes de descartar isso.

---

## Todas as mensagens bloqueadas no guardrail de entrada

Se o Redis estiver lento ou fora, o rate-limit é o caminho **não** fail-open.
Timeouts em `config/settings.py`: `redis_connect_timeout_seconds` e
`redis_socket_timeout_seconds` (6.0 — subiram de 2.0 exatamente por isso).

O cache RAG e a deny-list de JWT **são** fail-open por decisão explícita.

---

## `500` ao gravar no Mongo

Validação de `$jsonSchema`. Os testes mockam o Mongo e não pegam.

Ver `mottainai-contratos-mongo`. Os três clássicos: `sources[].type` fora do
enum, `agent_executions.error` como string, `rag_documents` sem
`category`/`version`.

---

## Previsão de demanda zerada

`sale_date` é `timestamp`, e a série era agrupada pelo valor cru, então nunca
casava com a data usada na busca — resultado silenciosamente zero. Corrigido
com `CAST(... AS DATE)` na query e normalização em
`app/analytics/forecasting.py`.

Se voltar a zerar, confira os dois lados.

---

## O agente cita "Fonte 3" ou nomeia sistema interno

O modelo repete os rótulos que recebe. Já aconteceu duas vezes:

- O contexto RAG era prefixado com `[Fonte N — score X]` e o agente escreveu "(ver Fonte 3)" para o cliente.
- Os prompts do Funcionário e do Dono rotulavam blocos como `--- Dados operacionais (PostgreSQL) ---` enquanto a própria regra do prompt proibia dizer "PostgreSQL".

A correção nunca é instruir mais forte. **É parar de entregar o rótulo.**

---

## O Juiz reprovou uma resposta que parece boa

Antes de mexer em prompt:

1. O Juiz não é determinístico nem a temperatura 0 — a mesma resposta já pontuou 0.35 e 0.86 em avaliações idênticas. Por isso existe retry único. **Repita 3 vezes antes de concluir que há regressão.**
2. Se a base não tem a informação, a recusa está certa e a nota baixa reflete "não respondeu". Confira se o dado existe antes de culpar o prompt.

---

## Embeddings falham ao carregar

`SentenceTransformer(..., local_files_only=True)` é deliberado: impede que uma
consulta RAG dependa da rede ou quebre em certificado corporativo. O modelo é
preparado por `scripts/generate_embeddings.py` e fica em cache no host.

Se falhar, o cache local sumiu — rode o script, não remova a flag.

---

## `setup_mongo.py` aborta ou cria um ambiente diferente

O banco em uso **não foi provisionado por esse script**: tem validadores
`$jsonSchema` que ele não cria e índices com outros nomes (`ix_*`/`ux_*`).

A criação de índice tolera `IndexOptionsConflict` e informa qual índice
manteve. Se você criar um ambiente novo pelo script, ele **não** terá os
validadores — e os bugs de contrato do Mongo passarão despercebidos ali.

---

## Ambiente local: pegadinhas de Windows

- Porta 8000 ocupada por instância anterior: `Stop-Process -Id <pid> -Force`. Confirme que o novo processo realmente subiu — o uvicorn falha com `Errno 10048` e o `/health` responde **da instância antiga**, dando a impressão de que o código novo está no ar.
- Docker Desktop cai entre sessões; os containers `mottainai-postgres` e `mottainai-redis` precisam de `docker start`.
- Tokens de desenvolvimento expiram no meio do trabalho: `scripts/generate_dev_token.py --usuario-id N --empresa-id N --role ROLE`.
