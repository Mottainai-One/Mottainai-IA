"""Turns raw text into embedded RAG chunks.

Before this, new knowledge-base content could only enter the system by
hand-editing scripts/setup_mongo.py (the seed script) or running
scripts/generate_embeddings.py to backfill embeddings afterward — there
was no way to add a document without touching the codebase. This module
is what POST /rag/documents (interfaces/api/main.py) calls: it chunks the
text and computes every chunk's embedding immediately, so a newly
uploaded document is searchable right away, not after a separate batch
job happens to run.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from app.database.mongo import get_mongo_db
from app.rag.retriever import get_embedding_model

MAX_CHUNK_CHARS = 800

# rag_documents' $jsonSchema requires a `category` from this enum and a
# `version` string. The upload endpoint takes a free-text `source`
# ("faq", "manual_operacional", ...), so map it onto the enum the
# collection actually accepts instead of writing a document Mongo rejects.
CATEGORIES = ("MANUAL", "FAQ", "PROCEDIMENTO", "TREINAMENTO", "POLITICA")
DEFAULT_CATEGORY = "MANUAL"
DEFAULT_VERSION = "1.0"


def category_for_source(source: str) -> str:
    """Best-effort mapping of the free-text `source` onto rag_documents.category."""
    normalized = source.strip().upper()
    if normalized in CATEGORIES:
        return normalized
    if normalized.startswith("FAQ"):
        return "FAQ"
    if normalized.startswith("PROCEDIMENTO"):
        return "PROCEDIMENTO"
    if normalized.startswith("TREINAMENTO"):
        return "TREINAMENTO"
    if normalized.startswith("POLITICA"):
        return "POLITICA"
    return DEFAULT_CATEGORY


class DuplicateSlugError(ValueError):
    """Raised when rag_documents already has this slug (unique index)."""


def split_into_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """
    Splits on blank lines (paragraphs) first; any paragraph still longer
    than max_chars is further split on sentence boundaries. Deliberately
    simple — no external tokenizer/library — matching the granularity of
    the hand-written seed chunks in scripts/setup_mongo.py (short
    paragraphs/sentences, not whole documents).
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            chunks.append(paragraph)
            continue

        sentences = [s.strip() for s in paragraph.replace("\n", " ").split(". ") if s.strip()]
        current = ""
        for sentence in sentences:
            piece = sentence if sentence.endswith((".", "!", "?")) else f"{sentence}."
            candidate = f"{current} {piece}".strip() if current else piece
            if len(candidate) > max_chars and current:
                chunks.append(current)
                current = piece
            else:
                current = candidate
        if current:
            chunks.append(current)
    return chunks


async def ingest_document(
    empresa_id: int,
    slug: str,
    title: str,
    source: str,
    text: str,
    category: str | None = None,
    version: str = DEFAULT_VERSION,
) -> dict:
    """
    Chunks `text`, embeds every chunk immediately (batched, same pattern as
    scripts/generate_embeddings.py), and writes rag_documents + rag_chunks.

    `category` and `version` are required by the collection's $jsonSchema;
    when no category is given it is derived from `source`.

    Raises DuplicateSlugError if `slug` already exists — rag_documents.slug
    has a unique index (scripts/setup_mongo.py); relies on that DB-level
    constraint rather than a check-then-insert, which would race under
    concurrent uploads. Raises plain ValueError if `text` produces no
    chunks (e.g. whitespace-only) or if `category` is outside the enum.
    """
    chunks_text = split_into_chunks(text)
    if not chunks_text:
        raise ValueError("O texto não produziu nenhum trecho após a divisão em chunks.")

    category = category_for_source(source) if category is None else category.strip().upper()
    if category not in CATEGORIES:
        raise ValueError(f"category deve ser um de {CATEGORIES}, recebido '{category}'.")

    db = get_mongo_db()
    now = datetime.now(timezone.utc)

    try:
        doc_result = await db.rag_documents.insert_one({
            "slug": slug,
            "empresaId": empresa_id,
            "title": title,
            "source": source,
            "category": category,
            "version": version,
            "createdAt": now,
        })
    except DuplicateKeyError as exc:
        raise DuplicateSlugError(f"Já existe um documento com o slug '{slug}'.") from exc

    model = await asyncio.to_thread(get_embedding_model)
    vectors = await asyncio.to_thread(model.encode, chunks_text, normalize_embeddings=True)

    await db.rag_chunks.insert_many([
        {
            "documentId": doc_result.inserted_id,
            "chunk": i,
            "text": chunk_text,
            "embedding": vector.tolist(),
            "createdAt": now,
        }
        for i, (chunk_text, vector) in enumerate(zip(chunks_text, vectors))
    ])

    return {
        "document_id": str(doc_result.inserted_id),
        "slug": slug,
        "chunks": len(chunks_text),
    }
