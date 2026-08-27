"""Diagnostics/observability dataclasses.

Owns: ``DiagnosticsConfig``, ``LinuxTracingConfig`` (with the pytest-frame guard
that prevents writing to ``/dev/shm`` under pytest), ``LoggingConfig``,
``McpResponseConfig``, ``OutputBudgetConfig`` (with the cross-validation that
keeps response/page byte limits aligned), and ``TokenUsageConfig`` (the
telemetry verbosity toggle for token-usage emission).
"""

from __future__ import annotations

import inspect
import os
from dataclasses import dataclass

from autoskillit.core import (
    RECIPE_RESPONSE_DEFAULT_BYTES,
    RECIPE_RESPONSE_MAX_UTF8_BYTES,
    RECIPE_SECTION_RESPONSE_FLOOR_BYTES,
    Utf8ByteLimit,
)


@dataclass
class TokenUsageConfig:
    verbosity: str = "summary"  # "summary" | "none"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    json_output: bool | None = None  # None = auto-detect from stderr.isatty()


@dataclass
class DiagnosticsConfig:
    pipeline_health: bool = False


@dataclass
class LinuxTracingConfig:
    enabled: bool = True
    proc_interval: float = 5.0
    log_dir: str = ""  # empty = platform default (~/.local/share/autoskillit/logs on Linux)
    tmpfs_path: str = "/dev/shm"  # RAM-backed tmpfs for crash-resilient streaming
    max_sessions: int = 2000

    def __post_init__(self) -> None:
        if self.tmpfs_path != "/dev/shm" or not os.environ.get("PYTEST_CURRENT_TEST"):
            return
        # Only raise when called directly from test code — not from library machinery
        # (e.g. AutomationConfig default_factory, from_dynaconf). We inspect the call
        # frame two levels up: __post_init__ → __init__ (generated) → actual caller.
        frame = inspect.currentframe()
        init_frame = frame.f_back if frame is not None else None
        caller = init_frame.f_back if init_frame is not None else None
        if caller is not None and "/tests/" in (caller.f_code.co_filename or ""):
            raise RuntimeError(
                "LinuxTracingConfig.tmpfs_path is '/dev/shm' but PYTEST_CURRENT_TEST "
                "is set — this test would write to the real shared tmpfs and pollute "
                "production state. Override tmpfs_path with a test-local path, e.g.: "
                "LinuxTracingConfig(tmpfs_path=str(tmp_path)). "
                "Use the isolated_tracing_config fixture for new tests."
            )
        del frame, init_frame, caller


@dataclass
class McpResponseConfig:
    alert_threshold_tokens: int = 2000


@dataclass
class OutputBudgetConfig:
    """Model-context output limits.

    ``*_chars`` values bound human-readable previews. ``response_max_bytes``
    bounds the compact serialized handler payload; it is neither a tokenizer
    estimate nor a JSON-RPC envelope limit.
    """

    inline_max_chars: int = 5000
    head_chars: int = 2500
    tail_chars: int = 2500
    response_max_bytes: Utf8ByteLimit = Utf8ByteLimit(RECIPE_RESPONSE_DEFAULT_BYTES)
    page_max_bytes: Utf8ByteLimit | None = Utf8ByteLimit(RECIPE_RESPONSE_MAX_UTF8_BYTES)
    guard_enabled: bool = True
    shell_max_inline_bytes: int = 12_000
    capture_capacity: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.response_max_bytes < RECIPE_SECTION_RESPONSE_FLOOR_BYTES:
            raise ValueError(
                f"response_max_bytes must be at least {RECIPE_SECTION_RESPONSE_FLOOR_BYTES} bytes"
            )
        if (
            self.page_max_bytes is not None
            and self.page_max_bytes < RECIPE_SECTION_RESPONSE_FLOOR_BYTES
        ):
            raise ValueError(
                f"page_max_bytes must be at least {RECIPE_SECTION_RESPONSE_FLOOR_BYTES} bytes"
            )
        if (
            self.page_max_bytes is not None
            and self.page_max_bytes > RECIPE_RESPONSE_MAX_UTF8_BYTES
        ):
            raise ValueError(
                f"page_max_bytes must not exceed {RECIPE_RESPONSE_MAX_UTF8_BYTES} bytes"
            )
        if self.page_max_bytes is not None and self.response_max_bytes > self.page_max_bytes:
            raise ValueError(
                f"response_max_bytes ({self.response_max_bytes}) must not exceed "
                f"page_max_bytes ({self.page_max_bytes})"
            )
