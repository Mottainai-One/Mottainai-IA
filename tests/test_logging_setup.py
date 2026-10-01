"""Tests for structured logging: LOG_LEVEL actually applying, and the
per-request correlation id propagating into log records."""
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.observability.logging_setup import (
    configure_logging,
    get_correlation_id,
    new_correlation_id,
    set_correlation_id,
)


class ConfigureLoggingTests(unittest.TestCase):
    def tearDown(self):
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def test_applies_the_configured_log_level_to_the_root_logger(self):
        with patch(
            "app.observability.logging_setup.get_settings",
            return_value=SimpleNamespace(log_level="DEBUG"),
        ):
            configure_logging()

        self.assertEqual(logging.getLogger().level, logging.DEBUG)

    def test_is_safe_to_call_more_than_once(self):
        with patch(
            "app.observability.logging_setup.get_settings",
            return_value=SimpleNamespace(log_level="INFO"),
        ):
            configure_logging()
            configure_logging()

        root = logging.getLogger()
        self.assertEqual(len(root.handlers), 1)

    def test_attaches_the_correlation_filter_to_the_handler_not_the_root_logger(self):
        with patch(
            "app.observability.logging_setup.get_settings",
            return_value=SimpleNamespace(log_level="INFO"),
        ):
            configure_logging()

        root = logging.getLogger()
        self.assertEqual(len(root.filters), 0)
        self.assertTrue(any(root.handlers[0].filters))

    def test_correlation_id_reaches_a_record_from_a_child_logger(self):
        with patch(
            "app.observability.logging_setup.get_settings",
            return_value=SimpleNamespace(log_level="INFO"),
        ):
            configure_logging()

        set_correlation_id("test-corr-id")
        child_logger = logging.getLogger("app.agents.some_agent")
        record = child_logger.makeRecord(child_logger.name, logging.INFO, __file__, 1, "msg", (), None)

        handler = logging.getLogger().handlers[0]
        passed = all(f.filter(record) for f in handler.filters)

        self.assertTrue(passed)
        self.assertEqual(record.correlation_id, "test-corr-id")


class CorrelationIdHelpersTests(unittest.TestCase):
    def test_new_correlation_id_is_short_and_unique(self):
        first = new_correlation_id()
        second = new_correlation_id()

        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 12)

    def test_default_correlation_id_is_a_placeholder_when_unset(self):
        # A fresh ContextVar read outside any request context.
        self.assertIsInstance(get_correlation_id(), str)

    def test_set_and_get_round_trip(self):
        set_correlation_id("abc123")
        self.assertEqual(get_correlation_id(), "abc123")


if __name__ == "__main__":
    unittest.main()
