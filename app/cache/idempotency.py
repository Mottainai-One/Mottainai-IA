"""
Idempotency for writes that must not double-apply on client retry.

Unlike the RAG cache (app/rag/retriever's Redis cache) or the alert
webhook dedup, this is deliberately fail-CLOSED: no try/except around the
Redis calls below, same as app/cache/rate_limit.check_rate_limit (no
try/except either, called directly from guardrail_entrada). A duplicated
disposal/receipt is worse than a rejected request during a Redis outage.

Only covers the real bug (a client retrying after a lost response, so the
retry arrives once the first attempt has already finished and stored its
result): this is a plain GET-before-run, not a distributed lock. Two
requests with the same key arriving genuinely concurrently — both past
the GET before either has stored a result — can still both run `fn`. A
full lock (SET NX as a mutex around the call, not just the cached
result) would close that too, but nothing in the reported bug requires
it, and it would make failure handling (crash while holding the lock)
part of this change's surface for a scenario it isn't fixing.
"""
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
    """Runs `fn` at most once for a given (empresa_id, key): a repeated
    call with the same key returns the first call's stored result
    without running `fn` again. Only successful calls are cached — if
    `fn` raises, nothing is stored, so a corrected retry under the same
    key can still succeed."""
    validate_idempotency_key(key)
    settings = get_settings()
    redis = get_redis()
    cache_key = idempotency_key(empresa_id, key)

    cached = await redis.get(cache_key)
    if cached is not None:
        return json.loads(cached, object_hook=_decimal_object_hook)

    result = await fn()
    await redis.set(
        cache_key,
        json.dumps(result, cls=_DecimalPreservingEncoder),
        ex=settings.idempotency_ttl_seconds,
    )
    return result
