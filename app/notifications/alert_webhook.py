"""Outbound webhook delivery for CRITICAL alerts.

This is a push channel — distinct from app.cache.notifications' Redis
inbox, which is pull (an employee only sees it when they happen to chat
with the Employee Agent). Before this, mottainai.alert rows just sat
there; nothing external ever got pinged.

Deliberately best-effort / fail-open: a notification failure must never
break the Predictive Engine's actual analysis, which is what calls this.
Disabled entirely (returns False, no-op) when settings.alert_webhook_url
is empty — this project has no webhook receiver configured by default,
so silently doing nothing beats raising.
"""
import logging

import httpx

from app.cache.keyspace import notified_alert
from app.database.redis_client import get_redis
from config.settings import get_settings

logger = logging.getLogger(__name__)


async def send_webhook_notification(payload: dict) -> bool:
    """POSTs `payload` to settings.alert_webhook_url. Never raises."""
    settings = get_settings()
    if not settings.alert_webhook_url:
        return False

    try:
        async with httpx.AsyncClient(timeout=settings.alert_webhook_timeout_seconds) as client:
            response = await client.post(settings.alert_webhook_url, json=payload)
            response.raise_for_status()
        return True
    except Exception:
        logger.warning("Alert webhook delivery failed", exc_info=True)
        return False


async def notify_new_critical_alerts(empresa_id: int, alerts: list[dict]) -> int:
    """
    Sends a webhook for each CRITICAL alert not already notified. "Already
    notified" is tracked in Redis (fail-open: if the check itself is
    unavailable, sends anyway rather than silently dropping a real alert —
    a possible duplicate notification is a far smaller problem than a
    missed critical one). Returns how many webhooks were actually sent.
    """
    critical = [alert for alert in alerts if alert.get("priority") == "CRITICAL"]
    if not critical:
        return 0

    settings = get_settings()
    redis = get_redis()
    sent = 0

    for alert in critical:
        key = notified_alert(empresa_id, str(alert.get("id")))
        try:
            already_notified = bool(await redis.exists(key))
        except Exception:
            logger.warning("Alert notification dedup check unavailable — sending anyway", exc_info=True)
            already_notified = False

        if already_notified:
            continue

        delivered = await send_webhook_notification({
            "empresa_id": empresa_id,
            "alert_id": alert.get("id"),
            "type": alert.get("type"),
            "priority": alert.get("priority"),
            "title": alert.get("title"),
            "store_name": alert.get("store_name"),
        })
        if not delivered:
            continue

        sent += 1
        try:
            await redis.set(key, "1", ex=settings.notification_ttl_seconds)
        except Exception:
            logger.warning("Failed to persist alert-notification dedup marker", exc_info=True)

    return sent
