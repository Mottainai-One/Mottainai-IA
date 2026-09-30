"""Tests for the deterministic demand-forecasting math used by the Predictive Engine."""
import datetime
import unittest

from app.analytics.forecasting import build_daily_series, forecast_product_demand


class BuildDailySeriesTests(unittest.TestCase):
    def test_fills_missing_days_with_zero_and_orders_oldest_first(self):
        reference = datetime.date(2026, 1, 10)
        rows = [
            {"product_id": 1, "sale_date": datetime.date(2026, 1, 9), "quantity_sold": 5},
            {"product_id": 1, "sale_date": datetime.date(2026, 1, 7), "quantity_sold": 3},
            {"product_id": 2, "sale_date": datetime.date(2026, 1, 9), "quantity_sold": 99},
        ]

        series = build_daily_series(rows, product_id=1, days_back=3, reference_date=reference)

        self.assertEqual(series, [3, 0, 5])  # Jan 7, 8, 9 — Jan 8 has no sale row

    def test_ignores_rows_from_other_products(self):
        reference = datetime.date(2026, 1, 5)
        rows = [{"product_id": 2, "sale_date": datetime.date(2026, 1, 4), "quantity_sold": 10}]

        series = build_daily_series(rows, product_id=1, days_back=2, reference_date=reference)

        self.assertEqual(series, [0, 0])

    def test_accepts_datetime_sale_date_as_returned_by_postgres(self):
        # mottainai.sales_transaction.sale_date is a `timestamp`, so a row can
        # carry a datetime rather than a date. A datetime never matches the
        # date key this function looks up, which silently zeroed every real
        # forecast while the date-only fixtures above kept passing.
        reference = datetime.date(2026, 1, 10)
        rows = [
            {"product_id": 1, "sale_date": datetime.datetime(2026, 1, 9, 13, 1, 37), "quantity_sold": 5},
            {"product_id": 1, "sale_date": datetime.datetime(2026, 1, 7, 2, 35, 8), "quantity_sold": 3},
        ]

        series = build_daily_series(rows, product_id=1, days_back=3, reference_date=reference)

        self.assertEqual(series, [3, 0, 5])

    def test_sums_multiple_sales_on_the_same_day(self):
        reference = datetime.date(2026, 1, 3)
        rows = [
            {"product_id": 1, "sale_date": datetime.datetime(2026, 1, 2, 9, 0), "quantity_sold": 4},
            {"product_id": 1, "sale_date": datetime.datetime(2026, 1, 2, 18, 30), "quantity_sold": 6},
        ]

        series = build_daily_series(rows, product_id=1, days_back=1, reference_date=reference)

        self.assertEqual(series, [10])


class ForecastProductDemandTests(unittest.TestCase):
    def test_no_history_returns_zero_forecast(self):
        result = forecast_product_demand([])

        self.assertEqual(result["forecast_next_7_days"], 0)
        self.assertEqual(result["method"], "sem_historico")

    def test_short_history_falls_back_to_simple_average(self):
        # 10 units/day for 5 days -> not enough for a trend comparison (< 14 days)
        result = forecast_product_demand([10, 10, 10, 10, 10])

        self.assertEqual(result["method"], "media_simples_historico_curto")
        self.assertEqual(result["avg_daily_demand"], 10.0)
        self.assertIsNone(result["recent_week_avg_daily"])
        self.assertEqual(result["forecast_next_7_days"], 70)

    def test_flat_demand_forecasts_the_same_rate_forward(self):
        # 14 days of a steady 10 units/day: no trend, forecast should track it.
        result = forecast_product_demand([10] * 14)

        self.assertEqual(result["method"], "media_movel_ponderada_com_tendencia")
        self.assertEqual(result["trend_pct"], 0.0)
        self.assertEqual(result["forecast_next_7_days"], 70)

    def test_upward_trend_raises_the_forecast_above_the_long_run_average(self):
        prior_week = [10] * 7
        recent_week = [20] * 7  # demand doubled in the most recent week

        result = forecast_product_demand(prior_week + recent_week)

        self.assertEqual(result["trend_pct"], 100.0)
        self.assertGreater(result["forecast_next_7_days"], result["avg_daily_demand"] * 7)

    def test_trend_ratio_is_capped_against_a_single_noisy_spike(self):
        # One huge spike day inflates the recent week average a lot, but the
        # trend multiplier itself must stay capped at 2x, not blow up further.
        prior_week = [5] * 7
        recent_week = [5, 5, 5, 5, 5, 5, 500]

        result = forecast_product_demand(prior_week + recent_week)

        self.assertLessEqual(result["trend_pct"], 100.0)

    def test_downward_trend_is_capped_and_never_negative(self):
        prior_week = [10] * 7
        recent_week = [0] * 7

        result = forecast_product_demand(prior_week + recent_week)

        self.assertEqual(result["trend_pct"], -50.0)  # capped at the 0.5x floor
        self.assertGreaterEqual(result["forecast_next_7_days"], 0)

    def test_zero_prior_week_with_recent_sales_is_treated_as_a_new_trend_not_a_crash(self):
        prior_week = [0] * 7
        recent_week = [4] * 7

        result = forecast_product_demand(prior_week + recent_week)

        self.assertGreater(result["forecast_next_7_days"], 0)


if __name__ == "__main__":
    unittest.main()
