"""Vectorized RAG scoring: batch_cosine_similarity must match the original
per-vector cosine_similarity exactly, just computed in one matrix op."""
import math
import unittest
from unittest.mock import patch

from app.rag.retriever import batch_cosine_similarity, cosine_similarity, retrieve


class FakeCollection:
    def __init__(self, documents):
        self.documents = documents

    def find(self, query, projection=None):
        documents = self.documents

        class Cursor:
            def __aiter__(self):
                async def iterate():
                    for document in documents:
                        yield document
                return iterate()
        return Cursor()


class BatchCosineSimilarityTests(unittest.TestCase):
    def test_matches_the_per_vector_function_for_each_embedding(self):
        query = [0.6, 0.8]
        embeddings = [[1.0, 0.0], [0.0, 1.0], [0.6, 0.8], [-0.6, -0.8]]

        batch = batch_cosine_similarity(query, embeddings)

        for score, embedding in zip(batch, embeddings):
            self.assertAlmostEqual(score, cosine_similarity(query, embedding), places=10)

    def test_empty_embeddings_returns_empty_list(self):
        self.assertEqual(batch_cosine_similarity([1.0, 0.0], []), [])

    def test_zero_vector_embedding_scores_zero_instead_of_dividing_by_zero(self):
        scores = batch_cosine_similarity([1.0, 0.0], [[0.0, 0.0], [1.0, 0.0]])

        self.assertEqual(scores[0], 0.0)
        self.assertAlmostEqual(scores[1], 1.0, places=10)

    def test_zero_query_vector_scores_everything_zero(self):
        scores = batch_cosine_similarity([0.0, 0.0], [[1.0, 0.0], [0.0, 1.0]])

        self.assertEqual(scores, [0.0, 0.0])

    def test_result_is_not_nan(self):
        scores = batch_cosine_similarity([0.0, 0.0], [[0.0, 0.0]])

        self.assertFalse(any(math.isnan(s) for s in scores))


class RetrieveWithMultipleChunksTests(unittest.IsolatedAsyncioTestCase):
    async def test_filters_by_min_score_sorts_by_score_and_respects_top_k(self):
        class Database:
            rag_documents = FakeCollection([{"_id": "doc-1"}])
            rag_chunks = FakeCollection([
                {"documentId": "doc-1", "text": "irrelevante", "embedding": [0.0, 1.0], "chunk": 0},
                {"documentId": "doc-1", "text": "melhor match", "embedding": [1.0, 0.0], "chunk": 1},
                {"documentId": "doc-1", "text": "match parcial", "embedding": [0.9, 0.1], "chunk": 2},
            ])

        with (
            patch("app.rag.retriever.get_mongo_db", return_value=Database()),
            patch("app.rag.retriever.embed", return_value=[1.0, 0.0]),
        ):
            results = await retrieve("consulta", empresa_id=1, top_k=1, min_score=0.4)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["text"], "melhor match")


if __name__ == "__main__":
    unittest.main()
