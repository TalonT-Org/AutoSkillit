"""Execution-scoped result dataclasses.

Narrow-cascade peer of _type_results.py. These types are consumed primarily by
execution/, server/, and pipeline/ — not by workspace/, recipe/, migration/, or
the root-level utility modules. Splitting them here means changes cascade to
4 test directories instead of 13. ProviderOutcome lives in _type_results.py
because SkillResult.provider references it (universal consumer surface).

Zero autoskillit imports outside this sub-package (IL-0).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._type_constants import KNOWN_CI_EVENTS
from ._type_execution_identity import ExecutionIdentity

__all__ = [
    "SessionTelemetry",
    "RecipeIdentity",
    "CIRunScope",
]


@dataclass(frozen=True, slots=True)
class SessionTelemetry:
    """Typed bundle of all per-session telemetry fields passed to flush_session_log.

    Legacy telemetry fields are required so omissions remain visible. Execution
    identity has an explicit empty sentinel for callers that do not launch a
    specialized parent/child execution.
    """

    token_usage: dict[str, Any] | None
    timing_seconds: float | None
    audit_record: dict[str, Any] | None
    github_api_usage: dict[str, Any] | None
    github_api_requests: int
    loc_insertions: int
    loc_deletions: int
    execution_identity: ExecutionIdentity = ExecutionIdentity.empty()

    @classmethod
    def empty(cls) -> SessionTelemetry:
        """Zero-value sentinel for error paths where no telemetry is available."""
        return cls(
            token_usage=None,
            timing_seconds=None,
            audit_record=None,
            github_api_usage=None,
            github_api_requests=0,
            loc_insertions=0,
            loc_deletions=0,
            execution_identity=ExecutionIdentity.empty(),
        )


@dataclass(frozen=True, slots=True)
class RecipeIdentity:
    """Typed bundle of recipe identification fields for session logging.

    All fields required — prevents silent empty-string drift when new recipe
    fields are added to flush_session_log but not wired from callers.
    """

    name: str
    content_hash: str
    composite_hash: str
    version: str

    @classmethod
    def empty(cls) -> RecipeIdentity:
        """Sentinel for sessions not driven by a recipe."""
        return cls(name="", content_hash="", composite_hash="", version="")


@dataclass(frozen=True, slots=True)
class CIRunScope:
    """Immutable scope parameters that uniquely identify which CI workflow runs are relevant.

    Passed as a single argument through the CIWatcher protocol so that adding a new
    scope axis requires changing only this dataclass and the API params builder —
    not every method signature in the call chain.
    """

    workflow: str | None = None
    head_sha: str | None = None
    event: str | None = None

    def __post_init__(self) -> None:
        if self.event is not None and self.event not in KNOWN_CI_EVENTS:
            raise ValueError(
                f"Invalid CI event {self.event!r}. Valid events: {sorted(KNOWN_CI_EVENTS)}"
            )
