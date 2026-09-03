---
name: Mottainai Orçamento LLM
description: O teto de 8000 tokens por minuto do Groq free tier e como ele moldou os limites de contexto do código. Use antes de aumentar qualquer contexto de agente, mexer em max_tokens, ou quando o chat responder 503 para tudo.
metadata:
  category: reference
  version: "1.0.0"
---

# Orçamento de tokens dos agentes

Use esta skill antes de aumentar o contexto de um agente, alterar
`llm_max_output_tokens`, ou ao investigar `503` generalizado no `/chat`.

## O teto

O provedor padrão é o **Groq free tier**: `llama-3.3-70b-versatile`,
**8000 tokens por minuto**.

O detalhe que pega todo mundo: **`max_tokens` é reservado contra esse
orçamento, não cobrado pelo que a resposta realmente usar**. O provedor soma
`input + max_tokens` e recusa antes de gerar:

```
Error code: 413 - Request too large ... on tokens per minute (TPM):
Limit 8000, Requested 8120
```

Então a conta que precisa fechar é:

```
tokens do prompt + llm_max_output_tokens  <  8000
```

E fechar **duas vezes por mensagem**: o Juiz avalia toda resposta, com o seu
próprio prompt, dentro da mesma janela de um minuto.

## Por que `llm_max_output_tokens = 4096`

Está em `config/settings.py`. Não é número redondo por acaso:

- **Sem configurar**, o cliente usava 3072 como padrão e **truncava o JSON do Motor Preditivo no meio**, que o Juiz então reprovava — corretamente — por estar malformado. O sintoma parecia "o Juiz está implicando"; a causa era truncamento.
- **Com 8192**, todo request virava `413`, porque a reserva sozinha já comia o orçamento.

4096 é o valor calibrado com o prompt real do Motor Preditivo (~2,3k tokens).
Ao mexer nele, meça o prompt antes.

## Os caps de contexto e por que existem

Todo bloco injetado no prompt é limitado. Não é estética:

| Agente | Bloco | Cap |
|---|---|---|
| `funcionario.py` | lotes vencendo | `EXPIRING_BATCHES_IN_PROMPT = 10` |
| `funcionario.py` | inventário | `[:10]` |
| `funcionario.py` | alertas / notificações | `limit=5` na query |
| `funcionario.py` | análises de prateleira | `limit=3` |
| `dono.py` | top produtos | `[:10]` |
| `dono.py` | alertas | `[:5]` |
| `motor_preditivo.py` | lotes vencendo | `[:10]` |
| `motor_preditivo.py` | vendas | `[:15]` |

O cap dos lotes do Funcionário nasceu de um incidente real: a lista era
serializada inteira, e com 20 lotes perto do vencimento aquele bloco sozinho
passava de 1,4k tokens. O request chegou a 8120 contra o teto de 8000 e o
agente passou a responder **503 para qualquer pergunta**, não só sobre lotes.

> **Regra:** todo bloco de contexto que cresce com o volume de dados precisa de
> cap. Um bloco sem cap é uma bomba-relógio — funciona no dev com 3 registros e
> derruba o agente quando o banco enche.

E quando truncar, **diga no título que truncou**:

```python
LOTES VENCENDO EM 7 DIAS (total {len(expiring_data)}, listando os {len(expiring_shown)} mais urgentes):
```

Sem isso o agente apresenta o recorte como se fosse o todo — "você tem 10 lotes
vencendo" quando são 40.

## Diagnóstico rápido

**Chat respondendo 503 em tudo:**

```bash
grep -oE "Limit 8000, Requested [0-9]+" <log da api> | tail -5
```

Se aparecer `Requested` acima de 8000, é orçamento — não é o provedor fora do ar.
Procure o bloco de contexto sem cap que cresceu.

**Latência alta (20–120 s):** normalmente é `429` com o retry/backoff do
`app/agents/runtime.py` funcionando como projetado (`llm_max_retries = 3`).
Verifique antes de tratar como bug:

```bash
grep -c "429 Too Many Requests" <log da api>
```

**Resposta cortada no meio:** `finish_reason: "length"`, não `"stop"`. É
truncamento de saída, não o modelo "decidindo parar".

## Ao trocar de provedor

`app/agents/runtime.py` suporta `ollama_local`, `ollama` e `groq`, e passa
`max_tokens=settings.llm_max_output_tokens` nos três. Um provedor sem teto de
TPM (Ollama local) elimina toda essa classe de problema — os caps de contexto
continuam valendo por qualidade de resposta, mas deixam de ser questão de
disponibilidade.
