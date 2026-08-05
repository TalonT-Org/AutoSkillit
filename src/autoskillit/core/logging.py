"""Centralized structlog configuration for the autoskillit package.

Zero autoskillit imports. get_logger() is the single import point for all production
modules. configure_logging() is called once at CLI startup.

Library contract:
    Modules import get_logger() from here. Never call structlog.configure()
    or import logging directly in production modules outside this file.

Application contract:
    The CLI's serve command calls configure_logging() once before the MCP
    server starts. Before that call, the stdlib NullHandler in __init__.py
    suppresses all output. After it, structured output goes to stderr only.

MCP server constraint:
    stdout is the MCP protocol wire. Logging MUST go to stderr exclusively.
    configure_logging() enforces this — it always routes to sys.stderr.

Exception-rendering contract:
    Every processor chain configured here — the module-import-time default
    below and configure_logging()'s console branch — renders exceptions with
    structlog.dev.plain_traceback (wraps the stdlib `traceback` module,
    imported eagerly at module load), never structlog's default
    RichTracebackFormatter. Rich's exception formatter lazily imports
    submodules (e.g. rich._emoji_codes) on first use; autoskillit's own
    self-update deletes and rebuilds its installed package tree mid-process
    (issue #4469), so an except handler that reaches for a not-yet-imported
    rich submodule from a since-deleted site-packages tree crashes a SECOND
    time inside the crash handler itself. This is a crash class here, not a
    cosmetic choice — never reintroduce a rich-capable exception formatter
    into either processor chain.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from typing import Any, Protocol

import structlog

PACKAGE_LOGGER_NAME = "autoskillit"
_PLUGIN_ARTIFACT_ACTIONS = frozenset(
    {
        "acquire",
        "release",
        "publish",
        "repair",
        "retire",
        "cancel_retirement",
        "reclaim",
    }
)
_PLUGIN_ARTIFACT_OUTCOMES = frozenset(
    {
        "succeeded",
        "deferred_contended",
        "deferred_io_error",
        "rejected_identity",
        "failed_validation",
    }
)

# The single sanctioned exception formatter for every processor chain this
# module configures — see the module docstring's exception-rendering
# contract. Defined once and reused so the two configure sites can never
# drift apart.
_EXCEPTION_FORMATTER = structlog.dev.plain_traceback

# Ensure all module-level get_logger() calls return lazy proxies rather than
# fully-resolved loggers.  Without this, loggers created before
# configure_logging() bind to stdout + ConsoleRenderer (structlog defaults),
# which fatally corrupts the MCP stdio transport.
#
# processors= is explicit (not left to structlog's internal default) so this
# pre-configure chain also renders through _EXCEPTION_FORMATTER: every
# autoskillit path that logs before configure_logging() runs — including the
# entire update transaction, which runs ahead of any configure_logging()
# call (see cli/app.py's main()) — is covered by the crash-proof contract
# from the very first log call, not just after CLI startup.
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.dev.ConsoleRenderer(exception_formatter=_EXCEPTION_FORMATTER),
    ],
    cache_logger_on_first_use=True,
    logger_factory=structlog.WriteLoggerFactory(file=sys.stderr),
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)


def get_logger(name: str | None = None) -> Any:
    """Return a structlog BoundLogger for the given module name.

    Usage in every autoskillit module:
        from autoskillit.core.logging import get_logger
        logger = get_logger(__name__)

    The name argument should always be __name__, which creates a logger
    named e.g. "autoskillit.server" that participates in the package
    logger hierarchy.

    The name is bound as a structured field so it appears in every log
    record emitted through this logger — regardless of the configured
    renderer (JSON, ConsoleRenderer, or testing capture).
    """
    logger = structlog.get_logger()
    if name is not None:
        # Why _initial_values instead of .bind(): .bind() resolves the lazy proxy
        # into a concrete BoundLoggerFilteringAtNotset, freezing the current config.
        # We need to keep the proxy lazy so it resolves against the config active at
        # first log call (after configure_logging() runs).
        # Why not structlog.get_logger(logger=name): the "logger" kwarg collides with
        # wrap_logger()'s first positional parameter, raising TypeError.
        logger._initial_values["logger"] = name
    return logger


def log_plugin_artifact_lifecycle(
    logger: Any,
    *,
    action: str,
    outcome: str,
    artifact_kind: str,
    semantic_key: str,
    incarnation: str,
    not_before: datetime | None = None,
    contention_detail: str | None = None,
    child_pid: int | None = None,
) -> None:
    """Emit the single schema used for plugin artifact lifecycle events."""
    if action not in _PLUGIN_ARTIFACT_ACTIONS:
        raise ValueError(f"unsupported plugin artifact lifecycle action: {action}")
    if outcome not in _PLUGIN_ARTIFACT_OUTCOMES:
        raise ValueError(f"unsupported plugin artifact lifecycle outcome: {outcome}")
    emit = logger.info if outcome == "succeeded" else logger.warning
    emit(
        "plugin_artifact_lifecycle",
        action=action,
        outcome=outcome,
        actor_pid=os.getpid(),
        child_pid=child_pid,
        artifact_kind=artifact_kind,
        semantic_key=semantic_key,
        incarnation=incarnation,
        not_before=not_before.isoformat() if not_before is not None else None,
        contention_detail=contention_detail,
    )


class _LeaseOwner(Protocol):
    @property
    def inherited_fds(self) -> tuple[int, ...]: ...

    @property
    def closed(self) -> bool: ...

    def close(self) -> None: ...


class PluginArtifactLifecycleLease:
    """Close-only lease wrapper that emits one release event."""

    __slots__ = (
        "_artifact_kind",
        "_incarnation",
        "_lease",
        "_logger",
        "_semantic_key",
    )

    def __init__(
        self,
        lease: _LeaseOwner,
        *,
        logger: Any,
        artifact_kind: str,
        semantic_key: str,
        incarnation: str,
    ) -> None:
        self._lease = lease
        self._logger = logger
        self._artifact_kind = artifact_kind
        self._semantic_key = semantic_key
        self._incarnation = incarnation

    @property
    def closed(self) -> bool:
        return bool(self._lease.closed)

    @property
    def inherited_fds(self) -> tuple[int, ...]:
        return self._lease.inherited_fds

    def close(self) -> None:
        if self.closed:
            return
        self._lease.close()
        log_plugin_artifact_lifecycle(
            self._logger,
            action="release",
            outcome="succeeded",
            artifact_kind=self._artifact_kind,
            semantic_key=self._semantic_key,
            incarnation=self._incarnation,
        )


def configure_logging(
    level: int = logging.INFO,
    json_output: bool = False,
    stream: Any = None,
) -> None:
    """Configure structlog and stdlib logging for application/server use.

    Call at CLI startup; may be called again after config load for two-phase
    boot (early init at INFO, then reconfigure with config-derived level).
    Never call from library code paths.

    Args:
        level: Minimum log level (e.g. logging.INFO, logging.DEBUG).
        json_output: True for JSON lines (production/container), False for
            human-readable ConsoleRenderer (TTY / development). When False
            and stream is a TTY, colors are enabled automatically.
        stream: Output stream. Defaults to sys.stderr. Must never be
            sys.stdout — stdout is the MCP protocol wire.
    """
    if stream is None:
        stream = sys.stderr

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
    ]

    is_tty = getattr(stream, "isatty", lambda: False)()
    use_json = json_output or not is_tty

    if use_json:
        # dict_tracebacks renders via the stdlib traceback module (structured
        # frame dicts, not text) — rich-free by construction, so it is not
        # subject to the exception-rendering contract above and needs no
        # exception_formatter.
        final_processors: list[Any] = [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        final_processors = [
            structlog.dev.ConsoleRenderer(exception_formatter=_EXCEPTION_FORMATTER)
        ]

    structlog.configure(
        processors=shared_processors + final_processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        # WriteLoggerFactory performs a single atomic write() per record.
        # PrintLoggerFactory (the default) uses two syscalls (message + newline)
        # which interleave with the stdlib StreamHandler on the same stderr fd.
        logger_factory=structlog.WriteLoggerFactory(file=stream),
        cache_logger_on_first_use=True,
    )

    # Also configure stdlib so FastMCP's internal logging routes to stderr.
    # FastMCP manages its own "fastmcp.*" logger namespace separately;
    # this only affects the autoskillit logger for third-party libraries.
    pkg_logger = logging.getLogger(PACKAGE_LOGGER_NAME)  # noqa: TID251
    pkg_logger.handlers.clear()
    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    pkg_logger.addHandler(handler)
    pkg_logger.setLevel(level)
    pkg_logger.propagate = False
