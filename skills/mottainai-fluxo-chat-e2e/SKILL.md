---
name: Mottainai Fluxo Chat E2E
description: O grafo LangGraph do /chat nó a nó — quem roteia, quem escreve no Mongo, como o Juiz reprova e quais são as mensagens de fallback. Use para responder "por que a resposta virou aquele texto genérico?" ou "onde a mensagem é gravada?".
metadata:
  category: reference
  version: "1.0.0"
---

# Fluxo do `/chat`, ponta a ponta

Use esta skill ao depurar uma resposta inesperada, ao adicionar um nó ao grafo,
ou para entender onde algo é persistido.

## O caminho

```
POST /chat (JWT → sub, empresa_id, role)          interfaces/api/main.py
  └─ mottainai_graph.ainvoke(state)               app/agents/supervisor.py

     guardrail_entrada  ──[bloqueado]──> END
          │                              (nunca chega em agente nenhum)
     load_context        Mongo: conversa + histórico + memória longa
     supervisor          roteia por role + keywords
          ├─ cliente ─┐
          ├─ faq ─────┤
          ├─ funcionario ─┤
          ├─ dono ────────┤
          └─ motor_preditivo ─┘
                        └──> juiz ──> guardrail_saida ──> END
```

Todo agente passa pelo Juiz. Não há atalho — a aresta `juiz → guardrail_saida`
é simples, sem condicional.

Fora do grafo: `POST /shelf/analyze` (visão) e `POST /motor-preditivo/trigger`
(chama o nó direto). A Governança roda em `BackgroundTasks`, pós-resposta.

## Roteamento (`supervisor.py`)

Por **role do JWT**, com keywords desempatando:

| Role | Vai para |
|---|---|
| `CLIENTE` | `cliente` ou `faq` |
| `ESTOQUISTA`, `GERENTE` | `funcionario` |
| `DONO` | `dono`, ou `motor_preditivo` se a pergunta bater em `KEYWORDS_PREDITIVO` |

É determinístico e barato, mas é match de substring — **é o teto de qualidade
do produto**. "Quanto vou vender semana que vem?" não contém keyword preditiva
e cai no agente Dono; "risco de perda" dispara o motor mesmo fora de contexto.
Ao investigar "o agente errado respondeu", olhe as keywords antes do prompt.

## Quem escreve o quê

| Nó | Escreve |
|---|---|
| `load_context` | nada (só lê) |
| agentes | nada no Mongo; leem Postgres/RAG |
| `juiz` | `prompt_evaluations` (inline, no caminho crítico) |
| `guardrail_saida` | **`messages` ×2, `memories` ×1..N** |
| pós-resposta (`BackgroundTasks`) | `agent_executions`, `metrics`, governança |

**O nome `guardrail_saida` engana.** Além de filtrar PII e vazamento, ele
persiste o histórico inteiro da rodada: a mensagem do usuário, a resposta, e a
memória de longo prazo. É lá que a conversa é gravada — não no endpoint.
Consequência: uma falha de Mongo nesse nó derruba uma resposta que o Juiz já
tinha aprovado.

## O Juiz

`app/agents/juiz.py`, `temperature=0.0`, **fail-closed**: erro na avaliação
reprova, nunca libera.

- Aprova com `approved == True` **e** `confidence_score >= 0.7`.
- Não confia no booleano sozinho — o modelo já retornou `approved` inconsistente com o próprio score. **O score decide.**
- **Retry único quando reprova.** O Juiz não é determinístico nem a temperatura 0: a mesma resposta correta já pontuou 0.35 numa avaliação e 0.86 na repetição idêntica. Por isso a segunda chance existe.
- Ele avalia a lista `sources` estruturada, não o texto do contexto RAG.

### Quando você vê uma destas, foi o Juiz ou o guardrail

| Texto | Origem |
|---|---|
| "Não encontrei essa informação com segurança nos dados disponíveis. Pode reformular…" | Juiz reprovou (score < 0.7) |
| "Não consigo fornecer essa informação no momento." | `guardrail_saida` bloqueou (vazamento interno) |
| "Não consigo confirmar…" | Juiz indisponível — fail-closed |

**Reprovação não significa bug.** Se a base não tem a informação, o agente
recusa corretamente e o Juiz pontua baixo por "não respondeu". Antes de culpar
o prompt, confira se o dado existe: veja a skill `mottainai-mapa-dados-v6`
para o Postgres, e os chunks recuperados para o RAG.

## Depurando uma resposta ruim

1. **Qual agente pegou?** Campo `agent` na resposta. Se for o errado → keywords do supervisor.
2. **Foi reprovada?** `judge_approved` / `judge_score` na resposta.
3. **Tinha fonte?** `sources` vazio em pergunta de RAG = a busca não achou nada; o problema é conteúdo ou embedding, não prompt.
4. **Truncou?** Ver skill `mottainai-orcamento-llm`.
5. **503 em tudo?** Orçamento de tokens, mesma skill.

## Ao mexer em prompt

Duas lições que custaram caro neste repositório:

- **Teste mockado não valida prompt.** Toda mudança de prompt precisa de bateria ao vivo contra a API real.
- **O modelo cita de volta os rótulos que você entrega.** O contexto RAG já foi prefixado com `[Fonte N — score X]` e o agente escreveu "(ver Fonte 3)" para o cliente. A correção não foi instruir mais forte — foi **parar de entregar o rótulo**. O mesmo valia para `--- Dados operacionais (PostgreSQL) ---`, que nomeava para o modelo exatamente o que o prompt proibia dizer.
