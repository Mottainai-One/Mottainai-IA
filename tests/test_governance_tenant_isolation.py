"""Tenant-isolation contracts for governance records and reports."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.agents.governanca import (
    run_auditoria_execucoes,
    run_controle_acesso,
    run_relatorio_conformidade,
)
from app.observability.metrics import record_execution_metrics


class AsyncCursor:
    def __init__(self, documents):
        self.documents = documents

    def __aiter__(self):
        async def iterate():
            for document in self.documents:
                yield document
        return iterate()

    async def to_list(self, length):
        return self.documents[:length]


class RecordingCollection:
    def __init__(self, *, counts=None, documents=None, aggregate_documents=None, find_one_document=None):
        self.counts = list(counts or [])
        self.documents = list(documents or [])
        self.aggregate_documents = list(aggregate_documents or [])
        self.find_one_document = find_one_document
        self.count_queries = []
        self.find_queries = []
        self.aggregate_pipelines = []
        self.find_one_queries = []
        self.inserted_documents = []

    async def count_documents(self, query):
        self.count_queries.append(query)
        return self.counts.pop(0) if self.counts else 0

    def find(self, query, projection=None):
        self.find_queries.append((query, projection))
        return AsyncCursor(self.documents)

    def aggregate(self, pipeline):
        self.aggregate_pipelines.append(pipeline)
        return AsyncCursor(self.aggregate_documents)

    async def find_one(self, query):
        self.find_one_queries.append(query)
        return self.find_one_document

    async def insert_one(self, document):
        self.inserted_documents.append(document)


def _has_direct_tenant_clause(query, empresa_id):
    return any(clause.get("empresaId") == empresa_id for clause in query.get("$or", []))


class GovernanceTenantIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_execution_audit_filters_every_aggregate_by_company(self):
        conversations = RecordingCollection(documents=[{"_id": "conversation-42", "sessionId": "session-42"}])
        executions = RecordingCollection(
            counts=[4, 1],
            aggregate_documents=[{"_id": "funcionario", "avg_latency": 0.2, "count": 4}],
        )
        evaluations = RecordingCollection(counts=[1])
        database = SimpleNamespace(
            conversations=conversations,
            agent_executions=executions,
            prompt_evaluations=evaluations,
        )

        with patch("app.agents.governanca.get_mongo_db", return_value=database):
            report = await run_auditoria_execucoes(42)

        self.assertEqual(report["total_execucoes"], 4)
        self.assertEqual(report["execucoes_com_erro"], 1)
        self.assertTrue(_has_direct_tenant_clause(executions.count_queries[0], 42))
        self.assertTrue(_has_direct_tenant_clause(executions.count_queries[1], 42))
        self.assertTrue(_has_direct_tenant_clause(executions.aggregate_pipelines[0][0]["$match"], 42))
        self.assertEqual(evaluations.count_queries[0]["empresaId"], 42)
        legacy_clause = executions.count_queries[0]["$or"][1]
        self.assertIn({"conversationId": {"$in": ["conversation-42"]}}, legacy_clause["$and"][1]["$or"])
        self.assertIn({"sessionId": {"$in": ["session-42"]}}, legacy_clause["$and"][1]["$or"])

    async def test_compliance_report_scopes_labeled_and_safely_linked_legacy_records(self):
        conversations = RecordingCollection(
            counts=[2],
            documents=[{"_id": "conversation-42", "sessionId": "session-42"}],
        )
        messages = RecordingCollection(counts=[4])
        feedbacks = RecordingCollection(counts=[3, 1])
        metrics = RecordingCollection(documents=[{"inputTokens": 1_000_000, "outputTokens": 500_000}])
        database = SimpleNamespace(
            conversations=conversations,
            messages=messages,
            feedbacks=feedbacks,
            metrics=metrics,
        )
        settings = SimpleNamespace(
            llm_input_cost_per_million_usd=1.0,
            llm_output_cost_per_million_usd=2.0,
        )

        with patch("app.agents.governanca.get_mongo_db", return_value=database), patch(
            "app.agents.governanca.get_settings", return_value=settings
        ):
            report = await run_relatorio_conformidade(42)

        self.assertEqual(report["total_mensagens"], 4)
        self.assertEqual(report["feedbacks"], {"positive": 3, "negative": 1})
        self.assertEqual(report["tokens"], {"input": 1_000_000, "output": 500_000})
        self.assertEqual(conversations.count_queries[0]["empresaId"], 42)
        for query in [
            messages.count_queries[0],
            feedbacks.count_queries[0],
            feedbacks.count_queries[1],
            metrics.find_queries[0][0],
        ]:
            self.assertTrue(_has_direct_tenant_clause(query, 42))
            legacy_clause = query["$or"][1]
            self.assertEqual(
                legacy_clause["$and"][0],
                {"$or": [{"empresaId": {"$exists": False}}, {"empresaId": 0}]},
            )
            self.assertIn({"conversationId": {"$in": ["conversation-42"]}}, legacy_clause["$and"][1]["$or"])

    async def test_access_control_uses_company_scoped_policy_and_event(self):
        policies = RecordingCollection(find_one_document={
            "_id": "policy-42",
            "scope": {"forbiddenDomains": ["estoque"]},
        })
        events = RecordingCollection()
        database = SimpleNamespace(agent_policies=policies, conversation_events=events)

        with patch("app.agents.governanca.get_mongo_db", return_value=database):
            result = await run_controle_acesso(42, "funcionario", "consultar estoque")

        self.assertTrue(result["violation_detected"])
        self.assertEqual(policies.find_one_queries, [{"agent": "funcionario", "empresaId": 42}])
        self.assertEqual(events.inserted_documents[0]["empresaId"], 42)


class MetricsTenantWriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_metric_without_a_valid_company(self):
        with self.assertRaises(ValueError):
            await record_execution_metrics("s1", None, "cliente", None, "groq/model", 1, 1, 0.1)


if __name__ == "__main__":
    unittest.main()
