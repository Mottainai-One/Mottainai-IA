"""Redis key conventions. Do not change without bumping the version."""

PREFIX = "mottainai:v1"


def rate_limit(empresa_id: int, usuario_id: int) -> str:
    return f"{PREFIX}:rate-limit:{empresa_id}:{usuario_id}"


def notification_inbox(empresa_id: int, usuario_id: int) -> str:
    return f"{PREFIX}:notification:inbox:{empresa_id}:{usuario_id}"


def notification_unread(empresa_id: int, usuario_id: int) -> str:
    return f"{PREFIX}:notification:unread:{empresa_id}:{usuario_id}"


def notification(empresa_id: int, usuario_id: int, notification_id: str) -> str:
    return f"{PREFIX}:notification:data:{empresa_id}:{usuario_id}:{notification_id}"


def rag_result(empresa_id: int, query_hash: str) -> str:
    return f"{PREFIX}:rag:{empresa_id}:{query_hash}"


def notified_alert(empresa_id: int, alert_id: str) -> str:
    return f"{PREFIX}:alert-webhook-sent:{empresa_id}:{alert_id}"
