# Neo4j graph for RAG citation traversal

## Purpose

Provide a reproducible Neo4j graph for the database course requirement and answer a business question that requires following graph relationships. This is an offline projection for analysis and demonstration; the chatbot runtime does not depend on Neo4j.

## Source data and boundaries

- MongoDB remains authoritative for conversations, assistant messages, RAG documents, and chunks.
- Redis remains a disposable cache and is not read by the importer.
- The importer only reads one explicitly selected `empresaId` from MongoDB.
- Message text, user IDs, embeddings, and chunk text are excluded from Neo4j. Only conversation/message metadata and RAG citation metadata are copied.
- Only assistant messages and `sources` entries with `type == "rag"` become citation relationships. SQL and other source types are ignored.

## Graph model

```text
(Company)-[:HAS_CONVERSATION]->(Conversation)
(Conversation)-[:HAS_MESSAGE]->(Message)
(Message)-[:CITES]->(RagChunk)-[:PART_OF]->(RagDocument)
```

Each import replaces the selected company's graph snapshot in a single Neo4j transaction, keeping repeated imports idempotent and preventing stale citations after MongoDB records are removed. Other companies are untouched.

## Business question

**Which RAG documents are cited most often in answers produced by the FAQ agent for this company?**

```cypher
MATCH (company:Company {empresaId: $empresa_id})
      -[:HAS_CONVERSATION]->(:Conversation)
      -[:HAS_MESSAGE]->(answer:Message {role: "assistant", agent: "faq"})
      -[:CITES]->(:RagChunk)-[:PART_OF]->(document:RagDocument)
RETURN document.title AS fonte, count(*) AS citacoes
ORDER BY citacoes DESC, fonte
LIMIT 10;
```

This is a traversal across company, conversation, answer, citation, chunk, and document relationships. The importer normalizes the existing Mongo citation reference format `doc:<ObjectId> chunk:<number>` and only links a citation when its document and chunk belong to the selected company.

## Run locally

1. Set `NEO4J_PASSWORD` in `.env` to a unique password of at least 8 characters. On Windows, put the same value in `.env.windows` for Docker Compose.
2. Start Neo4j with `docker compose --env-file .env -f docker-compose.neo4j.yml up -d` (use `--env-file .env.windows` on Windows).
3. Run `python scripts/sync_neo4j_rag.py --empresa-id <ID>` from the project root. It reuses the API's `MONGO_URI` and `MONGO_DB` settings from `.env`; for authenticated Windows MongoDB, configure the same `MONGO_URI` the API uses.
4. Open Neo4j Browser at `http://localhost:7474`, authenticate with `neo4j` and `NEO4J_PASSWORD`, set the query parameter with `:param empresa_id => <ID>`, then run the Cypher query above.

The Neo4j volume stores its initialized credentials. Changing `NEO4J_PASSWORD` after the first start does not reset the database password; keep the original value or change the password inside Neo4j.

No MongoDB, Redis, or other business API changes are required.
