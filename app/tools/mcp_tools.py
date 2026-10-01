"""
MCP Tools — MCP/A2A integration for Mottainai.
Lets agents communicate with external systems via the MCP protocol.

For this course project, we implemented:
  - A local MCP server that exposes internal tools as MCP endpoints
  - An MCP client that consumes external APIs (simulated with Open-Meteo + mock data)

Reference: https://modelcontextprotocol.io/introduction
"""
from typing import Any

import httpx

# A2A (Agent-to-Agent) call simulation:
# The Predictive Engine calls the "external weather agent" via the MCP interface
MCP_WEATHER_ENDPOINT = "https://api.open-meteo.com/v1/forecast"


async def mcp_call_weather_agent(latitude: float, longitude: float) -> dict[str, Any]:
    """
    MCP call to the external weather forecast agent.
    Simulates the A2A pattern: this agent calls a specialized external agent.
    Source: Open-Meteo (CC BY 4.0, free)
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
    Generic MCP interface — exposes Mottainai's internal tools as if they
    were MCP endpoints for consumption by external agents.

    Exposed tools:
      - get_active_alerts: active stock alerts
      - get_replenishment_suggestion: restocking suggestion
      - get_company_kpis: company KPIs
    """
    from app.tools.postgres_tools import get_kpis, get_stock_alerts

    empresa_id = params.get("empresa_id")
    if isinstance(empresa_id, bool) or not isinstance(empresa_id, int) or empresa_id < 1:
        return {"tool": tool_name, "error": "An authorized company is required."}

    if tool_name == "get_active_alerts":
        results = await get_stock_alerts(empresa_id)
        return {"tool": tool_name, "result": results}

    elif tool_name == "get_company_kpis":
        kpis = await get_kpis(empresa_id)
        return {"tool": tool_name, "result": kpis}

    else:
        return {"tool": tool_name, "error": f"Tool '{tool_name}' not found."}
