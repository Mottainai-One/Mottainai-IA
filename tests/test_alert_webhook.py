"""Outbound webhook delivery for CRITICAL alerts."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.notifications.alert_webhook import notify_new_critical_alerts, send_webhook_notification


def _settings(**overrides):
    base = {"alert_webhook_url": "", "alert_webhook_timeout_seconds": 5.0, "notification_ttl_seconds": 604800}
    base.update(overrides)
    return SimpleNamespace(**base)


class SendWebhookNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_is_a_noop_when_no_webhook_url_is_configured(self):
        with patch("app.notifications.alert_webhook.get_settings", return_value=_settings()):
            delivered = await send_webhook_notification({"alert_id": 1})

        self.assertFalse(delivered)

    async def test_posts_the_payload_and_returns_true_on_success(self):
        settings = _settings(alert_webhook_url="https://hooks.example.com/alerts")
        response = MagicMock()
        response.raise_for_status = MagicMock()
        client = AsyncMock()
        client.post = AsyncMock(return_value=response)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.notifications.alert_webhook.get_settings", return_value=settings),
            patch("app.notifications.alert_webhook.httpx.AsyncClient", return_value=client),
        ):
            delivered = await send_webhook_notification({"alert_id": 1})

        self.assertTrue(delivered)
        client.post.assert_awaited_once_with("https://hooks.example.com/alerts", json={"alert_id": 1})

    async def test_returns_false_instead_of_raising_on_delivery_failure(self):
        settings = _settings(alert_webhook_url="https://hooks.example.com/alerts")
        client = AsyncMock()
        client.post = AsyncMock(side_effect=ConnectionError("down"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.notifications.alert_webhook.get_settings", return_value=settings),
            patch("app.notifications.alert_webhook.httpx.AsyncClient", return_value=client),
        ):
            delivered = await send_webhook_notification({"alert_id": 1})

        self.assertFalse(delivered)


class NotifyNewCriticalAlertsTests(unittest.IsolatedAsyncioTestCase):
    def _alerts(self):
        return [
            {"id": "1", "priority": "CRITICAL", "title": "Lote vencendo"},
            {"id": "2", "priority": "MEDIUM", "title": "Estoque baixo"},
            {"id": "3", "priority": "CRITICAL", "title": "Ruptura"},
        ]

    async def test_only_notifies_critical_alerts(self):
        redis = AsyncMock()
        redis.exists = AsyncMock(return_value=0)
        redis.set = AsyncMock()
        send = AsyncMock(return_value=True)

        with (
            patch("app.notifications.alert_webhook.get_redis", return_value=redis),
            patch("app.notifications.alert_webhook.get_settings", return_value=_settings()),
            patch("app.notifications.alert_webhook.send_webhook_notification", new=send),
        ):
            sent = await notify_new_critical_alerts(42, self._alerts())

        self.assertEqual(sent, 2)
        self.assertEqual(send.await_count, 2)

    async def test_skips_alerts_already_notified(self):
        redis = AsyncMock()
        redis.exists = AsyncMock(return_value=1)  # every alert looks already-notified
        send = AsyncMock(return_value=True)

        with (
            patch("app.notifications.alert_webhook.get_redis", return_value=redis),
            patch("app.notifications.alert_webhook.get_settings", return_value=_settings()),
            patch("app.notifications.alert_webhook.send_webhook_notification", new=send),
        ):
            sent = await notify_new_critical_alerts(42, self._alerts())

        self.assertEqual(sent, 0)
        send.assert_not_awaited()

    async def test_dedup_check_failure_fails_open_and_still_sends(self):
        redis = AsyncMock()
        redis.exists = AsyncMock(side_effect=ConnectionError("down"))
        redis.set = AsyncMock()
        send = AsyncMock(return_value=True)

        with (
            patch("app.notifications.alert_webhook.get_redis", return_value=redis),
            patch("app.notifications.alert_webhook.get_settings", return_value=_settings()),
            patch("app.notifications.alert_webhook.send_webhook_notification", new=send),
        ):
            sent = await notify_new_critical_alerts(42, self._alerts())

        self.assertEqual(sent, 2)

    async def test_does_not_mark_as_notified_when_delivery_failed(self):
        redis = AsyncMock()
        redis.exists = AsyncMock(return_value=0)
        redis.set = AsyncMock()
        send = AsyncMock(return_value=False)

        with (
            patch("app.notifications.alert_webhook.get_redis", return_value=redis),
            patch("app.notifications.alert_webhook.get_settings", return_value=_settings()),
            patch("app.notifications.alert_webhook.send_webhook_notification", new=send),
        ):
            sent = await notify_new_critical_alerts(42, self._alerts())

        self.assertEqual(sent, 0)
        redis.set.assert_not_awaited()

    async def test_no_critical_alerts_never_touches_redis(self):
        redis = AsyncMock()

        with (
            patch("app.notifications.alert_webhook.get_redis", return_value=redis),
            patch("app.notifications.alert_webhook.get_settings", return_value=_settings()),
        ):
            sent = await notify_new_critical_alerts(42, [{"id": "1", "priority": "LOW"}])

        self.assertEqual(sent, 0)
        redis.exists.assert_not_called()


if __name__ == "__main__":
    unittest.main()
