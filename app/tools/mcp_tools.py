"""
MCP Tools — Integração MCP/A2A para o Mottainai.
Permite que agentes se comuniquem com sistemas externos via protocolo MCP.

No contexto acadêmico, implementamos:
  - Um servidor MCP local que expõe ferramentas internas como MCP endpoints
  - Um cliente MCP que consome APIs externas (simulado com Open-Meteo + dados mock)

Referência: https://modelcontextprotocol.io/introduction
"""
import httpx
from typing import Any


# Simulação de chamada A2A (Agent-to-Agent):
# O Motor Preditivo chama o "agente externo de clima" via interface MCP
MCP_WEATHER_ENDPOINT = "https://api.open-meteo.com/v1/forecast"


async def mcp_call_weather_agent(latitude: float, longitude: float) -> dict[str, Any]:
    """
    Chamada MCP ao agente externo de previsão climática.
    Simula o padrão A2A: este agente chama um agente externo especializado.
    Fonte: Open-Meteo (CC BY 4.0, gratuito)
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": ["temperature_2m", "precipitation", "weathercode"],
        "timezone": "America/Sao_Paulo",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(MCP_WEATHER_ENDPOINT, params=params)
        resp.raise_for_status()
        data = resp.json()

    current = data.get("current", {})
    return {
        "mcp_source": "open-meteo-weather-agent",
        "temperature_c": current.get("temperature_2m"),
        "precipitation_mm": current.get("precipitation"),
        "weathercode": current.get("weathercode"),
        "is_hot": (current.get("temperature_2m") or 0) >= 28,
        "has_rain": (current.get("precipitation") or 0) > 0.5,
    }


async def mcp_expose_tool(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """
    Interface genérica MCP — expõe ferramentas internas do Mottainai
    como se fossem endpoints MCP para consumo por agentes externos.

    Ferramentas expostas:
      - get_active_alerts: alertas ativos de estoque
      - get_replenishment_suggestion: sugestão de reposição
      - get_company_kpis: KPIs da empresa
    """
    from app.tools.postgres_tools import get_stock_alerts, get_kpis

    empresa_id = params.get("empresa_id")
    if isinstance(empresa_id, bool) or not isinstance(empresa_id, int) or empresa_id < 1:
        return {"tool": tool_name, "error": "Empresa autorizada obrigatória."}

    if tool_name == "get_active_alerts":
        results = await get_stock_alerts(empresa_id)
        return {"tool": tool_name, "result": results}

    elif tool_name == "get_company_kpis":
        kpis = await get_kpis(empresa_id)
        return {"tool": tool_name, "result": kpis}

    else:
        return {"tool": tool_name, "error": f"Ferramenta '{tool_name}' não encontrada."}
