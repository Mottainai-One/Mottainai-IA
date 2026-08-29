"""Structured logging: applies settings.log_level (previously unused — no
code anywhere called logging.basicConfig, so LOG_LEVEL had no effect) and
attaches a per-request correlation id to every log line via a ContextVar,
so log lines from the same HTTP request can be grepped together across the
whole guardrail -> supervisor -> agent -> Judge -> guardrail pipeline
without threading an id through every function signature.
"""
import logging
import uuid
from contextvars import ContextVar

from config.settings import get_settings

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

LOG_FORMAT = "%(asctime)s %(levelname)s [%(correlation_id)s] %(name)s: %(message)s"


class _CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get()
        return True


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:12]


def get_correlation_id() -> str:
    return _correlation_id.get()


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)


def configure_logging() -> None:
    """
    Idempotent: safe to call more than once (e.g. once per test).

    The correlation-id filter is attached to the HANDLER, not the root
    logger — a logging.Filter added to a logger only runs for records
    originating on that exact logger, not for records propagating up from
    child loggers (e.g. "app.agents.juiz"), while a filter on the handler
    runs for every record the handler emits regardless of origin.
    """
    settings = get_settings()
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())

    if not root.handlers:
        root.addHandler(logging.StreamHandler())

    for handler in root.handlers:
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        if not any(isinstance(f, _CorrelationIdFilter) for f in handler.filters):
            handler.addFilter(_CorrelationIdFilter())
