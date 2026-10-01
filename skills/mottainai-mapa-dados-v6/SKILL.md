---
name: Mottainai Mapa de Dados v6
description: Os joins canônicos do schema PostgreSQL v6, os filtros obrigatórios (soft-delete, status de item de venda, escopo de empresa) e por que o RLS não protege nada hoje. Use ao escrever ou revisar qualquer query em app/tools/postgres_tools.py.
metadata:
  category: reference
  version: "1.0.0"
---

# Mapa de dados — PostgreSQL v6

Use esta skill ao escrever, revisar ou depurar qualquer query operacional.
O PostgreSQL é a fonte da verdade; o Mongo é a camada de IA.

## O join canônico do tenant

Toda query com escopo de empresa chega na company **através da loja**:

```sql
FROM mottainai.inventory i
JOIN mottainai.retail_store rs ON rs.store_id = i.store_id
JOIN mottainai.company     c  ON c.company_id = rs.company_id
WHERE c.company_id = :empresa_id
```

Para vendas, o join tem **chave composta** — `sales_transaction` é
particionada por `sale_date`, e a partição precisa entrar no join:

```sql
JOIN mottainai.sale_item si
  ON si.sale_id = st.sale_id AND si.sale_date = st.sale_date
```

Esquecer `si.sale_date` produz resultado errado, não erro.

## Os filtros que não podem faltar

### 1. Soft-delete, em toda tabela do join

```sql
AND c.active = TRUE  AND c.deleted_at  IS NULL
AND rs.active = TRUE AND rs.deleted_at IS NULL
AND p.active = TRUE  AND p.deleted_at  IS NULL
```

Uma tabela esquecida traz registro apagado.

### 2. `si.status = 'SOLD'` em qualquer soma de venda

`sale_item.status` é enum de `SOLD | CANCELED | RETURNED`. Sem o filtro, você
soma item cancelado e devolvido.

Esse bug já existiu: `get_kpis` e `get_kpis_by_store` filtravam,
`get_sales_summary` e `get_daily_sales_series` não — e são justamente as duas
que alimentam "top produtos" e **a previsão de demanda**. Sobreviveu porque o
banco semeado só tem linhas `SOLD`, então a inflação era de 0,00% e nenhum
número visível mudava. É o tipo de defeito que só aparece com dado real.

### 3. `st.status = 'COMPLETED'` para a transação

Nível de transação, complementar ao `si.status` (nível de item). Os dois.

## Armadilhas de tipo

- **`sale_date` é `timestamp`, não `date`.** Agrupar pelo valor cru dá uma linha por instante de venda, e a série nunca casa com uma busca por data. Use `CAST(st.sale_date AS DATE)` no `SELECT` e no `GROUP BY`. Isso já zerou toda previsão silenciosamente (`app/analytics/forecasting.py` normaliza também, por segurança).
- Colunas monetárias e de quantidade voltam como `Decimal`. Serializar com `json.dumps(..., default=str)`.

## RLS: existe, mas não protege

Seis tabelas têm `ENABLE ROW LEVEL SECURITY` e há policies escritas. **Elas
não disparam.** Verificado no banco em execução:

- a aplicação conecta como `mottainai`, que é **dono** de todas as seis tabelas — e dono ignora RLS sem `FORCE`;
- `FORCE ROW LEVEL SECURITY` **não aparece em lugar nenhum** do schema;
- pior: esse usuário é **superusuário**, e superusuário ignora RLS **mesmo com `FORCE`**;
- os `GRANT`s para `app_user` estão comentados no schema.

Prova empírica: com `app.current_company_id = 999999`, `retail_store` continua
devolvendo as 5 lojas.

> **Consequência para você:** o isolamento entre empresas é **apenas** o
> `WHERE c.company_id = :empresa_id` que você escrever. Não existe rede de
> segurança. Uma query sem esse filtro vaza dados de outro tenant e nada
> avisa.

Corrigir de verdade exige `app_user` **não-superusuário** + `FORCE` nas seis
tabelas + índice em `retail_store(company_id)` + reescrever as policies
trocando a função plpgsql por `current_setting('app.current_company_id', true)`
inline. Só adicionar `FORCE` não resolve, por causa do superusuário.

## Tabelas que existem e ninguém usa

`kpi_cache`, `event_queue` e `query_performance` estão no schema com zero
referências no código Python. Não assuma que são alimentadas.

## Checklist antes de abrir PR com query nova

- [ ] `WHERE c.company_id = :empresa_id` presente
- [ ] `active = TRUE` e `deleted_at IS NULL` em **todas** as tabelas do join
- [ ] `si.status = 'SOLD'` se soma venda
- [ ] `st.status = 'COMPLETED'` se toca `sales_transaction`
- [ ] `si.sale_date = st.sale_date` no join com `sale_item`
- [ ] `CAST(... AS DATE)` se agrupa por dia
- [ ] parâmetros vinculados (`:nome`), nunca f-string com valor
- [ ] **rodada contra o banco real**, não só teste com `_exec` mockado
