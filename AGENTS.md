# Mottainai IA Layer — Project Conventions

## Structure

- `interfaces/api/`: FastAPI HTTP layer. Keep agent rules and database access out of routes, except for health checks.
- `app/agents/`: LangGraph nodes and domain agents.
- `app/tools/`: domain integrations with PostgreSQL, Redis, vision, and MCP.
- `app/memory/`: sessions, history, and long-term memory in MongoDB.
- `app/rag/`: knowledge retrieval and external sources.
- `app/guardrails/`: deterministic input and output controls.
- `app/observability/`: metrics, errors, cost, and technical audit trails.
- `config/settings.py`: the single source of environment settings. `app/config.py` is a legacy compatibility layer.
- `tests/`: mirrors application responsibilities; tests must not depend on live LLMs, networks, or databases.

## Language convention

- Use English for new Python identifiers, comments, docstrings, tests, and internal architecture documentation.
- Keep system prompts, model-facing context, and end-user messages in Portuguese: the product serves Portuguese-speaking users, and changing prompt wording can change behavior.
- Keep existing API routes, domain agent names, role values, database keys, and other persisted or external contracts until a separately reviewed migration can update their consumers.

## Required rules

- Preserve the flow: input guardrail → context → supervisor → domain agent → Judge → output guardrail.
- The Judge fails closed; an evaluation failure must never release a response.
- Customer and FAQ agents must never access internal operational data.
- Every session belongs to `empresa_id + usuario_id`; reject cross-tenant and cross-user access.
- Do not add automatic business actions; every business operation requires explicit user confirmation.
- Keep RAG sources in responses and message history.
- Use `.env` only locally; never commit or expose credentials.
- Reject startup when `JWT_SECRET` is missing, weak, or a documented placeholder.

## Minimum validation

```bash
python -m compileall -q app config interfaces tests scripts
python -m unittest discover -s tests -p 'test_*.py' -v
```

For an end-to-end check, start the API and validate `/health`, `/chat`, sessions, and `/metrics/summary` with real dependencies available.
