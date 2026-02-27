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
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

PACKAGE_LOGGER_NAME = "autoskillit"


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
    if name is not None:
        return structlog.get_logger().bind(logger=name)
    return structlog.get_logger()


def configure_logging(
    level: int = logging.INFO,
    json_output: bool = False,
    stream: Any = None,
) -> None:
    """Configure structlog and stdlib logging for application/server use.

    Call ONCE from the CLI's serve command before FastMCP starts. Never
    call from library code paths.

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
        final_processors: list[Any] = [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        final_processors = [structlog.dev.ConsoleRenderer()]

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
