"""Atomic Redis rate limit per company and user."""
from dataclasses import dataclass
from time import time
from uuid import uuid4

from app.cache.keyspace import endpoint_rate_limit, rate_limit
from app.database.redis_client import get_redis
from config.settings import get_settings

_RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local cutoff = tonumber(ARGV[1])
local now = tonumber(ARGV[2])
local member = ARGV[3]
local window = tonumber(ARGV[4])
local limit = tonumber(ARGV[5])
redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
redis.call('ZADD', key, now, member)
local count = redis.call('ZCARD', key)
redis.call('EXPIRE', key, window)
return {count, count <= limit and 1 or 0}
"""


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    request_count: int
    limit: int


async def _check_rate_limit(key: str, limit: int) -> RateLimitResult:
    settings = get_settings()
    now_ms = int(time() * 1000)
    window_ms = settings.rate_limit_window_seconds * 1000
    result = await get_redis().eval(
        _RATE_LIMIT_SCRIPT,
        1,
        key,
        now_ms - window_ms,
        now_ms,
        f"{now_ms}:{uuid4().hex}",
        settings.rate_limit_window_seconds,
        limit,
    )
    return RateLimitResult(allowed=bool(result[1]), request_count=int(result[0]), limit=limit)


async def check_rate_limit(empresa_id: int, usuario_id: int) -> RateLimitResult:
    """Checks the shared chat budget for one authenticated principal."""
    return await _check_rate_limit(
        rate_limit(empresa_id, usuario_id),
        get_settings().rate_limit_rpm,
    )


async def check_endpoint_rate_limit(
    empresa_id: int,
    usuario_id: int,
    *,
    scope: str,
    limit: int,
) -> RateLimitResult:
    """Checks an isolated budget for an expensive endpoint."""
    if not scope.replace("-", "").replace("_", "").isalnum():
        raise ValueError("scope must be an alphanumeric identifier")
    return await _check_rate_limit(
        endpoint_rate_limit(scope, empresa_id, usuario_id),
        limit,
    )
