"""Tests for RAG document ingestion: chunking and the write path behind
POST /rag/documents."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
from pymongo.errors import DuplicateKeyError

from app.rag.ingestion import DuplicateSlugError, ingest_document, split_into_chunks


class SplitIntoChunksTests(unittest.TestCase):
    def test_splits_on_blank_lines(self):
        text = "Primeiro parágrafo.\n\nSegundo parágrafo."

        chunks = split_into_chunks(text)

        self.assertEqual(chunks, ["Primeiro parágrafo.", "Segundo parágrafo."])

    def test_strips_whitespace_and_drops_empty_paragraphs(self):
        text = "  Um parágrafo.  \n\n\n\nOutro.  "

        chunks = split_into_chunks(text)

        self.assertEqual(chunks, ["Um parágrafo.", "Outro."])

    def test_whitespace_only_text_produces_no_chunks(self):
        self.assertEqual(split_into_chunks("   \n\n   "), [])

    def test_splits_a_long_paragraph_on_sentence_boundaries(self):
        sentence = "Esta é uma frase de teste com bastante texto para forçar a divisão. "
        long_paragraph = sentence * 20  # comfortably over MAX_CHUNK_CHARS

        chunks = split_into_chunks(long_paragraph, max_chars=200)

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 200 + len(sentence))  # one sentence may push slightly over

    def test_short_paragraph_is_not_split(self):
        chunks = split_into_chunks("Uma frase curta.", max_chars=800)

        self.assertEqual(chunks, ["Uma frase curta."])


class FakeInsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class FakeCollection:
    def __init__(self, *, insert_one_error=None):
        self.inserted_docs = []
        self.inserted_many_docs = None
        self._insert_one_error = insert_one_error

    async def insert_one(self, doc):
        if self._insert_one_error:
            raise self._insert_one_error
        self.inserted_docs.append(doc)
        return FakeInsertResult(inserted_id="doc-id-1")

    async def insert_many(self, docs):
        self.inserted_many_docs = docs


class IngestDocumentTests(unittest.IsolatedAsyncioTestCase):
    def _model(self):
        model = MagicMock()
        model.encode.return_value = np.array([[0.1, 0.2], [0.3, 0.4]])
        return model

    async def test_writes_document_and_embedded_chunks(self):
        db = SimpleNamespace(rag_documents=FakeCollection(), rag_chunks=FakeCollection())

        with (
            patch("app.rag.ingestion.get_mongo_db", return_value=db),
            patch("app.rag.ingestion.get_embedding_model", return_value=self._model()),
        ):
            result = await ingest_document(
                empresa_id=1, slug="politica-x", title="Política X",
                source="manual_operacional", text="Parágrafo um.\n\nParágrafo dois.",
            )

        self.assertEqual(result, {"document_id": "doc-id-1", "slug": "politica-x", "chunks": 2})
        self.assertEqual(db.rag_documents.inserted_docs[0]["slug"], "politica-x")
        self.assertEqual(db.rag_documents.inserted_docs[0]["empresaId"], 1)
        chunks_written = db.rag_chunks.inserted_many_docs
        self.assertEqual(len(chunks_written), 2)
        self.assertEqual(chunks_written[0]["embedding"], [0.1, 0.2])
        self.assertEqual(chunks_written[1]["embedding"], [0.3, 0.4])
        self.assertEqual(chunks_written[0]["documentId"], "doc-id-1")

    async def test_raises_duplicate_slug_error_on_unique_index_violation(self):
        db = SimpleNamespace(
            rag_documents=FakeCollection(insert_one_error=DuplicateKeyError("dup")),
            rag_chunks=FakeCollection(),
        )

        with patch("app.rag.ingestion.get_mongo_db", return_value=db):
            with self.assertRaises(DuplicateSlugError):
                await ingest_document(
                    empresa_id=1, slug="ja-existe", title="T", source="faq", text="algo",
                )

    async def test_raises_value_error_for_whitespace_only_text(self):
        db = SimpleNamespace(rag_documents=FakeCollection(), rag_chunks=FakeCollection())

        with patch("app.rag.ingestion.get_mongo_db", return_value=db):
            with self.assertRaises(ValueError):
                await ingest_document(
                    empresa_id=1, slug="vazio", title="T", source="faq", text="   ",
                )

        self.assertEqual(db.rag_documents.inserted_docs, [])  # never touched the DB


if __name__ == "__main__":
    unittest.main()
