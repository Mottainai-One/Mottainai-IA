"""
RAG — Fonte externa: Open-Meteo (API pública, sem chave, sem custo).
Fornece dados climáticos que o Motor Preditivo usa para correlacionar
com demanda de perecíveis (calor → bebidas/sorvete, chuva → queda de fluxo).
"""
import httpx
from datetime import date

from app.config import get_settings

settings = get_settings()


async def get_weather_forecast(
    latitude: float = -23.5505,   # São Paulo como default
    longitude: float = -46.6333,
    days: int = 7,
) -> dict:
    """
    Consulta previsão do tempo para os próximos `days` dias.
    Retorna: temperaturas máxima/mínima, precipitação e código de clima.

    Fonte: Open-Meteo (https://open-meteo.com) — gratuita, sem API key.
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
    Interpreta a previsão climática em texto para o Motor Preditivo.
    Converte dados brutos em insights acionáveis para o estoque.
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
