"""Temporary notifications, isolated per company and user."""
from dataclasses import dataclass
from time import time
from uuid import uuid4

from app.cache.keyspace import notification, notification_inbox, notification_unread
from app.database.redis_client import get_redis
from config.settings import get_settings

_CREATE_SCRIPT = """
local is_new = redis.call('EXISTS', KEYS[1]) == 0
redis.call('HSET', KEYS[1], 'id', ARGV[1], 'title', ARGV[2], 'body', ARGV[3], 'priority', ARGV[4], 'status', 'unread', 'created_at', ARGV[5])
redis.call('EXPIRE', KEYS[1], ARGV[6])
redis.call('ZADD', KEYS[2], ARGV[7], ARGV[1])
redis.call('EXPIRE', KEYS[2], ARGV[6])
if is_new then redis.call('INCR', KEYS[3]) end
redis.call('EXPIRE', KEYS[3], ARGV[6])
return redis.call('GET', KEYS[3]) or '0'
"""

_MARK_READ_SCRIPT = """
if redis.call('ZSCORE', KEYS[1], ARGV[1]) == false then return -1 end
if redis.call('HGET', KEYS[2], 'status') == 'read' then return 2 end
if redis.call('HGET', KEYS[2], 'status') ~= 'unread' then return -1 end
redis.call('HSET', KEYS[2], 'status', 'read')
local current = tonumber(redis.call('GET', KEYS[3]) or '0')
if current > 0 then redis.call('DECR', KEYS[3]) end
return 1
"""


@dataclass(frozen=True)
class Notification:
    id: str
    title: str
    body: str
    priority: int
    status: str
    created_at: int


def _validate_id(value: str) -> str:
    if not value or ':' in value or len(value) > 128:
        raise ValueError('Invalid notification identifier.')
    return value


async def create_notification(
    empresa_id: int, usuario_id: int, title: str, body: str, priority: int = 1, notification_id: str | None = None,
) -> Notification:
    if priority not in (1, 2, 3):
        raise ValueError('Priority must be 1, 2 or 3.')
    notification_id = _validate_id(notification_id or uuid4().hex)
    settings = get_settings()
    created_at = int(time())
    score = priority * 10_000_000_000 + created_at
    await get_redis().eval(
        _CREATE_SCRIPT,
        3,
        notification(empresa_id, usuario_id, notification_id),
        notification_inbox(empresa_id, usuario_id),
        notification_unread(empresa_id, usuario_id),
        notification_id, title, body, priority, created_at, settings.notification_ttl_seconds, score,
    )
    return Notification(notification_id, title, body, priority, 'unread', created_at)


async def get_unread_count(empresa_id: int, usuario_id: int) -> int:
    value = await get_redis().get(notification_unread(empresa_id, usuario_id))
    return int(value or 0)


async def get_inbox(empresa_id: int, usuario_id: int, limit: int = 10) -> list[dict[str, str]]:
    if not 1 <= limit <= 100:
        raise ValueError('The limit must be between 1 and 100.')
    redis = get_redis()
    inbox_key = notification_inbox(empresa_id, usuario_id)
    ids = await redis.zrevrange(inbox_key, 0, limit - 1)
    if not ids:
        return []
    pipe = redis.pipeline(transaction=False)
    for notification_id in ids:
        pipe.hgetall(notification(empresa_id, usuario_id, notification_id))
    entries = await pipe.execute()
    return [{"id": notification_id, **entry} for notification_id, entry in zip(ids, entries) if entry]


async def mark_as_read(empresa_id: int, usuario_id: int, notification_id: str) -> bool:
    notification_id = _validate_id(notification_id)
    changed = await get_redis().eval(
        _MARK_READ_SCRIPT,
        3,
        notification_inbox(empresa_id, usuario_id),
        notification(empresa_id, usuario_id, notification_id),
        notification_unread(empresa_id, usuario_id),
        notification_id,
    )
    return changed == 1
