# IA in the shared QA environment

The existing Dockerfile is the runtime image for QA. The `IA QA container` workflow builds it without credentials; the existing `CI` workflow continues to run its full quality checks. Never put `.env`, API keys or database passwords in the image.

The shared Compose stack lives in `Mottainai-Banco-Operacional/qa`. It provides isolated PostgreSQL, MongoDB and Redis services. The IA container receives their addresses and QA-only credentials at runtime. Its `/readyz` endpoint checks these dependencies; `/livez` only confirms that the HTTP process is up. Startup deliberately rejects a privileged PostgreSQL account, a weak JWT secret, and missing tenant RLS policies.

The QA stack does not configure a live LLM key by default. To test `/chat`, configure a dedicated QA provider key outside Git and verify the relevant provider quota. The CI build alone does not establish that chat scenarios work end to end.
