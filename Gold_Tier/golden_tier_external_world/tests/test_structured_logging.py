from __future__ import annotations

from unittest import TestCase, mock
import io
import json
import logging

from golden_tier_external_world.log_utils.structured_logger import (
    JsonFormatter,
    StructuredLogger,
    setup_logging,
    set_correlation_id,
    get_correlation_id,
)


class TestJsonFormatter(TestCase):
    def setUp(self) -> None:
        self.formatter = JsonFormatter()

    def _make_record(self, msg: str, level: int = logging.INFO) -> logging.LogRecord:
        record = logging.LogRecord(
            name="test_logger",
            level=level,
            pathname=__file__,
            lineno=42,
            msg=msg,
            args=(),
            exc_info=None,
        )
        record.funcName = "test_func"
        return record

    def test_format_returns_json(self) -> None:
        record = self._make_record("hello world")
        output = self.formatter.format(record)
        parsed = json.loads(output)
        self.assertEqual(parsed["message"], "hello world")
        self.assertEqual(parsed["level"], "INFO")
        self.assertEqual(parsed["logger"], "test_logger")
        self.assertEqual(parsed["module"], "test_structured_logging")
        self.assertEqual(parsed["function"], "test_func")

    def test_format_includes_timestamp(self) -> None:
        record = self._make_record("test")
        output = self.formatter.format(record)
        parsed = json.loads(output)
        self.assertIn("timestamp", parsed)
        self.assertIn("T", parsed["timestamp"])

    def test_format_includes_correlation_id(self) -> None:
        set_correlation_id("corr-123")
        record = self._make_record("test")
        output = self.formatter.format(record)
        parsed = json.loads(output)
        self.assertEqual(parsed["correlation_id"], "corr-123")
        set_correlation_id(None)

    def test_format_no_correlation_id(self) -> None:
        set_correlation_id(None)
        record = self._make_record("test")
        output = self.formatter.format(record)
        parsed = json.loads(output)
        self.assertNotIn("correlation_id", parsed)

    def test_format_with_exception(self) -> None:
        try:
            raise ValueError("test error")
        except ValueError:
            record = self._make_record("error occurred", level=logging.ERROR)
            record.exc_info = (ValueError, ValueError("test error"), None)
            output = self.formatter.format(record)
            parsed = json.loads(output)
            self.assertEqual(parsed["exception"]["type"], "ValueError")
            self.assertIn("test error", parsed["exception"]["message"])

    def test_format_extra_fields(self) -> None:
        record = self._make_record("test")
        record.extra_fields = {"platform": "facebook", "event_id": "evt_1"}
        output = self.formatter.format(record)
        parsed = json.loads(output)
        self.assertEqual(parsed["platform"], "facebook")
        self.assertEqual(parsed["event_id"], "evt_1")


class TestSetupLogging(TestCase):
    def tearDown(self) -> None:
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def test_setup_logging_json_format(self) -> None:
        setup_logging(level=logging.DEBUG, json_format=True)
        root = logging.getLogger()
        self.assertEqual(root.level, logging.DEBUG)
        self.assertTrue(any(isinstance(h, logging.StreamHandler) for h in root.handlers))

    def test_setup_logging_text_format(self) -> None:
        setup_logging(level=logging.INFO, json_format=False)
        root = logging.getLogger()
        self.assertEqual(root.level, logging.INFO)

    def test_output_is_json(self) -> None:
        buf = io.StringIO()
        setup_logging(level=logging.DEBUG, json_format=True)
        root = logging.getLogger()
        for h in root.handlers:
            h.stream = buf

        logger = logging.getLogger("test_output")
        logger.info("json message")
        output = buf.getvalue().strip()
        parsed = json.loads(output)
        self.assertEqual(parsed["message"], "json message")
        self.assertEqual(parsed["logger"], "test_output")


class TestCorrelationId(TestCase):
    def test_set_and_get(self) -> None:
        set_correlation_id("test-cid")
        self.assertEqual(get_correlation_id(), "test-cid")
        set_correlation_id(None)
        self.assertIsNone(get_correlation_id())

    def test_thread_isolation(self) -> None:
        import threading

        set_correlation_id("main-cid")
        results: list[str | None] = []

        def worker() -> None:
            results.append(get_correlation_id())

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        self.assertIsNone(results[0])
        self.assertEqual(get_correlation_id(), "main-cid")
        set_correlation_id(None)


class TestStructuredLogger(TestCase):
    def test_extra_fields(self) -> None:
        logger = logging.getLogger("test_extra")
        handler = logging.StreamHandler(io.StringIO())
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        buf = io.StringIO()
        handler.stream = buf

        logger.info("with fields", extra={"extra_fields": {"component": "test"}})
        output = buf.getvalue().strip()
        parsed = json.loads(output)
        self.assertEqual(parsed["component"], "test")
        logger.handlers.clear()
