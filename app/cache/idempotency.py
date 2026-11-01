"""
Idempotency for writes that must not double-apply on client retry.

Unlike the RAG cache (app/rag/retriever's Redis cache) or the alert
webhook dedup, this is deliberately fail-CLOSED: no try/except around the
Redis calls below, same as app/cache/rate_limit.check_rate_limit (no
try/except either, called directly from guardrail_entrada). A duplicated
disposal/receipt is worse than a rejected request during a Redis outage.

An ownership-token Redis lock serializes each key across workers. It has no
automatic expiry: a slow or interrupted database write must never be overtaken
by a second writer. An ambiguous failure retains the lock for reconciliation;
see docs/OFFICIAL_DATABASE_VALIDATION.md. Redis loss/eviction and PostgreSQL
commit ambiguity still prevent a durable exactly-once guarantee.
"""
import hashlib
import json
from decimal import Decimal
from typing import Any, Awaitable, Callable, TypeVar

from app.cache.keyspace import idempotency as idempotency_key
from app.database.redis_client import get_redis
from config.settings import get_settings

T = TypeVar("T")


class InvalidIdempotencyKey(ValueError):
    """The Idempotency-Key header's value doesn't meet the shape rule
    below — a client input error (400), distinguishable at the route
    layer from any other ValueError `fn` itself might raise."""


class IdempotencyInProgress(RuntimeError):
    """The key is executing or requires reconciliation after interruption."""


class _DecimalPreservingEncoder(json.JSONEncoder):
    """discard_batch/receive_inventory return real Decimal amounts (Postgres
    DECIMAL columns). FastAPI's own jsonable_encoder turns those into JSON
    numbers on the way out either way, but round-tripping the CACHED value
    through plain json.dumps(default=str) first would leave it a Python str
    on replay — a different Python type serving the identical wire shape by
    coincidence today, and a real mismatch if a future field needs precision
    float can't hold. Tagging Decimals explicitly avoids depending on that
    coincidence."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return {"__decimal__": str(obj)}
        return super().default(obj)


def _decimal_object_hook(obj: dict) -> Any:
    if set(obj) == {"__decimal__"}:
        return Decimal(obj["__decimal__"])
    return obj


def validate_idempotency_key(key: str) -> str:
    """Same shape rule as app/cache/notifications.py's identifiers: no
    ':' (keeps the Redis key unambiguous) and a length cap (bounds how
    much an attacker-controlled header can make Redis store)."""
    if not key or ":" in key or len(key) > 128:
        raise InvalidIdempotencyKey("Idempotency-Key must be non-empty, at most 128 chars, and contain no ':'.")
    return key


async def run_idempotent(empresa_id: int, key: str, fn: Callable[[], Awaitable[T]]) -> T:
    """Replay successful results; reject overlapping/uncertain executions.

    Callbacks must roll back before raising validation/authorization errors.
    Other errors, cancellation and failed result storage retain the lock.
    Completed results retain the configured TTL, not an infinite guarantee.
    """
    validate_idempotency_key(key)
    settings = get_settings()
    redis = get_redis()
    cache_key = idempotency_key(empresa_id, key)

    cached = await redis.get(cache_key)
    if cached is not None:
        return json.loads(cached, object_hook=_decimal_object_hook)

    lock = redis.lock(f"{cache_key}:lock", timeout=None, blocking=False, thread_local=False)
    if not await lock.acquire():
        # Completion may have raced with the failed acquisition.
        cached = await redis.get(cache_key)
        if cached is not None:
            return json.loads(cached, object_hook=_decimal_object_hook)
        raise IdempotencyInProgress("Write is in progress or awaiting reconciliation")

    # Another worker may have completed between the initial GET and acquisition.
    cached = await redis.get(cache_key)
    if cached is not None:
        await lock.release()
        return json.loads(cached, object_hook=_decimal_object_hook)
    try:
        result = await fn()
    except (ValueError, PermissionError):
        await lock.release()
        raise
    # Deliberately no finally-release: after an uncertain commit or cache failure,
    # retrying the write automatically could apply the inventory movement twice.
    await redis.set(
        cache_key,
        json.dumps(result, cls=_DecimalPreservingEncoder),
        ex=settings.idempotency_ttl_seconds,
    )
    await lock.release()
    return result


def scoped_write_key(user_id: int, operation: str, key: str) -> str:
    """Keep retries private to their actor and operation, even within one company."""
    validate_idempotency_key(key)
    return hashlib.sha256(f"{user_id}:{operation}:{key}".encode()).hexdigest()
