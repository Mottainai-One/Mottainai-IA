"""Compatibility layer for Redis tools used by the agents.

Note: format_notifications_for_agent() output is injected directly into an
agent's LLM prompt, so it is deliberately kept in Portuguese.
"""
from app.cache.notifications import get_inbox, get_unread_count, mark_as_read

__all__ = ["get_inbox", "get_unread_count", "mark_as_read", "format_notifications_for_agent"]


async def format_notifications_for_agent(notifications: list[dict]) -> str:
    if not notifications:
        return "Nenhuma notificação pendente."
    lines = [f"Você tem {len(notifications)} notificação(ões) recente(s):"]
    for notification in notifications:
        priority = {"3": "ALTA", "2": "MÉDIA", "1": "BAIXA"}.get(str(notification.get("priority", 1)), "NORMAL")
        status = "não lida" if notification.get("status") == "unread" else "lida"
        lines.append(f"• [{priority}] {notification.get('title', '?')}: {notification.get('body', '?')} ({status})")
    return "\n".join(lines)
