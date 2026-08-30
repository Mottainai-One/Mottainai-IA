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
from app.analytics.forecasting import build_daily_series, forecast_product_demand
from app.notifications.alert_webhook import notify_new_critical_alerts
from app.rag.external_source import get_weather_forecast, interpret_weather_for_demand
from app.tools.mcp_tools import mcp_call_weather_agent
from app.tools.postgres_tools import (
    get_daily_sales_series,
    get_expiring_batches,
    get_sales_summary,
    get_stock_alerts,
)

SYSTEM_PROMPT = """Você é o Motor Preditivo do Mottainai — sistema autônomo de gestão inteligente de estoque.

Suas responsabilidades:
1. PREVISÃO DE DEMANDA: Um cálculo de média móvel ponderada com tendência (feito em Python, não por você) já estima a demanda dos próximos 7 dias por produto. Sua função é EXPLICAR esses números — não recalculá-los nem inventar novos.
2. DETECÇÃO DE RISCO DE PERDA: Identifique lotes com alto risco de vencimento cruzando validade × giro histórico. Priorize por severidade (dias restantes × quantidade).
3. AÇÃO SUGERIDA: Para cada risco identificado, recomende: promoção relâmpago, transferência entre lojas, doação ou descarte. Justifique a escolha.
4. PRÉ-LISTA DE ABASTECIMENTO: Com base na previsão de demanda calculada e no estoque atual, gere sugestão de reposição.

Formato de saída: JSON estruturado + resumo executivo em português.
NUNCA invente dados. Use APENAS os dados fornecidos no contexto, incluindo a previsão de demanda já calculada.
"""


async def node_motor_preditivo(state: MottainaiState) -> MottainaiState:
    """
    Predictive Engine node in the graph.
    Can also be invoked directly via /motor-preditivo/trigger.

    Runs company-wide by default. If state["store_id"] is set, every query
    is scoped to that single store instead — e.g. to run the analysis for
    one location instead of the whole chain.
    """
    empresa_id = state["empresa_id"]
    store_id = state.get("store_id")

    # 1. Postgres data
    expiring = await get_expiring_batches(empresa_id, days_ahead=14, store_id=store_id)
    sales = await get_sales_summary(empresa_id, days_back=60, store_id=store_id)
    alerts = await get_stock_alerts(empresa_id, store_id=store_id)

    # 1b. Pushes a webhook for any CRITICAL alert not already notified —
    # see app/notifications/alert_webhook.py. Best-effort: never raises,
    # a notification failure must not break the analysis below.
    await notify_new_critical_alerts(empresa_id, alerts)

    # 1c. Real demand forecast (moving average + trend, computed in Python —
    # see app/analytics/forecasting.py) for the top products by volume.
    top_products = sales[:8]
    demand_forecast: list[dict] = []
    if top_products:
        product_ids = [p["product_id"] for p in top_products]
        daily_rows = await get_daily_sales_series(empresa_id, product_ids, days_back=28, store_id=store_id)
        for product in top_products:
            series = build_daily_series(daily_rows, product["product_id"], days_back=28)
            forecast = forecast_product_demand(series)
            demand_forecast.append({
                "product_id": product["product_id"],
                "product_name": product["product_name"],
                **forecast,
            })

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
    scope_line = f"Loja específica (store_id={store_id})" if store_id else "Todas as lojas da empresa"
    context = f"""
ESCOPO DA ANÁLISE: {scope_line}

PREVISÃO DE DEMANDA CALCULADA (média móvel ponderada com tendência, próximos 7 dias, já pronta — apenas explique):
{json.dumps(demand_forecast, default=str, ensure_ascii=False, indent=2)}

LOTES COM RISCO DE VENCIMENTO (próximos 14 dias):
{json.dumps(expiring, default=str, ensure_ascii=False, indent=2)}

HISTÓRICO DE VENDAS (últimos 60 dias, para contexto adicional):
{json.dumps(sales[:15], default=str, ensure_ascii=False, indent=2)}

ALERTAS ATIVOS:
{json.dumps(alerts[:5], default=str, ensure_ascii=False, indent=2)}

{weather_context}
"""

    messages = [
        SystemMessage(content=f"{SYSTEM_PROMPT}\n\n--- Dados operacionais ---\n{context}"),
        HumanMessage(content=f"Execute análise completa para empresa_id={empresa_id} ({scope_line}). Gere: previsão de demanda, riscos de perda, ações sugeridas e pré-lista de abastecimento."),
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
            {"type": "other", "ref": "app.analytics.forecasting (média móvel ponderada com tendência)", "score": None},
            {"type": "api", "ref": "Open-Meteo (open-meteo.com) — CC BY 4.0", "score": None},
        ],
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    }
