"""Backend-neutral startup-readiness contracts."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Protocol


class ObserverStatus(StrEnum):
    """Typed outcomes from a guarded startup-readiness adapter."""

    READY = "ready"
    ABSENT = "absent"
    LOCKED = "locked"
    CORRUPT = "corrupt"
    INCOMPLETE = "incomplete"
    SCHEMA_CHANGED = "schema_changed"
    UNSUPPORTED_VERSION = "unsupported_version"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class ReadinessProbe(Protocol):
    """Backend-owned readiness adapter consumed by generic observers."""

    def check(self) -> ObserverStatus:
        """Perform one non-blocking readiness observation."""
        ...

    def wait(
        self,
        *,
        timeout_seconds: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> ObserverStatus:
        """Wait within a bounded interval for a terminal readiness outcome."""
        ...


__all__ = ["ObserverStatus", "ReadinessProbe"]
