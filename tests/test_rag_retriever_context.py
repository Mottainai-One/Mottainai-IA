"""Tests for the RAG context string that retrieve_with_sources() injects into
the agents' prompt.

Regression guard: each retrieved chunk used to be prefixed with a numbered
"[Fonte N - score X]" header. That header was the only citable handle in the
whole prompt, and the model was observed quoting it back in a user-facing
answer ("(ver Fonte 3)") - which every agent's SYSTEM_PROMPT forbids, since it
reveals how the assistant works behind the scenes. These tests pin the two
halves of the fix: the context must stay unlabelled, and the traceability that
label was never responsible for must survive in `sources`.
"""
import re
import unittest
from unittest.mock import AsyncMock, patch

from app.rag.retriever import CHUNK_SEPARATOR, retrieve_with_sources

RESULTS = [
    {"text": "Os pontos expiram em 12 meses.", "score": 0.67,
     "documentId": "doc-a", "chunk": 0, "metadata": {}},
    {"text": "Cada real gasto vale um ponto.", "score": 0.61,
     "documentId": "doc-b", "chunk": 3, "metadata": {}},
]


class RetrieveWithSourcesContextTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, results):
        """Calls retrieve_with_sources with retrieval stubbed and the cache bypassed."""
        with (
            patch("app.rag.retriever.retrieve", new=AsyncMock(return_value=results)),
            patch("app.rag.retriever._read_rag_cache", new=AsyncMock(return_value=None)),
            patch("app.rag.retriever._write_rag_cache", new=AsyncMock()) as write_cache,
        ):
            context, sources = await retrieve_with_sources("pergunta", empresa_id=1)
        return context, sources, write_cache

    async def test_context_carries_only_the_chunk_text(self):
        context, _, _ = await self._run(RESULTS)

        self.assertEqual(
            context,
            f"Os pontos expiram em 12 meses.{CHUNK_SEPARATOR}Cada real gasto vale um ponto.",
        )

    async def test_context_exposes_no_citable_source_label(self):
        context, _, _ = await self._run(RESULTS)

        # The exact string the model echoed, plus the generic shape of any
        # numbered label that could replace it.
        self.assertNotIn("Fonte", context)
        self.assertIsNone(re.search(r"\[\s*\w+\s+\d+", context))

    async def test_context_exposes_no_similarity_score(self):
        # The score is an internal retrieval detail with no use to the agent,
        # and one more number it could read back to the user.
        context, _, _ = await self._run(RESULTS)

        for result in RESULTS:
            self.assertNotIn(str(result["score"]), context)

    async def test_separator_keeps_chunk_boundaries_visible(self):
        # Without the removed header the chunks must still not run together,
        # or unrelated passages read as one continuous statement.
        context, _, _ = await self._run(RESULTS)

        self.assertEqual(len(context.split(CHUNK_SEPARATOR)), len(RESULTS))

    async def test_sources_still_carry_full_traceability(self):
        # Dropping the label costs nothing here: this list - not the context
        # string - is what reaches messages.sources and the Judge.
        _, sources, _ = await self._run(RESULTS)

        self.assertEqual(sources, [
            {"type": "rag", "ref": "doc:doc-a chunk:0", "score": 0.67},
            {"type": "rag", "ref": "doc:doc-b chunk:3", "score": 0.61},
        ])

    async def test_no_results_returns_the_fallback_message_and_no_sources(self):
        context, sources, write_cache = await self._run([])

        self.assertEqual(
            context, "Nenhum documento relevante encontrado na base de conhecimento."
        )
        self.assertEqual(sources, [])
        write_cache.assert_awaited_once()  # the empty result is cached too


if __name__ == "__main__":
    unittest.main()
