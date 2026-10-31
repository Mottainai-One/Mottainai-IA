"""Build a tenant-scoped Neo4j snapshot of chatbot RAG citations."""
from __future__ import annotations

import argparse
import asyncio
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database.mongo import close_mongo_client, get_mongo_db
from config.settings import get_settings

CITATION_PATTERN = re.compile(r"^doc:([0-9a-fA-F]{24})\s+chunk:(\d+)$")
WRITE_BATCH_SIZE = 500


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("empresa-id deve ser um inteiro positivo") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("empresa-id deve ser um inteiro positivo")
    return parsed


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _batches(values: list[dict], size: int = WRITE_BATCH_SIZE):
    for start in range(0, len(values), size):
        yield values[start : start + size]


async def load_tenant_graph_data(empresa_id: int) -> dict[str, Any]:
    """Load only graph metadata for one tenant; never load message or chunk text."""
    db = get_mongo_db()
    conversations = [
        document
        async for document in db.conversations.find(
            {"empresaId": empresa_id},
            {"_id": 1, "status": 1, "agent": 1, "startedAt": 1},
        )
    ]
    conversation_ids = [document["_id"] for document in conversations]

    messages = []
    if conversation_ids:
        messages = [
            document
            async for document in db.messages.find(
                {"empresaId": empresa_id, "conversationId": {"$in": conversation_ids}},
                {"_id": 1, "conversationId": 1, "role": 1, "agent": 1, "skill": 1,
                 "createdAt": 1, "sources": 1},
            )
        ]

    documents = [
        document
        async for document in db.rag_documents.find(
            {"empresaId": empresa_id},
            {"_id": 1, "slug": 1, "title": 1, "source": 1, "category": 1, "version": 1},
        )
    ]
    document_ids = [document["_id"] for document in documents]
    chunks = []
    if document_ids:
        chunks = [
            chunk
            async for chunk in db.rag_chunks.find(
                {"documentId": {"$in": document_ids}},
                {"_id": 0, "documentId": 1, "chunk": 1},
            )
        ]

    return {
        "conversations": conversations,
        "messages": messages,
        "documents": documents,
        "chunks": chunks,
    }


def build_graph_rows(empresa_id: int, data: dict[str, Any]) -> dict[str, Any]:
    """Normalize Mongo IDs and link valid RAG citations to tenant-owned chunks."""
    conversation_by_id = {str(item["_id"]): item for item in data["conversations"]}
    document_by_id = {str(item["_id"]): item for item in data["documents"]}

    conversations = [
        {
            "key": f"{empresa_id}:{conversation_id}",
            "mongo_id": conversation_id,
            "status": conversation.get("status"),
            "agent": conversation.get("agent"),
            "started_at": _iso(conversation.get("startedAt")),
        }
        for conversation_id, conversation in conversation_by_id.items()
    ]

    messages = []
    valid_message_ids: set[str] = set()
    for message in data["messages"]:
        conversation_id = str(message.get("conversationId", ""))
        if conversation_id not in conversation_by_id:
            continue
        message_id = str(message["_id"])
        valid_message_ids.add(message_id)
        messages.append({
            "key": f"{empresa_id}:{message_id}",
            "conversation_key": f"{empresa_id}:{conversation_id}",
            "role": message.get("role", ""),
            "agent": message.get("agent"),
            "skill": message.get("skill"),
            "created_at": _iso(message.get("createdAt")),
        })

    chunk_rows = []
    available_chunks: set[tuple[str, int]] = set()
    for chunk in data["chunks"]:
        document_id = str(chunk.get("documentId", ""))
        number = chunk.get("chunk")
        if document_id not in document_by_id or isinstance(number, bool) or not isinstance(number, int):
            continue
        available_chunks.add((document_id, number))
        chunk_rows.append({
            "key": f"{empresa_id}:{document_id}:{number}",
            "document_key": f"{empresa_id}:{document_id}",
            "number": number,
        })

    citations = []
    skipped_citations = 0
    for message in data["messages"]:
        message_id = str(message.get("_id", ""))
        if message.get("role") != "assistant" or message_id not in valid_message_ids:
            continue
        message_key = f"{empresa_id}:{message_id}"
        for source in message.get("sources") or []:
            if not isinstance(source, dict) or source.get("type") != "rag":
                continue
            match = CITATION_PATTERN.fullmatch(str(source.get("ref", "")))
            if not match:
                skipped_citations += 1
                continue
            document_id, chunk_number = match.group(1).lower(), int(match.group(2))
            if document_id not in document_by_id or (document_id, chunk_number) not in available_chunks:
                skipped_citations += 1
                continue
            score = source.get("score")
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                score = None
            citations.append({
                "message_key": message_key,
                "chunk_key": f"{empresa_id}:{document_id}:{chunk_number}",
                "score": float(score) if score is not None else None,
            })

    documents = [
        {
            "key": f"{empresa_id}:{document['_id']}",
            "mongo_id": str(document["_id"]),
            "slug": document.get("slug"),
            "title": document.get("title"),
            "source": document.get("source"),
            "category": document.get("category"),
            "version": document.get("version"),
        }
        for document in data["documents"]
    ]

    return {
        "empresa_id": empresa_id,
        "conversations": conversations,
        "messages": messages,
        "documents": documents,
        "chunks": chunk_rows,
        "citations": citations,
        "skipped_citations": skipped_citations,
    }


def _write_batch(tx: Any, query: str, rows: list[dict], **parameters: Any) -> None:
    for batch in _batches(rows):
        tx.run(query, rows=batch, **parameters).consume()


def replace_tenant_graph(tx: Any, graph: dict[str, Any]) -> dict[str, int]:
    """Atomically replace one company's graph, retaining every other tenant."""
    empresa_id = graph["empresa_id"]
    tx.run(
        """MATCH (node)
           WHERE (node:Conversation OR node:Message OR node:RagDocument OR node:RagChunk)
             AND node.key STARTS WITH $tenant_prefix
           DETACH DELETE node""",
        tenant_prefix=f"{empresa_id}:",
    ).consume()
    tx.run(
        "MATCH (company:Company {empresaId: $empresa_id}) DETACH DELETE company",
        empresa_id=empresa_id,
    ).consume()
    tx.run("CREATE (:Company {empresaId: $empresa_id})", empresa_id=empresa_id).consume()

    _write_batch(tx, """
        UNWIND $rows AS row
        MATCH (company:Company {empresaId: $empresa_id})
        CREATE (conversation:Conversation {
            key: row.key, mongoId: row.mongo_id, status: row.status,
            agent: row.agent, startedAt: row.started_at
        })
        CREATE (company)-[:HAS_CONVERSATION]->(conversation)
    """, graph["conversations"], empresa_id=empresa_id)

    _write_batch(tx, """
        UNWIND $rows AS row
        MATCH (conversation:Conversation {key: row.conversation_key})
        CREATE (message:Message {
            key: row.key, role: row.role, agent: row.agent,
            skill: row.skill, createdAt: row.created_at
        })
        CREATE (conversation)-[:HAS_MESSAGE]->(message)
    """, graph["messages"])

    _write_batch(tx, """
        UNWIND $rows AS row
        CREATE (:RagDocument {
            key: row.key, mongoId: row.mongo_id, slug: row.slug,
            title: row.title, source: row.source, category: row.category,
            version: row.version
        })
    """, graph["documents"])

    _write_batch(tx, """
        UNWIND $rows AS row
        MATCH (document:RagDocument {key: row.document_key})
        CREATE (chunk:RagChunk {key: row.key, number: row.number})
        CREATE (chunk)-[:PART_OF]->(document)
    """, graph["chunks"])

    _write_batch(tx, """
        UNWIND $rows AS row
        MATCH (message:Message {key: row.message_key})
        MATCH (chunk:RagChunk {key: row.chunk_key})
        CREATE (message)-[:CITES {score: row.score}]->(chunk)
    """, graph["citations"])

    return {
        "conversations": len(graph["conversations"]),
        "messages": len(graph["messages"]),
        "documents": len(graph["documents"]),
        "chunks": len(graph["chunks"]),
        "citations": len(graph["citations"]),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--empresa-id", required=True, type=_positive_int)
    args = parser.parse_args()
    settings = get_settings()
    password = settings.neo4j_password.strip()
    if len(password) < 8 or password.upper().startswith("CHANGE_ME"):
        parser.error("Configure NEO4J_PASSWORD com uma senha local própria (mínimo de 8 caracteres).")

    try:
        data = await load_tenant_graph_data(args.empresa_id)
    finally:
        await close_mongo_client()

    graph = build_graph_rows(args.empresa_id, data)
    with GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, password),
    ) as driver:
        driver.verify_connectivity()
        with driver.session(database=settings.neo4j_database) as session:
            counts = session.execute_write(replace_tenant_graph, graph)

    print(
        f"Grafo atualizado para empresa {args.empresa_id}: "
        f"{counts['conversations']} conversas, {counts['messages']} mensagens, "
        f"{counts['documents']} documentos, {counts['chunks']} trechos e "
        f"{counts['citations']} citações RAG. "
        f"Citações inválidas ou sem trecho correspondente ignoradas: {graph['skipped_citations']}."
    )


if __name__ == "__main__":
    asyncio.run(main())
