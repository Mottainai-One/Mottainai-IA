"""
RAG — External source: Open-Meteo (public API, no key, no cost).
Provides weather data the Predictive Engine correlates with demand for
perishables (heat → drinks/ice cream, rain → foot-traffic drop).

Note: interpret_weather_for_demand() returns text injected directly into
the Predictive Engine's LLM prompt, so its output is kept in Portuguese.
"""
import httpx
from datetime import date

from app.config import get_settings

settings = get_settings()


async def get_weather_forecast(
    latitude: float = -23.5505,   # São Paulo, Brazil, as the default
    longitude: float = -46.6333,
    days: int = 7,
) -> dict:
    """
    Queries the weather forecast for the next `days` days.
    Returns: max/min temperatures, precipitation and weather code.

    Source: Open-Meteo (https://open-meteo.com) — free, no API key required.
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": [
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "weathercode",
        ],
        "timezone": "America/Sao_Paulo",
        "forecast_days": days,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(settings.openmeteo_base_url, params=params)
        resp.raise_for_status()
        data = resp.json()

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    temp_max = daily.get("temperature_2m_max", [])
    temp_min = daily.get("temperature_2m_min", [])
    precipitation = daily.get("precipitation_sum", [])
    weathercodes = daily.get("weathercode", [])

    forecast = []
    for i, d in enumerate(dates):
        forecast.append(
            {
                "date": d,
                "temp_max": temp_max[i] if i < len(temp_max) else None,
                "temp_min": temp_min[i] if i < len(temp_min) else None,
                "precipitation_mm": precipitation[i] if i < len(precipitation) else None,
                "weathercode": weathercodes[i] if i < len(weathercodes) else None,
                "is_hot": (temp_max[i] or 0) >= 28,
                "has_rain": (precipitation[i] or 0) > 5,
            }
        )

    return {
        "location": {"latitude": latitude, "longitude": longitude},
        "source": "Open-Meteo (https://open-meteo.com) — CC BY 4.0",
        "forecast": forecast,
    }


def interpret_weather_for_demand(forecast: list[dict]) -> str:
    """
    Interprets the weather forecast as text for the Predictive Engine.
    Converts raw data into actionable stock insights.
    (Output kept in Portuguese — see module docstring.)
    """
    hot_days = sum(1 for d in forecast if d.get("is_hot"))
    rainy_days = sum(1 for d in forecast if d.get("has_rain"))
    total = len(forecast)

    lines = [f"Previsão para os próximos {total} dias:"]

    if hot_days >= total // 2:
        lines.append(
            f"- {hot_days}/{total} dias com temperatura alta (≥28°C). "
            "AUMENTAR estoque de bebidas geladas, sorvetes e produtos refrescantes."
        )
    if rainy_days >= total // 3:
        lines.append(
            f"- {rainy_days}/{total} dias com chuva. "
            "ESPERAR queda de até 20% no fluxo em lojas físicas. "
            "Reduzir pré-lista de abastecimento para produtos de alto giro externo."
        )
    if hot_days < 2 and rainy_days < 2:
        lines.append("- Clima estável. Demanda esperada dentro do padrão histórico.")

    return "\n".join(lines)
