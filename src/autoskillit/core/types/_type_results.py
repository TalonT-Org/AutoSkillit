"""Core result dataclasses — universal types.
Execution-scoped types (ApiFailureOutcome, RateLimitWindow, SessionTelemetry,
RecipeIdentity, CIRunScope) live in _type_results_execution.py for narrower test
cascade. ProviderOutcome stays here because SkillResult.provider is universal.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Generic, Literal, TypedDict, TypeVar

from ..closure_hashing import HASH_RE as _HASH_RE
from ._type_audit_admission import AuditAttemptId, AuditOutcomeStatus
from ._type_audit_cycle_authority import AuditVerdict
from ._type_enums import FaultDomain, KillReason, RetryReason, SessionOutcome
from ._type_execution_identity import ExecutionIdentity
from ._type_results_execution import ApiFailureOutcome
from ._type_results_records import (
    SESSION_INDEX_SCHEMA_VERSION,
    CapturedStream,
    CleanupResult,
    CloneGateUncommitted,
    CloneGateUnpublished,
    CloneResult,
    CloneSuccessResult,
    FailureRecord,
    ModelTotalEntry,
    SessionIndexEntry,
    SpilledOutput,
    SpillSpec,
    TokenUsageFileEntry,
)

T = TypeVar("T")

__all__ = [
    "AuditResultOutcome",
    "ClosureAuthoritySpec",
    "closure_authority_spec_from_args",
    "ContaminationOutcome",
    "InputSpec",
    "InputSpecType",
    "LoadReport",
    "LoadResult",
    "ModelIdentity",
    "CapturedStream",
    "SpilledOutput",
    "SpillSpec",
    "TestResult",
    "ManagedSessionHome",
    "SkillUnavailabilityPayload",
    "SkillUnavailabilityRecord",
    "ValidatedAddDir",
    "ValidatedWorktreePath",
    "VALID_INPUT_SPEC_TYPES",
    "OutcomeInvariantSpec",
    "WriteBehaviorSpec",
    "WriteEvidence",
    "FailureRecord",
    "ProviderOutcome",
    "PreLaunchReadiness",
    "InfraOutcome",
    "ApiRetryOutcome",
    "NdjsonDriftOutcome",
    "SkillResult",
    "CleanupResult",
    "CloneSuccessResult",
    "CloneGateUncommitted",
    "CloneGateUnpublished",
    "CloneResult",
    "ModelTotalEntry",
    "TokenUsageFileEntry",
    "SessionIndexEntry",
    "SESSION_INDEX_SCHEMA_VERSION",
    "parse_plan_paths",
]

VALID_INPUT_SPEC_TYPES = frozenset({"file_path", "directory_path", "file_path_list"})

InputSpecType = Literal["file_path", "directory_path", "file_path_list"]


@dataclass
class TestResult:
    """Result of a test runner invocation."""

    passed: bool
    stdout: str
    stderr: str
    duration_seconds: float | None = None
    tests_selected: int | None = None
    tests_deselected: int | None = None
    filter_mode: str | None = None
    full_run_reason: str | None = None
    outer_timeout_seconds: float | None = None


@dataclass
class LoadReport:
    """A single file that failed to load, with the reason."""

    path: Path
    error: str


@dataclass
class LoadResult(Generic[T]):
    """Discovery result: successfully loaded items + error reports."""

    items: list[T]
    errors: list[LoadReport] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PreLaunchReadiness:
    """Backend readiness result whose failed probes never carry capability claims."""

    errors: tuple[str, ...]
    attested_env: Mapping[str, str] = field(default_factory=dict)


class SkillUnavailabilityRecord(TypedDict):
    """One deterministic backend-admission refusal exposed to the session."""

    skill: str
    backend: str
    operation: str
    diagnostic: str


class SkillUnavailabilityPayload(TypedDict):
    """Canonical machine-readable backend-admission refusals for one session."""

    backend: str | None
    unavailable: tuple[SkillUnavailabilityRecord, ...]


@dataclass(frozen=True, slots=True)
class ValidatedAddDir:
    """An --add-dir path validated for Claude Code convention compliance.

    Cannot be constructed directly — use ``validate_add_dir()`` or obtain from
    ``DefaultSessionSkillManager.init_session()``.

    Implements ``__str__``, ``__fspath__``, and ``__truediv__`` so it works
    transparently with ``str(d)`` (used by ``build_interactive_cmd``),
    ``shutil.rmtree`` (used by cook), and ``d / "subdir"`` (path
    composition in tests and production code).
    """

    path: str

    def __str__(self) -> str:
        return self.path

    def __fspath__(self) -> str:
        return self.path

    def __truediv__(self, other: str | Path) -> Path:
        return Path(self.path) / other

    def exists(self) -> bool:
        return Path(self.path).exists()

    def is_dir(self) -> bool:
        return Path(self.path).is_dir()

    def glob(self, pattern: str) -> list[Path]:
        return list(Path(self.path).glob(pattern))


@dataclass(frozen=True, slots=True)
class ManagedSessionHome:
    """Already-owned generated home for one logical interactive cook launch."""

    launch_id: str
    generated_home: Path
    skills_dir: ValidatedAddDir
    pass_fds: tuple[int, ...]
    unavailability_payload: SkillUnavailabilityPayload


@dataclass(frozen=True, slots=True)
class ValidatedWorktreePath:
    """A worktree path validated as absolute and existing on disk.

    Cannot be constructed directly — use ``validate_worktree_path()``.
    """

    path: str

    def __str__(self) -> str:
        return self.path

    def __fspath__(self) -> str:
        return self.path

    def __truediv__(self, other: str | Path) -> Path:
        return Path(self.path) / other

    def is_dir(self) -> bool:
        return Path(self.path).is_dir()


@dataclass(frozen=True, slots=True)
class OutcomeInvariantSpec:
    """Outcome invariant evaluated against skill-emitted output fields.

    when: predicate expression, e.g. "accept_count > 0"
    require: requirement expression, e.g. "fix_failures == 0"
    Both use grammar: <declared_int_field> <op> <int_literal>
    with ops: >, >=, ==, !=, <=, <
    """

    when: str
    require: str


@dataclass(frozen=True, slots=True)
class WriteBehaviorSpec:
    """Write-expectation metadata resolved from skill contracts.

    mode:
        None  — no write expectation (gate inactive)
        "always" — writes are always expected (gate active unconditionally)
        "conditional" — writes expected only when expected_when patterns match
    expected_when:
        Regex patterns matched against session output. Only meaningful when
        mode="conditional". If any pattern matches, writes are expected.
    """

    mode: str | None = None
    expected_when: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ClosureAuthoritySpec:
    """Caller-provided, hash-pinned authority file for closure-mode verification.

    authority_path:
        Absolute path to a frozen authority file (e.g. a pinned requirements inventory).
    authority_hash:
        Expected ``"sha256:<64-lowercase-hex>"`` digest of the authority file's bytes.
    plan_paths:
        Absolute paths to plan files whose hashes bind into the request hash.
    base_sha / diff_sha / target_sha:
        Git refs threaded into the request hash for ref-drift detection.
    """

    authority_path: str
    authority_hash: str
    plan_paths: tuple[str, ...] = ()
    base_sha: str = ""
    diff_sha: str = ""
    target_sha: str = ""

    def __post_init__(self) -> None:
        if not self.authority_path:
            raise ValueError("ClosureAuthoritySpec.authority_path must be non-empty")
        if not Path(self.authority_path).is_absolute():
            raise ValueError(
                f"ClosureAuthoritySpec.authority_path must be absolute, got "
                f"{self.authority_path!r}"
            )
        if not self.authority_hash:
            raise ValueError("ClosureAuthoritySpec.authority_hash must be non-empty")
        if not _HASH_RE.match(self.authority_hash):
            raise ValueError(
                f"ClosureAuthoritySpec.authority_hash must match 'sha256:[0-9a-f]{{64}}', got "
                f"{self.authority_hash!r}"
            )
        for idx, pp in enumerate(self.plan_paths):
            if not pp:
                raise ValueError(f"ClosureAuthoritySpec.plan_paths[{idx}] must be non-empty")
            if not Path(pp).is_absolute():
                raise ValueError(
                    f"ClosureAuthoritySpec.plan_paths[{idx}] must be absolute, got {pp!r}"
                )


def closure_authority_spec_from_args(
    path: str | None,
    hash_: str | None,
    *,
    plan_paths: tuple[str, ...] = (),
    base_sha: str = "",
    diff_sha: str = "",
    target_sha: str = "",
) -> ClosureAuthoritySpec | None:
    """Construct a ClosureAuthoritySpec from caller-supplied arguments.

    Returns None when both path and hash_ are None/empty (non-closure mode).
    Raises ValueError on XOR (exactly one of path/hash_ provided).
    """
    path_present = bool(path)
    hash_present = bool(hash_)
    if not path_present and not hash_present:
        return None
    if path_present != hash_present:
        raise ValueError("Closure mode requires both authority_path and authority_hash")
    return ClosureAuthoritySpec(
        authority_path=path or "",
        authority_hash=hash_ or "",
        plan_paths=plan_paths,
        base_sha=base_sha,
        diff_sha=diff_sha,
        target_sha=target_sha,
    )


def parse_plan_paths(raw: str) -> tuple[str, ...]:
    """Split plan paths on commas or newlines — handles both
    context.all_plan_paths (comma-separated) and context.group_files
    (newline-separated). Whitespace is stripped from each token; empty
    tokens are filtered.
    """
    parts = re.split(r"[,\n]+", raw)
    return tuple(p.strip() for p in parts if p.strip())


@dataclass(frozen=True, slots=True)
class InputSpec:
    """Input contract specification for a scalar or list path argument.

    Covers single-path types (``file_path``, ``directory_path``) and the
    list variant (``file_path_list``) whose value is one positional token
    carrying comma- or newline-separated member paths.
    """

    name: str
    type: InputSpecType
    required: bool
    position: int

    def __post_init__(self) -> None:
        if self.position < 0:
            raise ValueError(f"InputSpec.position must be >= 0, got {self.position}")
        if self.type not in VALID_INPUT_SPEC_TYPES:
            raise ValueError(
                f"InputSpec.type must be one of {sorted(VALID_INPUT_SPEC_TYPES)}, "
                f"got {self.type!r}"
            )


@dataclass(frozen=True, slots=True)
class ProviderOutcome:
    """Typed bundle of provider execution outcome fields.

    All fields are required — constructing without any field is a TypeError,
    making omissions visible at construction time rather than silently defaulting.
    """

    provider_used: str
    fallback_activated: bool

    @classmethod
    def none_used(cls) -> ProviderOutcome:
        """Sentinel for paths where no provider selection occurred."""
        return cls(provider_used="", fallback_activated=False)


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    """Typed bundle of model identity for a headless session.

    Carries both the configured Anthropic alias and the effective provider model,
    preventing provider-unaware resolution from silently producing wrong-but-truthy values.
    """

    configured_model: str
    effective_model: str
    profile_name: str

    @property
    def is_anthropic(self) -> bool:
        return not self.profile_name or self.profile_name == "anthropic"

    @classmethod
    def anthropic(cls, model: str) -> ModelIdentity:
        return cls(configured_model=model, effective_model=model, profile_name="")

    @classmethod
    def for_provider(cls, *, configured: str, effective: str, profile: str) -> ModelIdentity:
        return cls(configured_model=configured, effective_model=effective, profile_name=profile)

    @classmethod
    def unknown(cls) -> ModelIdentity:
        return cls(configured_model="", effective_model="", profile_name="")


@dataclass(frozen=True, slots=True)
class InfraOutcome:
    """Infrastructure exit classification bundle."""

    exit_category: str = ""
    cleanup_incomplete: bool = False
    """Surfaces ``SubprocessResult.cleanup_evidence`` for retry orchestration.
    See ``_should_flag_cleanup_incomplete`` in execution.headless._headless_result
    for the canonical contract."""
    fault_domain: FaultDomain = FaultDomain.LOGIC
    """Populated from exception type at the classifying catch site, never from
    ``exit_category`` (post-launch text analysis) — the two are not cross-populated.
    An ``infrastructure_fault`` result has ``exit_category=""``; an existing
    rate-limited/API-error result keeps the default ``fault_domain=LOGIC``."""


@dataclass(frozen=True, slots=True)
class ApiRetryOutcome:
    """API retry event accumulation bundle."""

    count: int = 0
    last_error: str = ""
    last_status: int | None = None
    exhausted: bool = False


@dataclass(frozen=True, slots=True)
class ContaminationOutcome:
    """Pre-contamination context preserved when clone_guard fires.

    Default values (NONE, "") indicate no contamination occurred.
    """

    retry_reason: RetryReason = RetryReason.NONE
    subtype: str = ""


@dataclass(frozen=True, slots=True)
class NdjsonDriftOutcome:
    """NDJSON parser vocabulary drift counters.

    Aggregates unknown event and item counts emitted by Codex NDJSON parsing
    when the parser encounters event/item types not in its known vocabulary.
    Surfaced through ``SkillResult.ndjson_drift`` and propagated into
    ``summary.json``, ``sessions.jsonl``, and ``anomalies.jsonl`` for
    diagnostics and the doctor ``codex_ndjson_drift`` check.
    """

    unknown_event_count: int = 0
    unknown_item_count: int = 0


@dataclass(frozen=True, slots=True)
class AuditResultOutcome:
    """Server-authored audit outcome attached to a skill result."""

    status: AuditOutcomeStatus | None = None
    verdict: AuditVerdict | None = None
    cycle_path: str | None = None
    attempt_id: AuditAttemptId | None = None


@dataclass(frozen=True, slots=True)
class WriteEvidence:
    """Bundled write evidence signals — either explicitly constructed or absent."""

    write_call_count: int
    fs_writes_detected: bool
    git_writes_detected: bool
    file_changes_count: int = 0

    @classmethod
    def none_observed(cls) -> WriteEvidence:
        return cls(
            write_call_count=0,
            fs_writes_detected=False,
            git_writes_detected=False,
            file_changes_count=0,
        )

    @property
    def has_evidence(self) -> bool:
        return (
            self.write_call_count >= 1
            or self.fs_writes_detected
            or self.git_writes_detected
            or self.file_changes_count >= 1
        )

    @property
    def has_implementation_evidence(self) -> bool:
        return self.write_call_count >= 1 or self.file_changes_count >= 1


@dataclass
class SkillResult:
    """Typed result returned by _build_skill_result and run_headless_core."""

    success: bool
    result: str
    session_id: str
    subtype: str
    is_error: bool
    exit_code: int
    needs_retry: bool
    retry_reason: RetryReason
    stderr: str
    token_usage: dict[str, Any] | None = None
    worktree_path: str | None = None
    branch_name: str | None = None
    cli_subtype: str = field(default="")
    write_path_warnings: list[str] = field(default_factory=list)
    evidence: WriteEvidence = field(default_factory=WriteEvidence.none_observed)
    order_id: str = ""
    kill_reason: KillReason = KillReason.NATURAL_EXIT
    """Why the subprocess was (or was not) killed after the race loop.

    Surfaces from SubprocessResult so the formatter can annotate exit_code
    with the kill cause, resolving the "success=True + exit_code=-9" contradiction.
    """
    last_stop_reason: str = ""
    lifespan_started: bool = False
    """True when the headless session called an MCP tool (heuristic for server lifespan)."""
    provider: ProviderOutcome = field(default_factory=ProviderOutcome.none_used)
    """Provider execution outcome bundle."""
    infra: InfraOutcome = field(default_factory=InfraOutcome)
    """Infrastructure exit classification bundle."""
    api_retry: ApiRetryOutcome = field(default_factory=ApiRetryOutcome)
    """API retry event accumulation bundle."""
    api_failure: ApiFailureOutcome = field(default_factory=ApiFailureOutcome)
    """Structured provider-failure evidence bundle."""
    contamination: ContaminationOutcome = field(default_factory=ContaminationOutcome)
    """Pre-contamination context bundle — populated only when clone_guard fires."""
    ndjson_drift: NdjsonDriftOutcome = field(default_factory=NdjsonDriftOutcome)
    """NDJSON parser vocabulary drift counters — populated by Codex sessions."""
    audit: AuditResultOutcome = field(default_factory=AuditResultOutcome)
    """Server-authored audit outcome bundle."""
    completion_required: bool = False
    outcome_fields: dict[str, int | str] | None = None
    outcome_invariant_violated: bool = False
    outcome_qualifier: str | None = None
    execution_identity: ExecutionIdentity = field(default_factory=ExecutionIdentity.empty)
    """Requested launch intent plus backend-owned effective execution evidence."""

    def to_json(self) -> str:
        data: dict[str, Any] = {
            "success": self.success,
            "result": self.result,
            "session_id": self.session_id,
            "subtype": self.subtype,
            "cli_subtype": self.cli_subtype,
            "is_error": self.is_error,
            "exit_code": self.exit_code,
            "kill_reason": self.kill_reason.value,
            "needs_retry": self.needs_retry,
            "retry_reason": self.retry_reason,
            "stderr": self.stderr,
            "token_usage": self.token_usage,
            "write_path_warnings": self.write_path_warnings,
            "write_call_count": self.evidence.write_call_count,
            "fs_writes_detected": self.evidence.fs_writes_detected,
            "git_writes_detected": self.evidence.git_writes_detected,
            "file_changes_count": self.evidence.file_changes_count,
            "has_progress_evidence": self.has_progress_evidence,
            "has_implementation_progress": self.has_implementation_progress,
            "completion_required": self.completion_required,
            "last_stop_reason": self.last_stop_reason,
            "lifespan_started": self.lifespan_started,
            "provider_fallback": self.provider.fallback_activated,
            "provider_used": self.provider.provider_used,
            "infra_exit_category": self.infra.exit_category,
            "infra_cleanup_incomplete": self.infra.cleanup_incomplete,
            "infra_fault_domain": self.infra.fault_domain.value,
            "api_retry_count": self.api_retry.count,
            "api_retry_last_error": self.api_retry.last_error,
            "api_retry_last_status": self.api_retry.last_status,
            "api_retry_exhausted": self.api_retry.exhausted,
            "api_error_status": self.api_failure.status,
            "api_terminal_reason": self.api_failure.terminal_reason,
            "api_error_code": self.api_failure.error_code,
            "api_error_message_seen": self.api_failure.api_error_message_seen,
            "rate_limit_status": self.api_failure.rate_limit.status,
            "rate_limit_type": self.api_failure.rate_limit.limit_type,
            "rate_limit_resets_at_epoch": self.api_failure.rate_limit.resets_at_epoch,
            "pre_contamination_retry_reason": self.contamination.retry_reason,
            "pre_contamination_subtype": self.contamination.subtype,
            "ndjson_unknown_event_count": self.ndjson_drift.unknown_event_count,
            "ndjson_unknown_item_count": self.ndjson_drift.unknown_item_count,
            "audit_status": self.audit.status.value if self.audit.status is not None else None,
            "audit_verdict": (
                self.audit.verdict.value if self.audit.verdict is not None else None
            ),
            "audit_cycle_path": self.audit.cycle_path,
            "audit_attempt_id": (
                self.audit.attempt_id.value if self.audit.attempt_id is not None else None
            ),
            "execution_identity": self.execution_identity.to_dict(),
        }
        if self.worktree_path is not None:
            data["worktree_path"] = self.worktree_path
        if self.branch_name is not None:
            data["branch_name"] = self.branch_name
        data["order_id"] = self.order_id
        return json.dumps(data, default=lambda o: o.value if isinstance(o, Enum) else str(o))

    @classmethod
    def crashed(
        cls,
        exception: Exception,
        skill_command: str = "",
        session_id: str = "",
        order_id: str = "",
    ) -> SkillResult:
        """Construct a SkillResult for a runner crash (pre-launch or mid-flight exception).

        Produces the same 13+ field envelope as _build_skill_result, ensuring
        pipeline orchestrators can route crash responses without schema inspection.
        """
        _result = f"{type(exception).__name__}: {exception}"
        if skill_command:
            _result += f" | skill_command={skill_command!r}"
        return cls(
            success=False,
            result=_result,
            session_id=session_id,
            subtype="crashed",
            is_error=True,
            exit_code=-1,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
            kill_reason=KillReason.EXCEPTION,
            order_id=order_id,
            evidence=WriteEvidence.none_observed(),
        )

    @classmethod
    def infeasible(
        cls,
        *,
        skill_name: str,
        backend: str,
        diagnostic: str,
        skill_command: str = "",
        session_id: str = "",
        order_id: str = "",
    ) -> SkillResult:
        """Construct a terminal result for a designed backend admission refusal."""
        result = f"Skill {skill_name!r} is not feasible on backend {backend!r}: {diagnostic}"
        if skill_command:
            result += f" | skill_command={skill_command!r}"
        return cls(
            success=False,
            result=result,
            session_id=session_id,
            subtype="infeasible",
            is_error=True,
            exit_code=-1,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
            kill_reason=KillReason.NOT_APPLICABLE,
            order_id=order_id,
            evidence=WriteEvidence.none_observed(),
        )

    @classmethod
    def cancelled(
        cls,
        skill_command: str = "",
        session_id: str = "",
        order_id: str = "",
    ) -> SkillResult:
        """Construct a SkillResult for transport-level CancelledError.

        Produces a retriable result with needs_retry=True and
        retry_reason=RetryReason.CANCELLED so the orchestrator can route
        via on_failure (cancellation is not a context-limit event).
        """
        return cls(
            success=False,
            result=f"CancelledError: transport teardown | skill_command={skill_command!r}",
            session_id=session_id,
            subtype="cancelled",
            is_error=True,
            exit_code=-1,
            needs_retry=True,
            retry_reason=RetryReason.CANCELLED,
            stderr="",
            kill_reason=KillReason.EXCEPTION,
            order_id=order_id,
            evidence=WriteEvidence.none_observed(),
        )

    @classmethod
    def infrastructure_fault(
        cls,
        exception: Exception,
        skill_command: str = "",
        session_id: str = "",
        order_id: str = "",
    ) -> SkillResult:
        """Construct a SkillResult for a fault that is a property of the environment.

        Sibling to ``crashed()``/``cancelled()``, for exceptions deriving from
        ``InfrastructureFaultError``. ``needs_retry=False`` deliberately: the
        environment is still broken, so retrying in-process cannot help. The
        result is distinguished from a logic crash by ``fault_domain``, not by
        retry semantics.
        """
        _result = f"{type(exception).__name__}: {exception}"
        if skill_command:
            _result += f" | skill_command={skill_command!r}"
        return cls(
            success=False,
            result=_result,
            session_id=session_id,
            subtype="infrastructure_fault",
            is_error=True,
            exit_code=-1,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
            kill_reason=KillReason.EXCEPTION,
            order_id=order_id,
            evidence=WriteEvidence.none_observed(),
            infra=InfraOutcome(fault_domain=FaultDomain.INFRASTRUCTURE),
        )

    @property
    def outcome(self) -> SessionOutcome:
        """Classify this result as SUCCEEDED, RETRIABLE, or FAILED.

        Derived from the (success, needs_retry) pair — not a stored field.
        Not included in to_json().
        """
        if self.success:
            return SessionOutcome.SUCCEEDED
        if self.needs_retry:
            return SessionOutcome.RETRIABLE
        return SessionOutcome.FAILED

    @property
    def has_progress_evidence(self) -> bool:
        """Whether any evidence of meaningful progress exists."""
        return self.worktree_path is not None or self.evidence.has_evidence

    @property
    def has_implementation_progress(self) -> bool:
        return self.evidence.has_implementation_evidence
