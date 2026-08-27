"""Execution session dataclass.

Owns ``RunSkillConfig`` and its ``_EXIT_GRACE_BUFFER_MS`` ClassVar, the
``output_format`` derived property, and the ``__post_init__`` invariants that
keep natural-exit grace aligned with exit-after-stop delay.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import ClassVar

from autoskillit.core import OutputFormat


@dataclass
class QuotaGuardConfig:
    enabled: bool = True
    short_window_enabled: bool = True
    long_window_enabled: bool = True
    short_window_threshold: float = 85.0
    long_window_threshold: float = 95.0
    long_window_patterns: list[str] = field(
        default_factory=lambda: ["seven_day", "sonnet", "opus"]
    )
    buffer_seconds: int = 60
    cache_max_age: int = 300
    cache_refresh_interval: int = 240
    credentials_path: str = "~/.claude/.credentials.json"
    cache_path: str = "~/.claude/autoskillit_quota_cache.json"


@dataclass
class RunSkillConfig:
    timeout: int = 7200
    stale_threshold: int = 1200  # 20 minutes
    completion_marker: str = "%%ORDER_UP%%"
    completion_drain_timeout: float = 5.0
    exit_after_stop_delay_ms: int = 2000
    natural_exit_grace_seconds: float = 3.0
    idle_output_timeout: int = 1000
    max_suppression_seconds: int = 1800
    stream_idle_timeout_ms: int = 600000
    mcp_tool_timeout_sec: float = 14364.0
    completion_child_deferral_ceiling_seconds: float = 120.0

    # Safety margin (ms) above exit_after_stop_delay_ms that
    # natural_exit_grace_seconds must cover so the drain window can absorb
    # the CLI self-exit delay without a race.
    _EXIT_GRACE_BUFFER_MS: ClassVar[int] = 500

    def __post_init__(self) -> None:
        if self.timeout <= 0:
            raise ValueError(f"timeout={self.timeout} must be > 0.")
        if self.stale_threshold < 0:
            raise ValueError(f"stale_threshold={self.stale_threshold} must be >= 0.")
        if self.idle_output_timeout < 0:
            raise ValueError(f"idle_output_timeout={self.idle_output_timeout} must be >= 0.")
        if self.max_suppression_seconds < 0:
            raise ValueError(
                f"max_suppression_seconds={self.max_suppression_seconds} must be >= 0."
            )
        mcp_timeout = self.mcp_tool_timeout_sec
        if (
            not isinstance(mcp_timeout, (int, float))
            or isinstance(mcp_timeout, bool)
            or not math.isfinite(mcp_timeout)
            or mcp_timeout <= 0
        ):
            raise ValueError(
                f"mcp_tool_timeout_sec={mcp_timeout} must be a finite positive number of seconds."
            )
        if self.stream_idle_timeout_ms < 0:
            raise ValueError(
                f"stream_idle_timeout_ms={self.stream_idle_timeout_ms} must be >= 0 "
                "(use 0 to disable injection)."
            )
        if self.completion_child_deferral_ceiling_seconds < 0:
            raise ValueError(
                f"completion_child_deferral_ceiling_seconds="
                f"{self.completion_child_deferral_ceiling_seconds} must be >= 0."
            )
        required_ms = self.exit_after_stop_delay_ms + self._EXIT_GRACE_BUFFER_MS
        # Convert seconds → ms for the comparison
        if self.natural_exit_grace_seconds * 1000 < required_ms:
            raise ValueError(
                f"natural_exit_grace_seconds={self.natural_exit_grace_seconds} is too small: "
                f"{self.natural_exit_grace_seconds * 1000:.0f}ms < "
                f"{required_ms}ms (exit_after_stop_delay_ms + {self._EXIT_GRACE_BUFFER_MS}). "
                "Increase natural_exit_grace_seconds so the drain window can absorb the "
                "CLI self-exit delay."
            )

    @property
    def output_format(self) -> OutputFormat:
        """Derived from feature requirements — not independently configurable."""
        return OutputFormat.derive(completion_marker=self.completion_marker)
