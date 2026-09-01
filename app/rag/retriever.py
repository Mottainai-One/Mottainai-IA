"""
RAG — Retriever: semantic search over rag_chunks (MongoDB).
Uses local embeddings (sentence-transformers) — zero API cost.
Returns relevant chunks with a cosine similarity score.

Note: the context strings built for the LLM prompt (e.g. "Nenhum documento
relevante...") are deliberately kept in Portuguese, like the agents'
SYSTEM_PROMPT.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from functools import lru_cache
from typing import Optional

import numpy as np
from sentence_transformers import SentenceTransformer

from app.cache.keyspace import rag_result
from app.config import get_settings
from app.database.mongo import get_mongo_db
from app.database.redis_client import get_redis

settings = get_settings()
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    """Loads the local embedding model once (singleton)."""
    # The model is prepared beforehand by scripts/generate_embeddings.py and
    # cached on the host. Blocking the download fallback keeps a RAG query
    # from depending on the network or failing on a corporate certificate.
    return SentenceTransformer(settings.embedding_model, local_files_only=True)


def embed(text: str) -> list[float]:
    """Generates the embedding for a text."""
    model = get_embedding_model()
    vec = model.encode(text, normalize_embeddings=True)
    return vec.tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    va, vb = np.array(a), np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def batch_cosine_similarity(query_vec: list[float], embeddings: list[list[float]]) -> list[float]:
    """
    Cosine similarity between one query vector and many chunk embeddings at
    once, via a single matrix-vector product instead of a Python loop
    calling cosine_similarity() per chunk (which also redundantly
    recomputed the query vector's own norm on every single iteration).
    Same math as cosine_similarity(), batched — not an approximation.
    """
    if not embeddings:
        return []

    query = np.asarray(query_vec, dtype=np.float64)
    query_norm = np.linalg.norm(query)
    matrix = np.asarray(embeddings, dtype=np.float64)  # shape (n_chunks, dim)
    chunk_norms = np.linalg.norm(matrix, axis=1)

    denom = chunk_norms * query_norm
    numerator = matrix @ query
    scores = np.divide(numerator, denom, out=np.zeros_like(numerator), where=denom != 0)
    return scores.tolist()


async def retrieve(
    query: str,
    empresa_id: int,
    top_k: int = 5,
    min_score: float = 0.4,
) -> list[dict]:
    """
    Fetches the `top_k` chunks most relevant to the query.

    Strategy: local embeddings + brute-force search in MongoDB.
    (In a real production system, we'd use Atlas Vector Search or pgvector.)

    Returns a list of dicts with: text, score, documentId, chunk, metadata.
    """
    query_vec = await asyncio.to_thread(embed, query)

    db = get_mongo_db()

    # Loads the company's document IDs
    doc_ids = [
        doc["_id"]
        async for doc in db.rag_documents.find(
            {"empresaId": empresa_id}, {"_id": 1}
        )
    ]

    if not doc_ids:
        return []

    # Loads chunks that have an embedding
    chunks = []
    async for chunk in db.rag_chunks.find(
        {"documentId": {"$in": doc_ids}, "embedding": {"$ne": None}}
    ):
        chunks.append(chunk)

    if not chunks:
        return []

    # Computes all scores in one batched matrix operation instead of a
    # per-chunk Python loop (see batch_cosine_similarity's docstring).
    scores = batch_cosine_similarity(query_vec, [chunk["embedding"] for chunk in chunks])

    scored = [
        {
            "text": chunk["text"],
            "score": round(score, 4),
            "documentId": str(chunk["documentId"]),
            "chunk": chunk["chunk"],
            "metadata": chunk.get("metadata", {}),
        }
        for chunk, score in zip(chunks, scores)
        if score >= min_score
    ]

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


# Retrieved chunks are joined by a plain rule instead of the numbered
# "[Fonte N — score X]" header they used to carry. That header was the only
# citable handle in the prompt, and the model was observed echoing it back
# into a user-facing answer ("(ver Fonte 3)") — which every agent's
# SYSTEM_PROMPT forbids, since it reveals how the assistant works behind the
# scenes. A label that does not exist cannot be quoted. The separator is kept
# so chunk boundaries stay visible and unrelated facts are not read as one
# passage. Traceability is unaffected: the `sources` list below still carries
# documentId, chunk and score, and that list — not this string — is what is
# written to messages.sources and what the Judge grades grounding against
# (app/agents/juiz.py).
CHUNK_SEPARATOR = "\n\n---\n\n"


def _rag_cache_key(query: str, empresa_id: int, top_k: int) -> str:
    normalized = f"{query.strip().lower()}::{top_k}"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return rag_result(empresa_id, digest)


async def _read_rag_cache(cache_key: str) -> Optional[tuple[str, list[dict]]]:
    """Reads the RAG result from cache. Fail-open: Redis error = cache miss."""
    try:
        cached = await get_redis().get(cache_key)
    except Exception:
        logger.warning("RAG cache unavailable (read) — continuing without cache", exc_info=True)
        return None
    if not cached:
        return None
    payload = json.loads(cached)
    return payload["context"], payload["sources"]


async def _write_rag_cache(cache_key: str, context: str, sources: list[dict]) -> None:
    """Writes the RAG result to cache. Fail-open: a Redis error does not affect the response."""
    try:
        payload = json.dumps({"context": context, "sources": sources})
        await get_redis().set(cache_key, payload, ex=settings.rag_cache_ttl_seconds)
    except Exception:
        logger.warning("RAG cache unavailable (write) — continuing without cache", exc_info=True)


async def retrieve_with_sources(
    query: str,
    empresa_id: int,
    top_k: int = 5,
) -> tuple[str, list[dict]]:
    """
    Returns (formatted_context, sources) to inject into the prompt.
    Sources are written to messages.sources for traceability.

    Uses a Redis cache (configurable TTL) for the same question within the
    same company — reduces latency and the cost of recomputing
    embeddings/similarity. The cache is purely an optimization: if Redis is
    down, RAG keeps working normally, just without the speed gain.
    """
    cache_key = _rag_cache_key(query, empresa_id, top_k)
    cached = await _read_rag_cache(cache_key)
    if cached is not None:
        return cached

    results = await retrieve(query, empresa_id, top_k)
    if not results:
        context, sources = "Nenhum documento relevante encontrado na base de conhecimento.", []
        await _write_rag_cache(cache_key, context, sources)
        return context, sources

    context_parts = []
    sources = []
    for r in results:
        context_parts.append(r["text"])
        sources.append(
            {
                "type": "rag",
                "ref": f"doc:{r['documentId']} chunk:{r['chunk']}",
                "score": r["score"],
            }
        )

    context = CHUNK_SEPARATOR.join(context_parts)
    await _write_rag_cache(cache_key, context, sources)
    return context, sources
