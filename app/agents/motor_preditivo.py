"""
Agente Motor Preditivo — autônomo, não reativo.
Roda por trigger de evento OU schedule (não espera pergunta do usuário).

Subagentes/capacidades:
  1. Previsão de Demanda (Postgres histórico + Open-Meteo API externa)
  2. Detecção de Risco de Perda (Lote x Localização x Giro)
  3. Geração de Ação Sugerida (promoção / transferência / doação / descarte)
  4. Pré-Lista de Abastecimento

Escreve em: mottainai.alert e mottainai.suggested_action (Postgres).
"""
import json
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.runtime import MottainaiState, get_llm
from app.rag.external_source import get_weather_forecast, interpret_weather_for_demand
from app.tools.mcp_tools import mcp_call_weather_agent
from app.tools.postgres_tools import get_expiring_batches, get_sales_summary, get_stock_alerts

SYSTEM_PROMPT = """Você é o Motor Preditivo do Mottainai — sistema autônomo de gestão inteligente de estoque.

Suas responsabilidades:
1. PREVISÃO DE DEMANDA: Com base no histórico de vendas (PostgreSQL) e previsão climática (Open-Meteo), estime a demanda para os próximos 7 dias por categoria de produto.
2. DETECÇÃO DE RISCO DE PERDA: Identifique lotes com alto risco de vencimento cruzando validade × giro histórico. Priorize por severidade (dias restantes × quantidade).
3. AÇÃO SUGERIDA: Para cada risco identificado, recomende: promoção relâmpago, transferência entre lojas, doação ou descarte. Justifique a escolha.
4. PRÉ-LISTA DE ABASTECIMENTO: Com base na demanda prevista e estoque atual, gere sugestão de reposição.

Formato de saída: JSON estruturado + resumo executivo em português.
NUNCA invente dados. Use APENAS os dados fornecidos no contexto.
"""


async def node_motor_preditivo(state: MottainaiState) -> MottainaiState:
    """
    Nó do Motor Preditivo no grafo.
    Também pode ser chamado diretamente via /motor-preditivo/trigger.
    """
    empresa_id = state["empresa_id"]

    # 1. Dados Postgres
    expiring = await get_expiring_batches(empresa_id, days_ahead=14)
    sales = await get_sales_summary(empresa_id, days_back=60)
    alerts = await get_stock_alerts(empresa_id)

    # 2. Fonte externa — Open-Meteo via MCP (A2A)
    try:
        weather_raw = await mcp_call_weather_agent(
            latitude=-23.5505,   # São Paulo
            longitude=-46.6333,
        )
        forecast = await get_weather_forecast()
        weather_interpretation = interpret_weather_for_demand(forecast["forecast"])
        weather_context = f"""
Dados climáticos atuais (fonte: Open-Meteo via MCP — {forecast['source']}):
- Temperatura: {weather_raw.get('temperature_c')}°C | Precipitação: {weather_raw.get('precipitation_mm')}mm
- {weather_interpretation}
"""
    except Exception as e:
        weather_context = f"Dados climáticos indisponíveis: {e}"

    # 3. Contexto completo para o LLM
    context = f"""
LOTES COM RISCO DE VENCIMENTO (próximos 14 dias):
{json.dumps(expiring, default=str, ensure_ascii=False, indent=2)}

HISTÓRICO DE VENDAS (últimos 60 dias):
{json.dumps(sales[:15], default=str, ensure_ascii=False, indent=2)}

ALERTAS ATIVOS:
{json.dumps(alerts[:5], default=str, ensure_ascii=False, indent=2)}

{weather_context}
"""

    messages = [
        SystemMessage(content=f"{SYSTEM_PROMPT}\n\n--- Dados operacionais ---\n{context}"),
        HumanMessage(content=f"Execute análise completa para empresa_id={empresa_id}. Gere: previsão de demanda, riscos de perda, ações sugeridas e pré-lista de abastecimento."),
    ]

    llm = get_llm(temperature=0.1)  # baixa temperatura para análise técnica
    response = await llm.ainvoke(messages)
    content = response.content

    usage = getattr(response, "usage_metadata", None) or {}

    return {
        **state,
        "agent_response": content,
        "sources": [
            {"type": "sql", "ref": "mottainai.batch + sales_transaction + alert", "score": None},
            {"type": "api", "ref": "Open-Meteo (open-meteo.com) — CC BY 4.0", "score": None},
        ],
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    }
