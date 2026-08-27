import unittest
from unittest.mock import patch

from app.cache.notifications import mark_as_read
from app.cache.rate_limit import check_rate_limit
from app.database import redis_client
from config.settings import Settings


class RedisContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limit_uses_atomic_script_and_unique_member(self):
        class Redis:
            async def eval(self, script, keys, *args):
                self.script, self.keys, self.args = script, keys, args
                return [2, 1]

        redis = Redis()
        with patch("app.cache.rate_limit.get_redis", return_value=redis):
            result = await check_rate_limit(10, 20)

        self.assertTrue(result.allowed)
        self.assertEqual(redis.keys, 1)
        self.assertIn("mottainai:v1:rate-limit:10:20", redis.args)
        self.assertIn(":", redis.args[3])
        self.assertIn("ZREMRANGEBYSCORE", redis.script)

    async def test_notification_creation_is_idempotent(self):
        from app.cache.notifications import _CREATE_SCRIPT
        self.assertIn("local is_new", _CREATE_SCRIPT)
        self.assertIn("if is_new then redis.call('INCR'", _CREATE_SCRIPT)

    async def test_mark_read_uses_user_scoped_keys(self):
        class Redis:
            async def eval(self, script, keys, *args):
                self.script, self.keys, self.args = script, keys, args
                return 1

        redis = Redis()
        with patch("app.cache.notifications.get_redis", return_value=redis):
            changed = await mark_as_read(10, 20, "notification-1")

        self.assertTrue(changed)
        self.assertEqual(redis.keys, 3)
        self.assertTrue(all(":10:20" in key for key in redis.args[:3]))
        self.assertIn("ZSCORE", redis.script)
        self.assertIn("return 2", redis.script)

    async def test_rejects_notification_identifier_with_separator(self):
        with self.assertRaises(ValueError):
            await mark_as_read(10, 20, "cross:tenant")


class RedisClientConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.original_pool = redis_client._pool
        redis_client._pool = None

    def tearDown(self):
        redis_client._pool = self.original_pool

    @patch("app.database.redis_client.aioredis.ConnectionPool.from_url")
    def test_passes_configured_password_to_connection_pool(self, from_url):
        settings = Settings(
            _env_file=None,
            redis_url="redis://127.0.0.1:6379/0",
            redis_password="local-redis-password",
        )
        with patch("app.database.redis_client.get_settings", return_value=settings):
            redis_client.get_redis_pool()

        self.assertEqual(from_url.call_args.kwargs["password"], "local-redis-password")


if __name__ == "__main__":
    unittest.main()
