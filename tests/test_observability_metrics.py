"""Contract for app/observability/metrics.py — execution metrics recording
and the GET /metrics/summary aggregation (cost, latency percentiles,
per-agent stats, error rate, scaling projection, ROI). record_execution_metrics
already has one happy-path (zero-cost) test in tests/test_integrations_metrics.py;
not duplicated here."""
import unittest
from unittest.mock import patch

from app.observability import metrics


class FakeMetricsCollection:
    def __init__(self, documents):
        self._documents = documents

    def find(self, query):
        class Cursor:
            def __init__(self, docs):
                self._docs = docs

            async def to_list(self, length):
                return self._docs
        return Cursor(self._documents)


class FakeAgentExecutionsCollection:
    def __init__(self, count=0):
        self.count = count
        self.last_query = None

    async def count_documents(self, query):
        self.last_query = query
        return self.count


def _db(metrics_docs, alerts_resolved=0):
    return type("Database", (), {
        "metrics": FakeMetricsCollection(metrics_docs),
        "agent_executions": FakeAgentExecutionsCollection(alerts_resolved),
    })()


class RecordExecutionMetricsValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_bool_empresa_id(self):
        with self.assertRaises(ValueError):
            await metrics.record_execution_metrics("s1", None, "cliente", None, "m", 1, 1, 0.1, empresa_id=True)

    async def test_rejects_non_int_empresa_id(self):
        with self.assertRaises(ValueError):
            await metrics.record_execution_metrics("s1", None, "cliente", None, "m", 1, 1, 0.1, empresa_id="42")

    async def test_rejects_empresa_id_below_one(self):
        with self.assertRaises(ValueError):
            await metrics.record_execution_metrics("s1", None, "cliente", None, "m", 1, 1, 0.1, empresa_id=0)


class PercentileTests(unittest.TestCase):
    def test_returns_zero_for_empty_list(self):
        self.assertEqual(metrics._percentile([], 50), 0.0)

    def test_p50_matches_median_for_odd_length_list(self):
        self.assertEqual(metrics._percentile([1.0, 3.0, 2.0], 50), 2.0)

    def test_p95_interpolates_between_ranks(self):
        self.assertEqual(metrics._percentile([1.0, 2.0, 3.0, 4.0], 95), 3.85)


class GetMetricsSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_empty_summary_when_no_metrics_found(self):
        with patch.object(metrics, "get_mongo_db", return_value=_db([])):
            result = await metrics.get_metrics_summary(42)

        self.assertEqual(result["total_requests"], 0)
        self.assertEqual(result["periodo_dias"], 7)

    async def test_computes_total_tokens_and_cost(self):
        docs = [
            {"inputTokens": 1000, "outputTokens": 500, "estimatedCost": 0.01},
            {"inputTokens": 2000, "outputTokens": 1000, "estimatedCost": 0.02},
        ]
        with patch.object(metrics, "get_mongo_db", return_value=_db(docs)):
            result = await metrics.get_metrics_summary(42)

        self.assertEqual(result["tokens"], {"input": 3000, "output": 1500})
        self.assertEqual(result["custo_total_usd"], 0.03)

    async def test_computes_latency_percentiles(self):
        docs = [{"latency": 1.0}, {"latency": 2.0}, {"latency": 3.0}]
        with patch.object(metrics, "get_mongo_db", return_value=_db(docs)):
            result = await metrics.get_metrics_summary(42)

        self.assertEqual(result["latencia"]["avg_s"], 2.0)
        self.assertEqual(result["latencia"]["p50_s"], 2.0)
        self.assertEqual(result["latencia"]["min_s"], 1.0)
        self.assertEqual(result["latencia"]["max_s"], 3.0)

    async def test_computes_per_agent_stats(self):
        docs = [
            {"agent": "cliente", "latency": 1.0, "status": "completed", "judgeScore": 0.9},
            {"agent": "cliente", "latency": 3.0, "status": "completed", "judgeScore": 0.8},
            {"agent": "funcionario", "latency": 2.0, "status": "error"},
        ]
        with patch.object(metrics, "get_mongo_db", return_value=_db(docs)):
            result = await metrics.get_metrics_summary(42)

        cliente = result["agentes"]["cliente"]
        self.assertEqual(cliente["requests"], 2)
        self.assertEqual(cliente["avg_latency_s"], 2.0)
        self.assertEqual(cliente["error_rate_pct"], 0)
        self.assertEqual(cliente["judge_avg_score"], 0.85)

        funcionario = result["agentes"]["funcionario"]
        self.assertEqual(funcionario["requests"], 1)
        self.assertEqual(funcionario["error_rate_pct"], 100.0)
        self.assertIsNone(funcionario["judge_avg_score"])

    async def test_counts_status_error_as_error(self):
        docs = [{"status": "error", "latency": 1.0}]
        with patch.object(metrics, "get_mongo_db", return_value=_db(docs)):
            result = await metrics.get_metrics_summary(42)

        self.assertEqual(result["qualidade"]["execucoes_com_erro"], 1)
        self.assertEqual(result["qualidade"]["indice_erros_pct"], 100.0)

    async def test_counts_judge_score_below_0_7_as_error(self):
        docs = [{"status": "completed", "judgeScore": 0.5, "latency": 1.0}]
        with patch.object(metrics, "get_mongo_db", return_value=_db(docs)):
            result = await metrics.get_metrics_summary(42)

        self.assertEqual(result["qualidade"]["respostas_baixa_qualidade"], 1)
        self.assertEqual(result["qualidade"]["indice_erros_pct"], 100.0)

    async def test_resolucoes_aprovadas_requires_completed_and_score_ge_0_7(self):
        docs = [
            {"status": "completed", "judgeScore": 0.8, "estimatedCost": 0.01},
            {"status": "completed", "judgeScore": 0.5, "estimatedCost": 0.01},
            {"status": "error", "judgeScore": 0.9, "estimatedCost": 0.01},
        ]
        with patch.object(metrics, "get_mongo_db", return_value=_db(docs)):
            result = await metrics.get_metrics_summary(42)

        self.assertEqual(result["resolucoes_aprovadas"], 1)
        self.assertEqual(result["custo_por_resolucao_usd"], 0.03)

    async def test_custo_por_resolucao_is_none_when_no_resolutions(self):
        docs = [{"status": "error", "judgeScore": 0.9, "estimatedCost": 0.01}]
        with patch.object(metrics, "get_mongo_db", return_value=_db(docs)):
            result = await metrics.get_metrics_summary(42)

        self.assertIsNone(result["custo_por_resolucao_usd"])

    async def test_scaling_projection_for_100_and_1000_users(self):
        # days=7 (default) -> avg_sessions_per_user = total_requests // (7*10).
        # 140 requests / 70 = exactly 2, keeping the projection hand-computable
        # instead of re-deriving the formula in the test.
        docs = [{"estimatedCost": 0.001} for _ in range(140)]
        with patch.object(metrics, "get_mongo_db", return_value=_db(docs)):
            result = await metrics.get_metrics_summary(42)

        self.assertEqual(
            result["escalamento"]["100_usuarios_semanais"],
            {"weekly_requests": 200, "estimated_cost_usd": 0.2},
        )
        self.assertEqual(
            result["escalamento"]["1000_usuarios_semanais"],
            {"weekly_requests": 2000, "estimated_cost_usd": 2.0},
        )

    async def test_roi_counts_only_completed_motor_preditivo_executions(self):
        with patch.object(metrics, "get_mongo_db", return_value=_db([{"estimatedCost": 0}], alerts_resolved=3)):
            result = await metrics.get_metrics_summary(42)

        self.assertEqual(result["roi"]["acoes_motor_preditivo"], 3)
        self.assertEqual(result["roi"]["economia_estimada_brl"], 45.0)


if __name__ == "__main__":
    unittest.main()
