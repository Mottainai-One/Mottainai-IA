"""
RAG — Retriever: busca semântica em rag_chunks (MongoDB).
Usa embeddings locais (sentence-transformers) — zero custo de API.
Retorna chunks relevantes com score de similaridade cossenoidal.
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
    """Carrega o modelo de embeddings local uma vez (singleton)."""
    # O modelo é preparado previamente por scripts/generate_embeddings.py e
    # fica em cache no host. Impedir o fallback de download evita que uma
    # consulta RAG dependa da rede ou falhe por certificado corporativo.
    return SentenceTransformer(settings.embedding_model, local_files_only=True)


def embed(text: str) -> list[float]:
    """Gera embedding de um texto."""
    model = get_embedding_model()
    vec = model.encode(text, normalize_embeddings=True)
    return vec.tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Similaridade cossenoidal entre dois vetores."""
    va, vb = np.array(a), np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


async def retrieve(
    query: str,
    empresa_id: int,
    top_k: int = 5,
    min_score: float = 0.4,
) -> list[dict]:
    """
    Busca os `top_k` chunks mais relevantes para a query.

    Estratégia: embeddings locais + busca bruta em MongoDB.
    (Em produção real, usaríamos Atlas Vector Search ou pgvector.)

    Retorna lista de dicts com: text, score, documentId, chunk, metadata.
    """
    query_vec = await asyncio.to_thread(embed, query)

    db = get_mongo_db()

    # Carrega IDs dos documentos da empresa
    doc_ids = [
        doc["_id"]
        async for doc in db.rag_documents.find(
            {"empresaId": empresa_id}, {"_id": 1}
        )
    ]

    if not doc_ids:
        return []

    # Carrega chunks com embedding
    chunks = []
    async for chunk in db.rag_chunks.find(
        {"documentId": {"$in": doc_ids}, "embedding": {"$ne": None}}
    ):
        chunks.append(chunk)

    if not chunks:
        return []

    # Calcula scores
    scored = []
    for chunk in chunks:
        score = cosine_similarity(query_vec, chunk["embedding"])
        if score >= min_score:
            scored.append(
                {
                    "text": chunk["text"],
                    "score": round(score, 4),
                    "documentId": str(chunk["documentId"]),
                    "chunk": chunk["chunk"],
                    "metadata": chunk.get("metadata", {}),
                }
            )

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def _rag_cache_key(query: str, empresa_id: int, top_k: int) -> str:
    normalized = f"{query.strip().lower()}::{top_k}"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return rag_result(empresa_id, digest)


async def _read_rag_cache(cache_key: str) -> Optional[tuple[str, list[dict]]]:
    """Lê o resultado do RAG do cache. Falha aberta: erro no Redis = cache miss."""
    try:
        cached = await get_redis().get(cache_key)
    except Exception:
        logger.warning("Cache RAG indisponível (leitura) — seguindo sem cache", exc_info=True)
        return None
    if not cached:
        return None
    payload = json.loads(cached)
    return payload["context"], payload["sources"]


async def _write_rag_cache(cache_key: str, context: str, sources: list[dict]) -> None:
    """Grava o resultado do RAG no cache. Falha aberta: erro no Redis não afeta a resposta."""
    try:
        payload = json.dumps({"context": context, "sources": sources})
        await get_redis().set(cache_key, payload, ex=settings.rag_cache_ttl_seconds)
    except Exception:
        logger.warning("Cache RAG indisponível (escrita) — seguindo sem cache", exc_info=True)


async def retrieve_with_sources(
    query: str,
    empresa_id: int,
    top_k: int = 5,
) -> tuple[str, list[dict]]:
    """
    Retorna (contexto_formatado, fontes) para injetar no prompt.
    As fontes são gravadas em messages.sources para rastreabilidade.

    Usa cache no Redis (TTL configurável) para a mesma pergunta na mesma
    empresa — reduz latência e custo de recomputar embeddings/similaridade.
    O cache é puramente uma otimização: se o Redis estiver fora do ar, o RAG
    segue funcionando normalmente, só sem o ganho de velocidade.
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
    for i, r in enumerate(results, 1):
        context_parts.append(f"[Fonte {i} — score {r['score']}]\n{r['text']}")
        sources.append(
            {
                "type": "rag",
                "ref": f"doc:{r['documentId']} chunk:{r['chunk']}",
                "score": r["score"],
            }
        )

    context = "\n\n".join(context_parts)
    await _write_rag_cache(cache_key, context, sources)
    return context, sources
