"""Read layer for the skill_registry collection (MongoDB).

list_active() is TTL-cached, the same shape as app/agents/intent_catalog.
get_active_intents() — but fails CLOSED (an empty list), not open to a
hardcoded fallback: intent_catalog's fallback keywords are harmless to
serve during a Mongo outage (worst case, routing degrades to the old
hardcoded behavior), while a skill can be write_with_approval-scoped —
silently substituting some hardcoded skill list during an outage is a
worse failure mode than an agent temporarily having none available.

get_manifest() (used by app/skills/executor.py to authorize and resolve
one specific call) is deliberately NOT cached: it is the
authorization-critical read, and a document just disabled or edited
should take effect on the very next call, not up to CACHE_TTL_SECONDS
later. Caching is for the informational listing only.
"""
import asyncio
import logging
import time

from app.database.mongo import get_mongo_db
from app.skills.base import SkillManifest

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60
# Bounds one cold-cache Mongo round trip — the shared Mongo client sets no
# serverSelectionTimeoutMS, so an unreachable Mongo otherwise costs
# pymongo's default server-selection wait (~30s) per attempt instead of
# failing fast into the empty-list fallback below.
LOOKUP_TIMEOUT_S = 1.5

# Keyed by (empresa_id, role): list_active's own parameters, so a cached
# entry only ever answers the exact tenant+role it was fetched for.
_cache: dict[tuple[int, str], tuple[list[SkillManifest], float]] = {}


def _to_manifest(doc: dict) -> SkillManifest:
    return SkillManifest(
        id=str(doc["_id"]),
        empresa_id=doc["empresaId"],
        name=doc["name"],
        version=doc["version"],
        description=doc["description"],
        owner=doc["owner"],
        entrypoint=doc["entrypoint"],
        timeout_seconds=doc["timeoutSeconds"],
        max_retries=doc.get("maxRetries", 0),
        scope=doc["scope"],
        required_roles=doc["requiredRoles"],
        input_schema=doc["inputSchema"],
        output_schema=doc["outputSchema"],
        tags=doc.get("tags") or [],
        active=doc["active"],
    )


async def _fetch_active(empresa_id: int, role: str) -> list[dict]:
    db = get_mongo_db()
    # Tenant AND role filtered in the query itself, not after fetching —
    # a client-side filter one call site forgets to apply would be a
    # tenant/role leak the query shape structurally can't have.
    return [
        doc async for doc in db.skill_registry.find(
            {"empresaId": empresa_id, "active": True, "requiredRoles": role}
        )
    ]


async def list_active(empresa_id: int, role: str) -> list[SkillManifest]:
    """TTL-cached read of this tenant+role's active skills. On a Mongo
    timeout or error, logs and returns an empty list rather than raising
    or serving stale/fallback data — see module docstring."""
    cache_key = (empresa_id, role)
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached is not None and now - cached[1] < CACHE_TTL_SECONDS:
        return cached[0]

    try:
        docs = await asyncio.wait_for(_fetch_active(empresa_id, role), timeout=LOOKUP_TIMEOUT_S)
        manifests = [_to_manifest(doc) for doc in docs]
    except Exception:
        logger.warning(
            "skill_registry unavailable for empresa_id=%s role=%s — no skills available",
            empresa_id, role, exc_info=True,
        )
        return []

    _cache[cache_key] = (manifests, now)
    return manifests


async def get_manifest(empresa_id: int, name: str, version: str) -> SkillManifest | None:
    """Direct (uncached) lookup of one exact skill, scoped to the caller's
    tenant — a name+version that exists for a different empresaId must
    resolve to None here, not to that other tenant's document."""
    db = get_mongo_db()
    doc = await db.skill_registry.find_one({"empresaId": empresa_id, "name": name, "version": version})
    if doc is None:
        return None
    return _to_manifest(doc)
