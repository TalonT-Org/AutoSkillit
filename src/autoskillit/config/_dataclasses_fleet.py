"""Fleet dispatch and process-tether dataclasses.

Owns: ``FleetConfig`` (with the ``validate`` method that enforces
``max_concurrent_dispatches <= _MAX_CONCURRENT_DISPATCHES`` and the other
fleet field invariants), ``ProcessTetherConfig`` (with its own ``validate``
method), and the hard ceiling constant ``_MAX_CONCURRENT_DISPATCHES`` that
``FleetConfig.validate`` references.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_MAX_CONCURRENT_DISPATCHES = 8


@dataclass
class FleetConfig:
    default_timeout_sec: int = 3600
    max_concurrent_dispatches: int = 3  # default; ceiling is _MAX_CONCURRENT_DISPATCHES
    max_total_issues: int = 12
    enable_deadline_extension: bool = True
    max_extension_seconds: float = 7200
    idle_output_timeout: float = 1800
    acquire_timeout_sec: float = 300.0
    max_issues_per_food_truck: int = 3
    inspector_model: str = ""

    def validate(self, feature_enabled: bool) -> None:
        """Validate only when the feature is active."""
        if not feature_enabled:
            return

        self._validate_enabled_values()

    def _validate_enabled_values(self) -> None:
        if self.default_timeout_sec <= 0:
            raise ValueError(
                f"default_timeout_sec must be positive, got {self.default_timeout_sec}"
            )
        if self.max_concurrent_dispatches < 1:
            raise ValueError(
                f"max_concurrent_dispatches must be >= 1, got {self.max_concurrent_dispatches}"
            )
        if self.max_concurrent_dispatches > _MAX_CONCURRENT_DISPATCHES:
            raise ValueError(
                f"max_concurrent_dispatches must be <= {_MAX_CONCURRENT_DISPATCHES},"
                f" got {self.max_concurrent_dispatches}"
            )
        if self.max_total_issues < 1:
            raise ValueError(f"max_total_issues must be >= 1, got {self.max_total_issues}")
        if self.max_extension_seconds <= 0:
            raise ValueError(
                f"max_extension_seconds must be positive, got {self.max_extension_seconds}"
            )
        if self.idle_output_timeout < 0:
            raise ValueError(
                f"idle_output_timeout must be non-negative, got {self.idle_output_timeout}"
            )
        if self.acquire_timeout_sec <= 0:
            raise ValueError(
                f"acquire_timeout_sec must be positive, got {self.acquire_timeout_sec}"
            )
        if self.max_issues_per_food_truck < 1:
            raise ValueError(
                f"max_issues_per_food_truck must be >= 1, got {self.max_issues_per_food_truck}"
            )
        if self.max_issues_per_food_truck > self.max_total_issues:
            raise ValueError(
                f"max_issues_per_food_truck must be <= max_total_issues"
                f" ({self.max_total_issues}), got {self.max_issues_per_food_truck}"
            )


@dataclass
class ProcessTetherConfig:
    """Lifetime authority for interactive cooks and process-tethered children.

    ``cook_ceiling_seconds`` is the soft interactive lifetime. After it, the
    session may continue only while active, for at most
    ``cook_max_extension_seconds`` more. This object is the single authority
    consumed by ``run_cook_attempt``.

    The orphan default mirrors ``execution.process._process_tether``'s
    ``DEFAULT_TETHER_CEILING_SECONDS``. Config cannot import execution (IL-002),
    so a parity test in ``tests/execution/test_process_tether.py`` ties the
    literal to that constant.
    """

    orphan_ceiling_seconds: float = 86400.0
    cook_ceiling_seconds: float = 172800.0
    cook_max_extension_seconds: float = 86400.0
    # Optional, default-off kernel-enforced ceiling via
    # `systemd-run --user --scope`; defense-in-depth only, never the ceiling
    # of record — see docs/decisions/0010-systemd-scope-defense-in-depth.md
    # for the WSL2/linger/probe preconditions and why RuntimeMaxSec is
    # unreliable.
    systemd_scope_enabled: bool = False

    def validate(self) -> None:
        if not math.isfinite(self.orphan_ceiling_seconds) or self.orphan_ceiling_seconds <= 0:
            raise ValueError(
                "orphan_ceiling_seconds must be a positive finite number, "
                f"got {self.orphan_ceiling_seconds}"
            )
        if not math.isfinite(self.cook_ceiling_seconds) or self.cook_ceiling_seconds <= 0:
            raise ValueError(
                "cook_ceiling_seconds must be a positive finite number, "
                f"got {self.cook_ceiling_seconds}"
            )
        if (
            not math.isfinite(self.cook_max_extension_seconds)
            or self.cook_max_extension_seconds < 0
        ):
            raise ValueError(
                "cook_max_extension_seconds must be a non-negative finite number, "
                f"got {self.cook_max_extension_seconds}"
            )
