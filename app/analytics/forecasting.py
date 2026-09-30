"""Deterministic demand forecasting math for the Predictive Engine.

Kept separate from app/agents/motor_preditivo.py and free of any LLM calls so
the forecast itself is a pure, testable calculation — the agent's job is to
narrate these numbers, not to invent them.
"""
from __future__ import annotations

import datetime


def build_daily_series(
    rows: list[dict],
    product_id: int,
    days_back: int,
    reference_date: datetime.date | None = None,
) -> list[int]:
    """
    Turns sparse (product_id, sale_date, quantity_sold) rows from
    get_daily_sales_series into a dense, zero-filled daily series for one
    product, oldest day first, ending yesterday.
    """
    reference_date = reference_date or datetime.date.today()
    # sale_date is a Postgres `timestamp`, so it arrives as a datetime unless
    # the query casts it. A datetime never equals the plain date used for the
    # lookup below, which silently zeroed every forecast — normalize it here so
    # the function is correct for either shape.
    by_date: dict[datetime.date, int] = {}
    for row in rows:
        if row["product_id"] != product_id:
            continue
        sale_date = row["sale_date"]
        if isinstance(sale_date, datetime.datetime):
            sale_date = sale_date.date()
        by_date[sale_date] = by_date.get(sale_date, 0) + int(row["quantity_sold"])
    series = []
    for offset in range(days_back, 0, -1):
        day = reference_date - datetime.timedelta(days=offset)
        series.append(by_date.get(day, 0))
    return series


def forecast_product_demand(daily_quantities: list[int]) -> dict:
    """
    Weighted moving-average forecast for the next 7 days.

    Blends the long-run daily average with the ratio between the most recent
    week and the week before it, so a real recent trend nudges the forecast
    without letting one noisy day (a single big sale, a stockout day) swing
    it wildly. The trend ratio is capped to [0.5, 2.0] for the same reason.
    Falls back to a flat average when there isn't enough history (< 14 days)
    to compare two full weeks.
    """
    total_days = len(daily_quantities)
    if total_days == 0:
        return {
            "avg_daily_demand": 0.0,
            "recent_week_avg_daily": None,
            "trend_pct": 0.0,
            "forecast_next_7_days": 0,
            "days_of_history": 0,
            "method": "sem_historico",
        }

    avg_daily = sum(daily_quantities) / total_days

    if total_days >= 14:
        recent_week = daily_quantities[-7:]
        prior_week = daily_quantities[-14:-7]
        recent_avg = sum(recent_week) / 7
        prior_avg = sum(prior_week) / 7

        if prior_avg > 0:
            trend_ratio = recent_avg / prior_avg
        else:
            trend_ratio = 2.0 if recent_avg > 0 else 1.0
        trend_ratio = max(0.5, min(trend_ratio, 2.0))

        # 60% recent trend-adjusted rate, 40% long-run average: reacts to a
        # real shift without overfitting to the last week alone.
        projected_daily = (0.6 * recent_avg * trend_ratio) + (0.4 * avg_daily)
        trend_pct = round((trend_ratio - 1) * 100, 1)
        method = "media_movel_ponderada_com_tendencia"
    else:
        recent_avg = avg_daily
        projected_daily = avg_daily
        trend_pct = 0.0
        method = "media_simples_historico_curto"

    return {
        "avg_daily_demand": round(avg_daily, 2),
        "recent_week_avg_daily": round(recent_avg, 2) if total_days >= 14 else None,
        "trend_pct": trend_pct,
        "forecast_next_7_days": round(projected_daily * 7),
        "days_of_history": total_days,
        "method": method,
    }
