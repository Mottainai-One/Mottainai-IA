"""Separate Redis pools for protected state and disposable RAG cache."""
import asyncio

import redis.asyncio as aioredis

from config.settings import get_settings

_pool: aioredis.ConnectionPool | None = None
_cache_pool: aioredis.ConnectionPool | None = None


def get_redis_pool() -> aioredis.ConnectionPool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = aioredis.ConnectionPool.from_url(
            settings.redis_url,
            password=settings.redis_password or None,
            decode_responses=True,
            max_connections=settings.redis_max_connections,
            socket_connect_timeout=settings.redis_connect_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
            health_check_interval=settings.redis_health_check_interval_seconds,
        )
    return _pool


def get_redis() -> aioredis.Redis:
    return aioredis.Redis(connection_pool=get_redis_pool())


def get_cache_redis_pool() -> aioredis.ConnectionPool:
    global _cache_pool
    if _cache_pool is None:
        settings = get_settings()
        if not settings.redis_cache_url:
            raise RuntimeError("REDIS_CACHE_URL is not configured; RAG cache is disabled")
        _cache_pool = aioredis.ConnectionPool.from_url(
            settings.redis_cache_url,
            password=settings.redis_cache_password or None,
            decode_responses=True,
            max_connections=settings.redis_max_connections,
            socket_connect_timeout=settings.redis_connect_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
            health_check_interval=settings.redis_health_check_interval_seconds,
        )
    return _cache_pool


def get_cache_redis() -> aioredis.Redis:
    return aioredis.Redis(connection_pool=get_cache_redis_pool())


async def close_redis_pool() -> None:
    global _pool, _cache_pool
    pools = [_pool, _cache_pool]
    _pool = _cache_pool = None
    await asyncio.gather(*(pool.disconnect() for pool in pools if pool is not None))
