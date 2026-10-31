# Neo4j RAG Citation Traversal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a repeatable Neo4j snapshot of MongoDB conversations and RAG citations that answers which documents the FAQ agent cites most often for one company.

**Architecture:** A standalone CLI reads tenant-scoped MongoDB documents and messages, excludes message content and embeddings, then transactionally replaces only that company's Neo4j projection. Neo4j is an optional local service and is not added to the chatbot request path.

**Tech Stack:** Python 3.13, Motor, Neo4j Python Driver 6.x, Docker Compose, Cypher.

**Spec:** `docs/NEO4J_RAG_GRAPH.md`

## Global Constraints

- MongoDB remains the source of truth for conversation and RAG data.
- Redis remains cache infrastructure and is not queried by the importer.
- All imported records are restricted to the explicitly requested `empresaId`.
- Do not copy message text, user IDs, embeddings, or chunk text into Neo4j.
- The chatbot must continue operating when Neo4j is unavailable because it is not in the runtime path.

## Review Focus

- Malformed or non-RAG citation references are ignored and cannot attach to another tenant's document.
- A repeated import replaces only the requested tenant and leaves other tenants intact.
- Neo4j credentials are required only when the graph service or importer is used; the importer reuses the project's existing MongoDB configuration.
- Messages without an FAQ agent or RAG citation do not inflate the business answer.
- Empty companies produce an empty, valid tenant graph rather than stale previous results.

---

### Task 1: Add optional Neo4j runtime configuration

**Files:**
- Modify: `config/settings.py`
- Modify: `.env.example`
- Modify: `.env.windows.example`
- Modify: `requirements.txt`
- Create: `docker-compose.neo4j.yml`

**Interfaces:**
- Produces `neo4j_uri`, `neo4j_user`, `neo4j_password`, `neo4j_database` settings and an optional standalone Neo4j service on Bolt port 7687 / Browser port 7474.

- [x] Add the pinned official Neo4j Python driver and typed environment settings.
- [x] Add a persisted Community Neo4j service to its own Compose file, without making the API container depend on it.
- [x] Document a non-secret password placeholder in `.env.example`.

### Task 2: Add tenant-scoped Mongo-to-Neo4j import and traversal docs

**Files:**
- Create: `scripts/sync_neo4j_rag.py`
- Create: `docs/NEO4J_RAG_GRAPH.md`

**Interfaces:**
- CLI: `python scripts/sync_neo4j_rag.py --empresa-id <positive-int>`.
- Produces `Company`, `Conversation`, `Message`, `RagChunk`, and `RagDocument` nodes and the relationships defined by the spec.

- [x] Load only the selected tenant's conversations, message metadata, RAG documents, and chunk metadata from MongoDB.
- [x] Add a citation relationship only for assistant messages.
- [x] Parse citations strictly, link only tenant-owned document/chunk pairs, and exclude content, IDs of users, and embeddings.
- [x] Replace the selected tenant snapshot atomically in Neo4j; preserve all other tenant subgraphs.
- [x] Include the executable Cypher traversal and setup steps in `docs/NEO4J_RAG_GRAPH.md`.

### Task 3: Review and publish

**Files:**
- Review the files above; no other database API repository is changed.

- [x] Inspect the final diff for tenant scoping, credential exposure, and accidental changes to existing work.
- [x] Run syntax/static checks without requiring live MongoDB or Neo4j.
- [x] Commit with a Conventional Commit, push `feat/neo4j-rag-traversal`, and open a ready-for-review PR against `main` using the repository template.
