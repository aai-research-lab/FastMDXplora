"""Tests for the FastMDXplora logging module.

Covers:
  - Pretty formatter (with and without color)
  - Plain ISO formatter (file-style, includes milliseconds)
  - setup_console idempotency (no duplicate handlers on repeat calls)
  - attach_file_logger replaces (not duplicates) the file handler
  - Environment variable overrides (FASTMDX_LOG_STYLE, FASTMDX_LOGLEVEL, NO_COLOR)
  - get_logger returns the package logger and child loggers correctly
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from fastmdxplora.utils.logging import (
    _PlainISOFormatter,
    _PrettyFormatter,
    attach_file_logger,
    get_logger,
    set_level,
    setup_console,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def fresh_logger(monkeypatch):
    """Reset the package logger state around each test.

    Removes all handlers and resets the module-level handler bookkeeping
    so successive tests do not see each other's handlers.
    """
    import fastmdxplora.utils.logging as logmod

    base = logging.getLogger("fastmdx")
    saved_handlers = list(base.handlers)
    saved_level = base.level
    saved_propagate = base.propagate

    base.handlers.clear()
    monkeypatch.setattr(logmod, "_console_handler", None, raising=True)
    monkeypatch.setattr(logmod, "_file_handler", None, raising=True)

    # Clear logging env vars by default; tests can re-set as needed.
    monkeypatch.delenv("FASTMDX_LOG_STYLE", raising=False)
    monkeypatch.delenv("FASTMDX_LOGLEVEL", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)

    yield base

    # Restore prior state.
    base.handlers.clear()
    for h in saved_handlers:
        base.addHandler(h)
    base.level = saved_level
    base.propagate = saved_propagate


def _make_record(level: int = logging.INFO, msg: str = "hello") -> logging.LogRecord:
    return logging.LogRecord(
        name="fastmdx.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )


def _owned_handlers(log: logging.Logger) -> list[logging.Handler]:
    """Handlers installed by FastMDXplora."""
    return [
        h for h in log.handlers
        if getattr(h, "_fastmdx_owned", False)
    ]


def _owned_handlers_of_kind(
    log: logging.Logger,
    kind: str,
) -> list[logging.Handler]:
    return [
        h for h in _owned_handlers(log)
        if getattr(h, "_fastmdx_handler_kind", None) == kind
    ]


def _owned_console_handler(log: logging.Logger) -> logging.Handler:
    handlers = _owned_handlers_of_kind(log, "console")
    assert len(handlers) == 1
    return handlers[0]


def _owned_file_handlers(log: logging.Logger) -> list[logging.Handler]:
    return _owned_handlers_of_kind(log, "file")


# ---------------------------------------------------------------------------
# Formatter behavior
# ---------------------------------------------------------------------------
class TestPrettyFormatter:
    def test_no_color_when_disabled(self):
        fmt = _PrettyFormatter(use_color=False)
        out = fmt.format(_make_record())
        # Ensure no ANSI escape sequences leaked in
        assert "\x1b[" not in out
        # Should have icon, level, message
        assert "INFO" in out
        assert "hello" in out
        assert "✓" in out  # info icon

    def test_color_when_enabled(self):
        fmt = _PrettyFormatter(use_color=True)
        out = fmt.format(_make_record())
        assert "\x1b[" in out  # ANSI escape present
        assert "INFO" in out
        assert "hello" in out

    def test_level_icons(self):
        fmt = _PrettyFormatter(use_color=False)
        expected = {logging.DEBUG: "·", logging.INFO: "✓",
                    logging.WARNING: "⚠", logging.ERROR: "✗", logging.CRITICAL: "‼"}
        for level, icon in expected.items():
            out = fmt.format(_make_record(level=level))
            assert icon in out


class TestPlainISOFormatter:
    def test_iso_format_with_milliseconds(self):
        fmt = _PlainISOFormatter()
        out = fmt.format(_make_record())
        # Pattern: YYYY-MM-DD HH:MM:SS,mmm - LEVEL - message
        assert re.match(
            r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - INFO - hello",
            out,
        ), f"unexpected format: {out!r}"


# ---------------------------------------------------------------------------
# setup_console
# ---------------------------------------------------------------------------
class TestSetupConsole:
    def test_first_call_adds_handler(self, fresh_logger):
        log = setup_console()
        assert len(_owned_handlers_of_kind(log, "console")) == 1

    def test_it_does_not_claim_the_process(self, fresh_logger):
        # This used to assert `propagate is False`, because setup_console
        # set it. That one line ran on the library path too, so importing
        # the package and running a study left the caller's own logging
        # permanently unable to see anything from us -- silently.
        #
        # Attaching a handler and deciding who owns the process are two
        # decisions. own_the_console() is the second one, and the CLI is
        # the only caller.
        from fastmdxplora.utils.logging import own_the_console

        fresh_logger.propagate = True
        setup_console()
        assert fresh_logger.propagate is True

        own_the_console()
        assert fresh_logger.propagate is False

    def test_idempotent_no_duplicate_handler(self, fresh_logger):
        setup_console()
        setup_console()
        setup_console()
        assert len(_owned_handlers_of_kind(fresh_logger, "console")) == 1

    def test_level_argument_applied(self, fresh_logger):
        setup_console(level=logging.WARNING)
        assert fresh_logger.level == logging.WARNING

    def test_env_loglevel_overrides_argument(self, fresh_logger, monkeypatch):
        monkeypatch.setenv("FASTMDX_LOGLEVEL", "DEBUG")
        setup_console(level=logging.WARNING)
        assert fresh_logger.level == logging.DEBUG

    def test_env_style_overrides_argument(self, fresh_logger, monkeypatch):
        monkeypatch.setenv("FASTMDX_LOG_STYLE", "plain")
        log = setup_console(style="pretty")
        handler = _owned_console_handler(log)
        assert isinstance(handler.formatter, _PlainISOFormatter)

    def test_pretty_is_default(self, fresh_logger):
        log = setup_console()
        handler = _owned_console_handler(log)
        assert isinstance(handler.formatter, _PrettyFormatter)

    def test_external_handler_is_ignored_for_console_idempotency(self, fresh_logger):
        external = logging.StreamHandler()
        external.setLevel(logging.ERROR)
        fresh_logger.addHandler(external)

        log = setup_console(level=logging.INFO)

        assert external in log.handlers
        assert getattr(external, "_fastmdx_owned", False) is False
        assert external.level == logging.ERROR
        assert len(_owned_handlers_of_kind(log, "console")) == 1

        setup_console(level=logging.DEBUG)

        assert external in log.handlers
        assert external.level == logging.ERROR
        assert len(_owned_handlers_of_kind(log, "console")) == 1


# ---------------------------------------------------------------------------
# attach_file_logger
# ---------------------------------------------------------------------------
class TestAttachFileLogger:
    def test_writes_to_file(self, fresh_logger, tmp_path: Path):
        log_path = tmp_path / "session.log"
        log = attach_file_logger(log_path)
        log.info("hello from a test")
        # Flush by closing
        for h in log.handlers:
            h.flush()
        contents = log_path.read_text(encoding="utf-8")
        assert "hello from a test" in contents
        # Plain (file default) format includes the ISO timestamp
        assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", contents)

    def test_creates_parent_dirs(self, fresh_logger, tmp_path: Path):
        deep_path = tmp_path / "a" / "b" / "c" / "session.log"
        attach_file_logger(deep_path)
        # The file is opened in append mode so the file itself may be
        # 0 bytes until a message is written; but its parent must exist.
        assert deep_path.parent.exists()

    def test_replaces_previous_file_handler(self, fresh_logger, tmp_path: Path):
        first = tmp_path / "first.log"
        second = tmp_path / "second.log"
        log = attach_file_logger(first)
        attach_file_logger(second)

        # Count FastMDXplora-owned file handlers attached to the logger.
        file_handlers = _owned_file_handlers(log)
        assert len(file_handlers) == 1
        assert isinstance(file_handlers[0], logging.FileHandler)
        assert Path(file_handlers[0].baseFilename) == second


# ---------------------------------------------------------------------------
# get_logger
# ---------------------------------------------------------------------------
class TestGetLogger:
    def test_root_logger_name(self):
        log = get_logger()
        assert log.name == "fastmdx"

    def test_child_logger_name(self):
        log = get_logger("analysis.rmsd")
        assert log.name == "fastmdx.analysis.rmsd"

    def test_child_inherits_handlers(self, fresh_logger):
        # Setup console on the root, then verify the child propagates.
        setup_console()
        child = get_logger("analysis.test")
        # The child has no handlers of its own — it relies on the root.
        assert child.handlers == []
        # The root has exactly one handler from setup_console.
        assert len(_owned_handlers_of_kind(fresh_logger, "console")) == 1


# ---------------------------------------------------------------------------
# set_level
# ---------------------------------------------------------------------------
class TestSetLevel:
    def test_changes_root_level(self, fresh_logger):
        setup_console(level=logging.INFO)
        set_level(logging.DEBUG)
        assert fresh_logger.level == logging.DEBUG

    def test_changes_handler_level(self, fresh_logger):
        setup_console(level=logging.INFO)
        set_level(logging.DEBUG)
        for h in _owned_handlers(fresh_logger):
            assert h.level == logging.DEBUG

    def test_accepts_string_level(self, fresh_logger):
        setup_console()
        set_level("WARNING")
        assert fresh_logger.level == logging.WARNING


# ---------------------------------------------------------------------------
# End-to-end smoke
# ---------------------------------------------------------------------------
def test_end_to_end_console_plus_file(fresh_logger, tmp_path: Path, capsys):
    """Configure both handlers, emit a message, verify both received it."""
    log_path = tmp_path / "fastmdxplora.log"
    setup_console()
    log = attach_file_logger(log_path)
    log.info("hello world")

    # Console output via the captured stream
    captured = capsys.readouterr()
    assert "hello world" in captured.out

    # File output
    for h in log.handlers:
        h.flush()
    assert "hello world" in log_path.read_text(encoding="utf-8")
