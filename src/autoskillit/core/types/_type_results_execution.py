"""Execution-scoped result dataclasses.

Narrow-cascade peer of _type_results.py. These types are consumed primarily by
execution/, server/, and pipeline/ — not by workspace/, recipe/, migration/, or
the root-level utility modules. Splitting them here means changes cascade to
4 test directories instead of 13. ProviderOutcome lives in _type_results.py
because SkillResult.provider references it (universal consumer surface).

Zero autoskillit imports outside this sub-package (IL-0).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, NotRequired, TypedDict, cast

from ._type_constants import KNOWN_CI_EVENTS
from ._type_execution_identity import ChildOutcomeDict, ExecutionIdentity
from ._type_token import TurnTokenEntry

__all__ = [
    "SubagentModelOutcomeDict",
    "ApiFailureOutcome",
    "RateLimitWindow",
    "ContinuationRecommendation",
    "ExecutionCandidateAttempt",
    "ExecutionSelection",
    "SessionTelemetry",
    "RecipeIdentity",
    "CIRunScope",
]


class SubagentModelOutcomeDict(TypedDict):
    model: str
    final_model: str
    model_swapped: bool
    agent_type: NotRequired[str]


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


@dataclass(frozen=True, slots=True)
class ContinuationRecommendation:
    """A bounded same-binding continuation decision for a terminal attempt.

    A recommendation either names the real child session that may be resumed,
    or records why no such continuation is available. It never authorizes a
    replay; dispatch must separately validate the stored launch binding.
    """

    resume_session_id: str | None
    reason: str | None
    reset_after_seconds: int
    remaining_deadline_seconds: int

    def __post_init__(self) -> None:
        has_resume_session = self.resume_session_id is not None
        has_reason = self.reason is not None
        if has_resume_session == has_reason:
            raise ValueError(
                "continuation recommendation requires exactly one of resume_session_id or reason"
            )
        if self.resume_session_id is not None and not self.resume_session_id.strip():
            raise ValueError("continuation recommendation resume_session_id must be nonempty")
        if self.reason is not None and not self.reason.strip():
            raise ValueError("continuation recommendation reason must be nonempty")
        if isinstance(self.reset_after_seconds, bool) or not isinstance(
            self.reset_after_seconds, int
        ):
            raise TypeError("continuation recommendation reset_after_seconds must be an integer")
        if not 0 <= self.reset_after_seconds <= 60:
            raise ValueError(
                "continuation recommendation reset_after_seconds must be between 0 and 60"
            )
        if isinstance(self.remaining_deadline_seconds, bool) or not isinstance(
            self.remaining_deadline_seconds, int
        ):
            raise TypeError(
                "continuation recommendation remaining_deadline_seconds must be an integer"
            )
        if has_resume_session:
            if self.remaining_deadline_seconds <= self.reset_after_seconds:
                raise ValueError(
                    "available continuation requires deadline remaining after the reset delay"
                )
        elif self.remaining_deadline_seconds < 0:
            raise ValueError(
                "unavailable continuation remaining_deadline_seconds cannot be negative"
            )

    def to_payload(self) -> dict[str, str | int | None]:
        """Return the canonical JSON representation."""
        return {
            "resume_session_id": self.resume_session_id,
            "reason": self.reason,
            "reset_after_seconds": self.reset_after_seconds,
            "remaining_deadline_seconds": self.remaining_deadline_seconds,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ContinuationRecommendation:
        """Strictly decode a canonical continuation recommendation."""
        expected_fields = {
            "resume_session_id",
            "reason",
            "reset_after_seconds",
            "remaining_deadline_seconds",
        }
        if set(payload) != expected_fields:
            raise ValueError("continuation recommendation payload is not canonical")
        resume_session_id = payload["resume_session_id"]
        reason = payload["reason"]
        reset_after_seconds = payload["reset_after_seconds"]
        remaining_deadline_seconds = payload["remaining_deadline_seconds"]
        if resume_session_id is not None and not isinstance(resume_session_id, str):
            raise ValueError(
                "continuation recommendation resume_session_id must be a string or null"
            )
        if reason is not None and not isinstance(reason, str):
            raise ValueError("continuation recommendation reason must be a string or null")
        if isinstance(reset_after_seconds, bool) or not isinstance(reset_after_seconds, int):
            raise ValueError("continuation recommendation reset_after_seconds must be an integer")
        if isinstance(remaining_deadline_seconds, bool) or not isinstance(
            remaining_deadline_seconds, int
        ):
            raise ValueError(
                "continuation recommendation remaining_deadline_seconds must be an integer"
            )
        return cls(
            resume_session_id=cast(str | None, resume_session_id),
            reason=cast(str | None, reason),
            reset_after_seconds=reset_after_seconds,
            remaining_deadline_seconds=remaining_deadline_seconds,
        )


@dataclass(frozen=True, slots=True)
class ExecutionCandidateAttempt:
    """One candidate's progress through selection, admission, and execution.

    Empty or ``None`` fields mean that the candidate did not reach that stage.
    Dispatch owns creating replacement snapshots as the candidate advances.
    """

    candidate_id: str = ""
    ordinal: int = 0
    attempt: int = 0
    parent_backend: str = ""
    parent_provider: str = ""
    parent_model: str = ""
    requested_backend: str = ""
    requested_provider: str = ""
    requested_model: str = ""
    effective_backend: str = ""
    effective_provider: str = ""
    effective_model: str = ""
    backend_source_path: str = ""
    provider_source_path: str = ""
    model_source_path: str = ""
    admission_status: str = ""
    admission_at_epoch: int | None = None
    admission_cache_status: str = ""
    admission_cache_age_seconds: float | None = None
    rate_limit_status: str = ""
    rate_limit_type: str = ""
    rate_limit_resets_at_epoch: int | None = None
    rejection_reason: str | None = None
    transition: str = ""
    execution_started: bool = False
    child_session_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Return the durable, JSON-serializable attempt evidence."""
        return {
            "candidate_id": self.candidate_id,
            "ordinal": self.ordinal,
            "attempt": self.attempt,
            "parent_backend": self.parent_backend,
            "parent_provider": self.parent_provider,
            "parent_model": self.parent_model,
            "requested_backend": self.requested_backend,
            "requested_provider": self.requested_provider,
            "requested_model": self.requested_model,
            "effective_backend": self.effective_backend,
            "effective_provider": self.effective_provider,
            "effective_model": self.effective_model,
            "backend_source_path": self.backend_source_path,
            "provider_source_path": self.provider_source_path,
            "model_source_path": self.model_source_path,
            "admission_status": self.admission_status,
            "admission_at_epoch": self.admission_at_epoch,
            "admission_cache_status": self.admission_cache_status,
            "admission_cache_age_seconds": self.admission_cache_age_seconds,
            "rate_limit_status": self.rate_limit_status,
            "rate_limit_type": self.rate_limit_type,
            "rate_limit_resets_at_epoch": self.rate_limit_resets_at_epoch,
            "rejection_reason": self.rejection_reason,
            "transition": self.transition,
            "execution_started": self.execution_started,
            "child_session_id": self.child_session_id,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ExecutionCandidateAttempt:
        """Strictly decode one persisted candidate attempt."""
        expected_fields = {
            "candidate_id",
            "ordinal",
            "attempt",
            "parent_backend",
            "parent_provider",
            "parent_model",
            "requested_backend",
            "requested_provider",
            "requested_model",
            "effective_backend",
            "effective_provider",
            "effective_model",
            "backend_source_path",
            "provider_source_path",
            "model_source_path",
            "admission_status",
            "admission_at_epoch",
            "admission_cache_status",
            "admission_cache_age_seconds",
            "rate_limit_status",
            "rate_limit_type",
            "rate_limit_resets_at_epoch",
            "rejection_reason",
            "transition",
            "execution_started",
            "child_session_id",
        }
        if set(payload) != expected_fields:
            raise ValueError("execution candidate attempt payload is not canonical")

        def require_str(field_name: str) -> str:
            value = payload[field_name]
            if not isinstance(value, str):
                raise ValueError(f"execution candidate attempt {field_name} must be a string")
            return value

        def require_int(field_name: str) -> int:
            value = payload[field_name]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"execution candidate attempt {field_name} must be an integer")
            return value

        def require_optional_int(field_name: str) -> int | None:
            value = payload[field_name]
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"execution candidate attempt {field_name} must be an integer or null"
                )
            return value

        def require_optional_str(field_name: str) -> str | None:
            value = payload[field_name]
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValueError(
                    f"execution candidate attempt {field_name} must be a string or null"
                )
            return value

        cache_age = payload["admission_cache_age_seconds"]
        if cache_age is not None and (
            isinstance(cache_age, bool) or not isinstance(cache_age, (int, float))
        ):
            raise ValueError(
                "execution candidate attempt admission_cache_age_seconds must be a number or null"
            )
        execution_started = payload["execution_started"]
        if not isinstance(execution_started, bool):
            raise ValueError("execution candidate attempt execution_started must be a boolean")

        return cls(
            candidate_id=require_str("candidate_id"),
            ordinal=require_int("ordinal"),
            attempt=require_int("attempt"),
            parent_backend=require_str("parent_backend"),
            parent_provider=require_str("parent_provider"),
            parent_model=require_str("parent_model"),
            requested_backend=require_str("requested_backend"),
            requested_provider=require_str("requested_provider"),
            requested_model=require_str("requested_model"),
            effective_backend=require_str("effective_backend"),
            effective_provider=require_str("effective_provider"),
            effective_model=require_str("effective_model"),
            backend_source_path=require_str("backend_source_path"),
            provider_source_path=require_str("provider_source_path"),
            model_source_path=require_str("model_source_path"),
            admission_status=require_str("admission_status"),
            admission_at_epoch=require_optional_int("admission_at_epoch"),
            admission_cache_status=require_str("admission_cache_status"),
            admission_cache_age_seconds=(float(cache_age) if cache_age is not None else None),
            rate_limit_status=require_str("rate_limit_status"),
            rate_limit_type=require_str("rate_limit_type"),
            rate_limit_resets_at_epoch=require_optional_int("rate_limit_resets_at_epoch"),
            rejection_reason=require_optional_str("rejection_reason"),
            transition=require_str("transition"),
            execution_started=execution_started,
            child_session_id=require_optional_str("child_session_id"),
        )


@dataclass(frozen=True, slots=True)
class ExecutionSelection:
    """Immutable candidate-selection evidence attached to a skill execution."""

    selection_id: str = ""
    campaign_id: str = ""
    attempts: tuple[ExecutionCandidateAttempt, ...] = ()
    terminal_candidate_id: str | None = None
    manifest_ref: str = ""
    invocation_deadline_epoch: int | None = None
    remaining_retry_budget: int | None = None
    backend_rerouted: bool = False
    candidate_fallback: bool = False
    provider_fallback: bool = False
    continuation: ContinuationRecommendation | None = None
    completed: bool = False

    def __post_init__(self) -> None:
        if self.selection_id and not self.manifest_ref:
            object.__setattr__(
                self,
                "manifest_ref",
                f"execution-candidates/{self.selection_id}.json",
            )
        if self.terminal_candidate_id is not None and self.terminal_attempt is None:
            raise ValueError("terminal_candidate_id must identify a recorded candidate attempt")
        if self.remaining_retry_budget is not None and (
            isinstance(self.remaining_retry_budget, bool)
            or not isinstance(self.remaining_retry_budget, int)
            or self.remaining_retry_budget < 0
        ):
            raise ValueError("remaining_retry_budget must be a nonnegative integer or null")
        if self.continuation is not None:
            if not isinstance(self.continuation, ContinuationRecommendation):
                raise TypeError("continuation must be a ContinuationRecommendation or null")
            if self.continuation.resume_session_id is not None:
                terminal_attempt = self.terminal_attempt
                if (
                    terminal_attempt is None
                    or not terminal_attempt.execution_started
                    or terminal_attempt.child_session_id != self.continuation.resume_session_id
                    or not terminal_attempt.effective_backend
                    or not terminal_attempt.effective_provider
                ):
                    raise ValueError(
                        "available continuation requires a bound, started terminal attempt"
                    )

    @property
    def terminal_attempt(self) -> ExecutionCandidateAttempt | None:
        """Return the selected terminal candidate, if dispatch recorded one."""
        if self.terminal_candidate_id is None:
            return None
        return next(
            (
                attempt
                for attempt in reversed(self.attempts)
                if attempt.candidate_id == self.terminal_candidate_id
            ),
            None,
        )

    def to_payload(self) -> dict[str, Any]:
        """Return the durable, JSON-serializable selection evidence."""
        return {
            "selection_id": self.selection_id,
            "campaign_id": self.campaign_id,
            "attempts": [attempt.to_payload() for attempt in self.attempts],
            "terminal_candidate_id": self.terminal_candidate_id,
            "manifest_ref": self.manifest_ref,
            "invocation_deadline_epoch": self.invocation_deadline_epoch,
            "remaining_retry_budget": self.remaining_retry_budget,
            "backend_rerouted": self.backend_rerouted,
            "candidate_fallback": self.candidate_fallback,
            "provider_fallback": self.provider_fallback,
            "continuation": (
                self.continuation.to_payload() if self.continuation is not None else None
            ),
            "completed": self.completed,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ExecutionSelection:
        """Strictly decode persisted execution-selection evidence."""
        expected_fields = {
            "selection_id",
            "campaign_id",
            "attempts",
            "terminal_candidate_id",
            "manifest_ref",
            "invocation_deadline_epoch",
            "remaining_retry_budget",
            "backend_rerouted",
            "candidate_fallback",
            "provider_fallback",
            "continuation",
            "completed",
        }
        if set(payload) != expected_fields:
            raise ValueError("execution selection payload is not canonical")

        def require_str(field_name: str) -> str:
            value = payload[field_name]
            if not isinstance(value, str):
                raise ValueError(f"execution selection {field_name} must be a string")
            return value

        def require_optional_str(field_name: str) -> str | None:
            value = payload[field_name]
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValueError(f"execution selection {field_name} must be a string or null")
            return value

        def require_optional_int(field_name: str) -> int | None:
            value = payload[field_name]
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"execution selection {field_name} must be an integer or null")
            return value

        def require_bool(field_name: str) -> bool:
            value = payload[field_name]
            if not isinstance(value, bool):
                raise ValueError(f"execution selection {field_name} must be a boolean")
            return value

        attempts = payload["attempts"]
        if not isinstance(attempts, list) or not all(
            isinstance(item, Mapping) for item in attempts
        ):
            raise ValueError("execution selection attempts must be an array of objects")
        continuation = payload["continuation"]
        if continuation is not None and not isinstance(continuation, Mapping):
            raise ValueError("execution selection continuation must be an object or null")
        selection_id = require_str("selection_id")
        manifest_ref = require_str("manifest_ref")
        if selection_id and manifest_ref != f"execution-candidates/{selection_id}.json":
            raise ValueError("execution selection manifest_ref does not match selection_id")
        return cls(
            selection_id=selection_id,
            campaign_id=require_str("campaign_id"),
            attempts=tuple(ExecutionCandidateAttempt.from_payload(item) for item in attempts),
            terminal_candidate_id=require_optional_str("terminal_candidate_id"),
            manifest_ref=manifest_ref,
            invocation_deadline_epoch=require_optional_int("invocation_deadline_epoch"),
            remaining_retry_budget=require_optional_int("remaining_retry_budget"),
            backend_rerouted=require_bool("backend_rerouted"),
            candidate_fallback=require_bool("candidate_fallback"),
            provider_fallback=require_bool("provider_fallback"),
            continuation=(
                ContinuationRecommendation.from_payload(continuation)
                if continuation is not None
                else None
            ),
            completed=require_bool("completed"),
        )


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
    subagent_model_outcomes: tuple[SubagentModelOutcomeDict, ...]
    child_outcomes: tuple[ChildOutcomeDict, ...]
    turn_usage: list[TurnTokenEntry] = field(default_factory=list)
    execution_identity: ExecutionIdentity = ExecutionIdentity.empty()

    @classmethod
    def empty(cls) -> SessionTelemetry:
        """Zero-value sentinel for error paths where no telemetry is available."""
        return cls(
            token_usage=None,
            turn_usage=[],
            timing_seconds=None,
            audit_record=None,
            github_api_usage=None,
            github_api_requests=0,
            loc_insertions=0,
            loc_deletions=0,
            subagent_model_outcomes=(),
            child_outcomes=(),
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
