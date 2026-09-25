"""Unit tests for the structured-logging helper (backlog item 9)."""

import io
import logging

import pytest

from uc_sync import logging_util as lg


@pytest.fixture(autouse=True)
def _reset_logging():
    """Isolate each test: fresh context, no secrets, detached handlers."""
    lg._CONTEXT.clear()
    lg._CONTEXT.update({"run_id": "-", "stage": "-"})
    lg._SECRETS.clear()
    app = logging.getLogger(lg.APP_LOGGER_NAME)
    lg._remove_our_handlers(app)
    lg._BUFFER = None
    yield
    lg._remove_our_handlers(app)
    lg._SECRETS.clear()
    lg._BUFFER = None


def test_get_log_returns_child_of_app_logger():
    log = lg.get_log("uc_sync.package_import")
    assert log.name == "uc_sync.package_import"
    # A descendant of the app logger, so it reaches the app logger's handlers.
    assert log.name.startswith(lg.APP_LOGGER_NAME + ".")


def test_get_log_rehomes_main_and_arbitrary_names():
    assert lg.get_log("__main__").name == "uc_sync.notebook"
    assert lg.get_log("01_inventory").name == "uc_sync.01_inventory"
    assert lg.get_log("").name == "uc_sync.notebook"


def test_configure_emits_run_id_and_stage_on_every_line():
    buf = lg.configure_logging(run_id="abc123", stage="IMPORT", capture=True)
    lg.get_log("uc_sync.mod").info("hello world")
    out = buf.getvalue()
    assert "run=abc123" in out
    assert "stage=IMPORT" in out
    assert "hello world" in out
    assert "[uc_sync.mod]" in out


def test_set_context_updates_subsequent_lines():
    buf = lg.configure_logging(run_id="", stage="INVENTORY", capture=True)
    log = lg.get_log("uc_sync.mod")
    log.info("before")
    lg.set_context(run_id="run9", stage="EXPORT")
    log.info("after")
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert "run=- " in lines[0] or "run=-" in lines[0]
    assert "run=run9" in lines[1]
    assert "stage=EXPORT" in lines[1]


def test_no_double_emit_propagate_false():
    """App logger must not propagate to root (would double-print in notebooks)."""
    lg.configure_logging(run_id="r", stage="IMPORT", capture=True)
    app = logging.getLogger(lg.APP_LOGGER_NAME)
    assert app.propagate is False
    # Both of this module's own handlers are attached (ignore any pytest-injected one).
    ours = {h.get_name() for h in app.handlers if h.get_name() in
            (lg._STREAM_HANDLER_NAME, lg._BUFFER_HANDLER_NAME)}
    assert ours == {lg._STREAM_HANDLER_NAME, lg._BUFFER_HANDLER_NAME}


def test_reconfigure_is_idempotent_no_handler_duplication():
    lg.configure_logging(run_id="r", stage="IMPORT", capture=True)
    lg.configure_logging(run_id="r", stage="IMPORT", capture=True)
    app = logging.getLogger(lg.APP_LOGGER_NAME)
    stream_handlers = [h for h in app.handlers if h.get_name() == lg._STREAM_HANDLER_NAME]
    buffer_handlers = [h for h in app.handlers if h.get_name() == lg._BUFFER_HANDLER_NAME]
    assert len(stream_handlers) == 1
    assert len(buffer_handlers) == 1


def test_secret_is_redacted():
    buf = lg.configure_logging(run_id="r", stage="IMPORT", capture=True)
    lg.register_secret("supersecretvalue")
    lg.get_log("uc_sync.mod").info("connecting with token=supersecretvalue done")
    out = buf.getvalue()
    assert "supersecretvalue" not in out
    assert "***REDACTED***" in out


def test_short_secret_not_registered():
    lg.register_secret("ab")  # too short — would redact everywhere
    assert "ab" not in lg._SECRETS


def test_log_level_filters_debug_by_default():
    buf = lg.configure_logging(run_id="r", stage="IMPORT", level="INFO", capture=True)
    log = lg.get_log("uc_sync.mod")
    log.debug("verbose detail")
    log.info("info line")
    out = buf.getvalue()
    assert "verbose detail" not in out
    assert "info line" in out


def test_debug_level_opt_in():
    buf = lg.configure_logging(run_id="r", stage="IMPORT", level="DEBUG", capture=True)
    lg.get_log("uc_sync.mod").debug("verbose detail")
    assert "verbose detail" in buf.getvalue()


def test_exception_captures_traceback():
    buf = lg.configure_logging(run_id="r", stage="IMPORT", capture=True)
    log = lg.get_log("uc_sync.mod")
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("operation failed")
    out = buf.getvalue()
    assert "operation failed" in out
    assert "Traceback (most recent call last)" in out
    assert "ValueError: boom" in out


def test_get_captured_log_matches_buffer():
    buf = lg.configure_logging(run_id="r", stage="IMPORT", capture=True)
    lg.get_log("uc_sync.mod").info("captured line")
    assert lg.get_captured_log() == buf.getvalue()
    assert "captured line" in lg.get_captured_log()


def test_capture_off_returns_none():
    buf = lg.configure_logging(run_id="r", stage="IMPORT", capture=False)
    assert buf is None
    assert lg.get_captured_log() == ""
    app = logging.getLogger(lg.APP_LOGGER_NAME)
    ours = {h.get_name() for h in app.handlers if h.get_name() in
            (lg._STREAM_HANDLER_NAME, lg._BUFFER_HANDLER_NAME)}
    assert ours == {lg._STREAM_HANDLER_NAME}


def test_custom_stream_receives_output():
    stream = io.StringIO()
    lg.configure_logging(run_id="r", stage="IMPORT", capture=False, stream=stream)
    lg.get_log("uc_sync.mod").warning("to custom stream")
    assert "to custom stream" in stream.getvalue()
    assert "WARNING" in stream.getvalue()
