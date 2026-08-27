#!/usr/bin/env python3
"""Gera embeddings locais para chunks RAG ainda não indexados."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from app.database.mongo import get_mongo_client
from app.rag.retriever import get_embedding_model
from config.settings import get_settings

BATCH_SIZE = 64


async def main() -> None:
    settings = get_settings()
    model = await asyncio.to_thread(get_embedding_model)
    client = get_mongo_client()
    db = client[settings.mongo_db]
    updated = 0

    while True:
        chunks = await db.rag_chunks.find({"embedding": None}, {"text": 1}).to_list(length=BATCH_SIZE)
        if not chunks:
            break
        vectors = await asyncio.to_thread(
            model.encode,
            [chunk["text"] for chunk in chunks],
            normalize_embeddings=True,
        )
        for chunk, vector in zip(chunks, vectors):
            await db.rag_chunks.update_one({"_id": chunk["_id"]}, {"$set": {"embedding": vector.tolist()}})
            updated += 1

    print(f"[embeddings] {updated} chunks indexados com {settings.embedding_model}.")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
