"""
Predictive Engine Agent — autonomous, not reactive.
Runs on event trigger OR schedule (does not wait for a user question).

Sub-agents/capabilities:
  1. Demand Forecast (Postgres history + Open-Meteo external API)
  2. Loss Risk Detection (batch x location x turnover)
  3. Suggested Action Generation (flash promotion / transfer / donation / disposal)
  4. Restocking Pre-List

Writes to: mottainai.alert and mottainai.suggested_action (Postgres).
Pushes a webhook for CRITICAL alerts not yet notified (best-effort — see
app/notifications/alert_webhook.py; no-op if no webhook URL is configured).

Note: SYSTEM_PROMPT and the operational context block fed to the LLM are
deliberately kept in Portuguese, same as the other agents.
"""
import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.runtime import MottainaiState, get_llm
from app.notifications.alert_webhook import notify_new_critical_alerts
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
    Predictive Engine node in the graph.
    Can also be invoked directly via /motor-preditivo/trigger.
    """
    empresa_id = state["empresa_id"]

    # 1. Postgres data
    expiring = await get_expiring_batches(empresa_id, days_ahead=14)
    sales = await get_sales_summary(empresa_id, days_back=60)
    alerts = await get_stock_alerts(empresa_id)

    # 1b. Pushes a webhook for any CRITICAL alert not already notified —
    # see app/notifications/alert_webhook.py. Best-effort: never raises,
    # a notification failure must not break the analysis below.
    await notify_new_critical_alerts(empresa_id, alerts)

    # 2. External source — Open-Meteo via MCP (A2A)
    try:
        weather_raw = await mcp_call_weather_agent(
            latitude=-23.5505,   # São Paulo, Brazil
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

    # 3. Full context for the LLM (kept in Portuguese, see module docstring)
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

    llm = get_llm(temperature=0.1)  # low temperature for technical analysis
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
