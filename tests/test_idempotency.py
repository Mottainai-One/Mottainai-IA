"""Unit contract for app/cache/idempotency.py, independent of any route."""
import unittest
from decimal import Decimal
from unittest.mock import patch

from app.cache.idempotency import InvalidIdempotencyKey, run_idempotent, validate_idempotency_key
from config.settings import get_settings


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.set_calls: list[tuple] = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.set_calls.append((key, value, ex))
        self.store[key] = value


class ValidateIdempotencyKeyTests(unittest.TestCase):
    def test_accepts_a_normal_key(self):
        self.assertEqual(validate_idempotency_key("abc-123"), "abc-123")

    def test_rejects_empty(self):
        with self.assertRaises(InvalidIdempotencyKey):
            validate_idempotency_key("")

    def test_rejects_a_colon(self):
        with self.assertRaises(InvalidIdempotencyKey):
            validate_idempotency_key("a:b")

    def test_accepts_exactly_128_chars(self):
        validate_idempotency_key("a" * 128)  # does not raise

    def test_rejects_over_128_chars(self):
        with self.assertRaises(InvalidIdempotencyKey):
            validate_idempotency_key("a" * 129)


class RunIdempotentTests(unittest.IsolatedAsyncioTestCase):
    async def test_stores_with_the_configured_ttl(self):
        redis = FakeRedis()
        with patch("app.cache.idempotency.get_redis", return_value=redis):
            await run_idempotent(1, "key", lambda: _returns({"a": 1}))

        [(key, _, ttl)] = redis.set_calls
        self.assertEqual(key, "mottainai:v1:idempotency:1:key")
        self.assertEqual(ttl, get_settings().idempotency_ttl_seconds)

    async def test_does_not_call_fn_again_on_a_cache_hit(self):
        redis = FakeRedis()
        calls = []

        async def fn():
            calls.append(1)
            return {"n": len(calls)}

        with patch("app.cache.idempotency.get_redis", return_value=redis):
            first = await run_idempotent(1, "key", fn)
            second = await run_idempotent(1, "key", fn)

        self.assertEqual(len(calls), 1)
        self.assertEqual(first, second)

    async def test_a_failed_call_is_not_cached_so_a_retry_can_still_succeed(self):
        redis = FakeRedis()
        attempts = {"n": 0}

        async def fn():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ValueError("transient")
            return {"ok": True}

        with patch("app.cache.idempotency.get_redis", return_value=redis):
            with self.assertRaises(ValueError):
                await run_idempotent(1, "key", fn)
            result = await run_idempotent(1, "key", fn)

        self.assertEqual(attempts["n"], 2)
        self.assertEqual(result, {"ok": True})

    async def test_preserves_decimal_type_across_the_cache_round_trip(self):
        redis = FakeRedis()
        with patch("app.cache.idempotency.get_redis", return_value=redis):
            await run_idempotent(1, "key", lambda: _returns({"amount": Decimal("71.500")}))
            replayed = await run_idempotent(1, "key", lambda: _returns({"amount": Decimal("999")}))

        self.assertEqual(replayed["amount"], Decimal("71.500"))
        self.assertIsInstance(replayed["amount"], Decimal)


async def _returns(value):
    return value


if __name__ == "__main__":
    unittest.main()
