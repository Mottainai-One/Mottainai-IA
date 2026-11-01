import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.database import redis_client
from config.settings import Settings


class RedisCacheIsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original = redis_client._pool, redis_client._cache_pool
        redis_client._pool = redis_client._cache_pool = None

    def tearDown(self):
        redis_client._pool, redis_client._cache_pool = self.original

    async def test_cache_uses_distinct_endpoint_password_and_pool(self):
        settings = Settings(_env_file=None, redis_url="redis://protected:6379/0",
                            redis_password="protected-password", redis_cache_url="redis://cache:6379/0",
                            redis_cache_password="cache-password")
        with patch.object(redis_client, "get_settings", return_value=settings), patch.object(
            redis_client.aioredis.ConnectionPool, "from_url", side_effect=[object(), object()]
        ) as create:
            protected = redis_client.get_redis_pool()
            cache = redis_client.get_cache_redis_pool()
            self.assertIsNot(protected, cache)
            self.assertEqual(create.call_args_list[0].args[0], "redis://protected:6379/0")
            self.assertEqual(create.call_args_list[1].args[0], "redis://cache:6379/0")
            self.assertEqual(create.call_args_list[1].kwargs["password"], "cache-password")
            self.assertIs(cache, redis_client.get_cache_redis_pool())

    async def test_unconfigured_cache_never_falls_back_to_protected_pool(self):
        with patch.object(redis_client, "get_settings", return_value=Settings(_env_file=None, redis_cache_url="")):
            with self.assertRaises(RuntimeError):
                redis_client.get_cache_redis_pool()
        self.assertIsNone(redis_client._pool)

    async def test_shutdown_closes_both_pools(self):
        protected = SimpleNamespace(disconnect=AsyncMock())
        cache = SimpleNamespace(disconnect=AsyncMock())
        redis_client._pool, redis_client._cache_pool = protected, cache
        await redis_client.close_redis_pool()
        protected.disconnect.assert_awaited_once()
        cache.disconnect.assert_awaited_once()
        self.assertIsNone(redis_client._pool)
        self.assertIsNone(redis_client._cache_pool)
