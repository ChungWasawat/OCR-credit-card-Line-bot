import json
import logging

from app.logging_setup import JsonFormatter


def _record(**kwargs) -> logging.LogRecord:
    defaults = dict(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    defaults.update(kwargs)
    return logging.LogRecord(**defaults)


def test_json_formatter_includes_severity_and_message():
    formatter = JsonFormatter()
    record = _record()

    payload = json.loads(formatter.format(record))

    assert payload["severity"] == "INFO"
    assert payload["message"] == "hello world"
    assert payload["logger"] == "test.logger"


def test_json_formatter_includes_extra_fields():
    formatter = JsonFormatter()
    record = _record()
    record.message_id = "msg-1"
    record.step = "ocr"
    record.latency_ms = 42

    payload = json.loads(formatter.format(record))

    assert payload["message_id"] == "msg-1"
    assert payload["step"] == "ocr"
    assert payload["latency_ms"] == 42


def test_json_formatter_includes_exc_info_when_present():
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _record(exc_info=sys.exc_info())

    payload = json.loads(formatter.format(record))

    assert "exc_info" in payload
    assert "ValueError" in payload["exc_info"]
    assert "boom" in payload["exc_info"]
