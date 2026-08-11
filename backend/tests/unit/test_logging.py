"""Structured logging must never be able to fail a request.

The bug this guards against turned every 4xx into a 500: a handler logged
``extra={"message": ...}``, and ``message`` is a reserved LogRecord attribute,
so ``logging`` raised KeyError inside the exception handler.

It was invisible to the rest of the backend suite because pytest's log capture
suppresses it -- a browser-level console-error check is what surfaced it. So
the fix is structural (SafeLogger renames colliding keys) and this test asserts
the structure rather than the one symptom.
"""

from __future__ import annotations

import logging

import pytest

from app.core.logging import SafeLogger, configure_logging, get_logger

RESERVED = ["message", "args", "exc_info", "levelname", "module", "lineno", "name"]


@pytest.fixture(autouse=True)
def _configured():
    configure_logging(level="DEBUG", fmt="json")


class TestSafeLogger:
    def test_app_loggers_are_safe_loggers(self):
        assert isinstance(get_logger("app.probe"), SafeLogger)

    @pytest.mark.parametrize("key", RESERVED)
    def test_reserved_extra_keys_do_not_raise(self, key):
        get_logger("app.probe").warning("domain error", extra={key: "value"})

    def test_the_colliding_value_is_preserved_under_a_prefix(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.probe"):
            get_logger("app.probe").warning("domain error", extra={"message": "kept"})

        record = caplog.records[-1]
        # Renamed rather than dropped: the diagnostic value is the reason the
        # caller passed it.
        assert record.ctx_message == "kept"
        assert record.getMessage() == "domain error"

    def test_non_reserved_keys_are_untouched(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.probe"):
            get_logger("app.probe").warning("x", extra={"error_code": "NOT_FOUND"})

        assert caplog.records[-1].error_code == "NOT_FOUND"

    def test_a_stdlib_logger_still_raises(self):
        """Confirms the hazard is real and that SafeLogger is what removes it,
        rather than the behaviour having changed in the stdlib."""
        plain = logging.Logger("not_an_app_logger")
        with pytest.raises(KeyError, match="message"):
            plain.warning("x", extra={"message": "boom"})


class TestJsonFormatting:
    """The formatter is tested directly rather than through captured stdout.

    The handler binds to whichever stream existed when `configure_logging` ran,
    which is not necessarily the one pytest is capturing -- so asserting on
    captured output would test fixture ordering, not the formatter.
    """

    @staticmethod
    def _render(logger_name: str = "app.probe", **extra: object) -> dict:
        import json

        from app.core.logging import JsonFormatter

        record = get_logger(logger_name).makeRecord(
            logger_name, logging.WARNING, __file__, 1, "hello", None, None, extra=extra or None
        )
        return json.loads(JsonFormatter().format(record))

    def test_renders_one_json_object(self):
        payload = self._render(k="v")

        assert payload["message"] == "hello"
        assert payload["level"] == "WARNING"
        assert payload["logger"] == "app.probe"
        assert payload["k"] == "v"
        assert payload["timestamp"].endswith("+00:00")  # UTC, per NFR-3

    def test_request_id_is_attached_when_bound(self):
        from app.core.logging import bind_request_id

        bind_request_id("trace-123")
        assert self._render()["request_id"] == "trace-123"

    def test_user_id_is_attached_when_bound(self):
        from app.core.logging import bind_request_id, bind_user_id

        bind_request_id("trace-123")
        bind_user_id("user-abc")
        assert self._render()["user_id"] == "user-abc"

    def test_a_reserved_extra_key_survives_renamed(self):
        assert self._render(message="collided")["ctx_message"] == "collided"
