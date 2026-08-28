"""Structured provider-failure evidence retained on headless skill results."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["ApiFailureOutcome", "RateLimitWindow"]


@dataclass(frozen=True, slots=True)
class RateLimitWindow:
    """Observed provider rate-limit window evidence."""

    status: str = ""
    limit_type: str = ""
    resets_at_epoch: int | None = None


@dataclass(frozen=True, slots=True)
class ApiFailureOutcome:
    """Structured provider-failure evidence retained from a session."""

    status: int | None = None
    terminal_reason: str = ""
    error_code: str = ""
    api_error_message_seen: bool = False
    rate_limit: RateLimitWindow = field(default_factory=RateLimitWindow)
