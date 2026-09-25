"""Structured logging for the UC governance-migration utility (backlog item 9).

One named application logger (``uc_sync``) with per-module **child** loggers obtained
via :func:`get_log` (``logging.getLogger("uc_sync").getChild(...)``), so every line
carries the emitting module in ``%(name)s``. Handlers (a stdout ``StreamHandler`` and an
optional in-memory ``StringIO`` buffer) attach to the app logger only; children
propagate up to it, and the app logger has ``propagate = False`` so lines are not
re-emitted by the notebook's root logger (which usually already has a handler → double
prints).

Every record carries ``run_id`` and ``stage`` (INVENTORY / EXPORT / IMPORT) via a filter
reading a mutable module-level context, so ``get_log(__name__)`` can be called at import
time (before the run id exists) and the context is filled in later by
:func:`configure_logging` / :func:`set_context`. The optional buffer captures the whole
run's log into a string that the notebooks write alongside the report on the volume, so a
handed-over artifact already includes the logs.

Secrets registered with :func:`register_secret` are scrubbed from every emitted line.

Design notes (backlog item 9): this is the Python standard ``logging`` module. The
"full stack trace" benefit comes from calling ``log.exception(msg)`` (or
``log.error(msg, exc_info=True)``) inside an ``except`` block — never a bare
``print(str(exc))``.
"""

from __future__ import annotations

import io
import logging
import sys
from typing import Optional

APP_LOGGER_NAME = "uc_sync"

# Line format: timestamp, level, logger name, run_id, stage, message — greppable, with
# run_id + stage on every line.
_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] run=%(run_id)s stage=%(stage)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_BUFFER_HANDLER_NAME = "uc_sync_buffer"
_STREAM_HANDLER_NAME = "uc_sync_stream"

# Mutable per-run context injected into every record by ``_ContextFilter``. Defaults keep
# the columns aligned before ``configure_logging``/``set_context`` runs.
_CONTEXT: dict[str, str] = {"run_id": "-", "stage": "-"}

# Registered secret values scrubbed from every line (client secrets, tokens).
_SECRETS: set[str] = set()

# Holds the active in-memory capture buffer (set by ``configure_logging(capture=True)``).
_BUFFER: Optional[io.StringIO] = None


def _level(level: object) -> int:
    """Resolve a level name/number to a ``logging`` level int (default INFO)."""
    if isinstance(level, int):
        return level
    name = str(level or "INFO").strip().upper()
    return getattr(logging, name, logging.INFO)


class _ContextFilter(logging.Filter):
    """Inject the current ``run_id`` and ``stage`` onto every record."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.run_id = _CONTEXT.get("run_id", "-")
        record.stage = _CONTEXT.get("stage", "-")
        return True


class _RedactFilter(logging.Filter):
    """Scrub any registered secret value from the fully-rendered message.

    Rendering the message here (rather than mutating ``record.msg``) keeps lazy
    ``%`` formatting intact for records with no secret in them, and guarantees the
    scrub is applied to both handlers identically.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if not _SECRETS:
            return True
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - defensive; never drop a log line
            return True
        redacted = msg
        for secret in _SECRETS:
            if secret and secret in redacted:
                redacted = redacted.replace(secret, "***REDACTED***")
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


def register_secret(value: Optional[str]) -> None:
    """Register a secret value to be scrubbed from all subsequent log lines.

    Only non-trivial values are tracked (a 1-2 char "secret" would redact everywhere).
    """
    if value and len(str(value)) >= 4:
        _SECRETS.add(str(value))


def set_context(*, run_id: Optional[str] = None, stage: Optional[str] = None) -> None:
    """Update the run_id / stage stamped on every subsequent line."""
    if run_id is not None and str(run_id).strip():
        _CONTEXT["run_id"] = str(run_id).strip()
    if stage is not None and str(stage).strip():
        _CONTEXT["stage"] = str(stage).strip()


def _make_handler(handler: logging.Handler, name: str) -> logging.Handler:
    handler.set_name(name)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    handler.addFilter(_ContextFilter())
    handler.addFilter(_RedactFilter())
    return handler


def _remove_our_handlers(app: logging.Logger) -> None:
    """Remove handlers this module previously attached (idempotent re-configure)."""
    for h in list(app.handlers):
        if h.get_name() in (_STREAM_HANDLER_NAME, _BUFFER_HANDLER_NAME):
            app.removeHandler(h)


def configure_logging(
    *,
    run_id: str = "",
    stage: str = "",
    level: object = "INFO",
    capture: bool = True,
    stream=None,
) -> Optional[io.StringIO]:
    """Configure the ``uc_sync`` app logger once per run and return the capture buffer.

    Idempotent: re-invoking replaces this module's own handlers (never duplicates them)
    and never touches handlers added elsewhere. Returns the ``StringIO`` capture buffer
    when ``capture`` is true (else ``None``), so the caller can write the run's full log
    alongside the report.
    """
    global _BUFFER
    app = logging.getLogger(APP_LOGGER_NAME)
    app.setLevel(_level(level))
    # In Databricks notebooks the root logger already has a handler, so without this
    # every line prints twice (backlog item 9, tweak 1).
    app.propagate = False
    set_context(run_id=run_id, stage=stage)

    _remove_our_handlers(app)

    app.addHandler(_make_handler(logging.StreamHandler(stream or sys.stdout), _STREAM_HANDLER_NAME))

    _BUFFER = None
    if capture:
        _BUFFER = io.StringIO()
        app.addHandler(_make_handler(logging.StreamHandler(_BUFFER), _BUFFER_HANDLER_NAME))
    return _BUFFER


def get_log(name: str) -> logging.Logger:
    """Return the child logger for a module (``get_log(__name__)``).

    Names already under ``uc_sync`` are returned as-is (they are descendants of the app
    logger and propagate to its handlers); ``__main__`` / anything else is re-homed under
    the app logger so notebook code shares the same handlers and format.
    """
    n = str(name or "").strip()
    if n == APP_LOGGER_NAME or n.startswith(APP_LOGGER_NAME + "."):
        return logging.getLogger(n)
    if n in ("", "__main__", "__mp_main__"):
        n = "notebook"
    return logging.getLogger(APP_LOGGER_NAME).getChild(n)


def get_log_buffer() -> Optional[io.StringIO]:
    """The active capture buffer, if ``configure_logging(capture=True)`` was called."""
    return _BUFFER


def get_captured_log() -> str:
    """The full captured run log as a string (empty when capture is off)."""
    return _BUFFER.getvalue() if _BUFFER is not None else ""
