"""Core result dataclasses — universal types.

Execution-scoped types (SessionTelemetry, RecipeIdentity, CIRunScope) live in
_type_results_execution.py for narrower test cascade. ProviderOutcome stays here
because SkillResult.provider references it, and SkillResult is consumed by 13+
directories — a cross-import would undermine the cascade narrowing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Generic, Literal, TypedDict, TypeVar

from ..closure_hashing import HASH_RE as _HASH_RE
from ._type_audit_admission import AuditAttemptId, AuditOutcomeStatus
from ._type_audit_cycle import AuditVerdict
from ._type_enums import KillReason, RetryReason, SessionOutcome

T = TypeVar("T")

__all__ = [
    "AuditResultOutcome",
    "CapabilityResolutionDetail",
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
    "ValidatedAddDir",
    "ValidatedWorktreePath",
    "VALID_INPUT_SPEC_TYPES",
    "OutcomeInvariantSpec",
    "WriteBehaviorSpec",
    "WriteEvidence",
    "FailureRecord",
    "ProviderOutcome",
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
class SpillSpec:
    """Character budgets for lossless artifact-backed output previews.

    Dual denomination: ``spill_output`` uses ``inline_max_chars`` as a character
    threshold; ``summarize_capture`` uses it as a byte threshold (identical for
    ASCII; at most more conservative for multibyte).
    """

    inline_max_chars: int = 5000
    head_chars: int = 2500
    tail_chars: int = 2500

    def __post_init__(self) -> None:
        if self.inline_max_chars < 0 or self.head_chars < 0 or self.tail_chars < 0:
            raise ValueError("spill character budgets must be non-negative")


@dataclass(frozen=True, slots=True)
class CapturedStream:
    """Streaming capture result — bounded slices only, never a full read."""

    path: Path
    total_bytes: int
    sha256: str
    inline_text: str | None
    head: str
    tail: str
    complete: bool


@dataclass(frozen=True, slots=True)
class SpilledOutput:
    """Lossless spill result with a bounded inline representation."""

    spilled: bool
    text: str
    artifact_path: str | None
    head: str = ""
    tail: str = ""
    sha256: str = ""
    total_chars: int = 0
    total_utf8_bytes: int = 0
    total_lines: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "spilled": self.spilled,
            "text": self.text,
            "artifact_path": self.artifact_path,
            "head": self.head,
            "tail": self.tail,
            "sha256": self.sha256,
            "total_chars": self.total_chars,
            "total_utf8_bytes": self.total_utf8_bytes,
            "total_lines": self.total_lines,
        }


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


@dataclass
class FailureRecord:
    """Structured record of a single run_skill failure.

    Pure-stdlib dataclass — no autoskillit imports required.
    Shared between pipeline/audit.py (DefaultAuditLog store) and
    execution/headless.py (_capture_failure).
    """

    timestamp: str  # ISO 8601 UTC, e.g. "2026-02-24T16:12:26Z"
    skill_command: str  # truncated to COMMAND_MAX_LEN
    exit_code: int
    subtype: str  # e.g. "error", "stale", "timeout", "gate_error"
    needs_retry: bool
    retry_reason: str  # RetryReason.value string
    stderr: str  # truncated to STDERR_MAX_LEN

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "skill_command": self.skill_command,
            "exit_code": self.exit_code,
            "subtype": self.subtype,
            "needs_retry": self.needs_retry,
            "retry_reason": self.retry_reason,
            "stderr": self.stderr,
        }


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
class CapabilityResolutionDetail:
    """Diagnostic metadata for per-step capability override resolution.

    Carries the resolution path and per-step data through the capability
    admission chain so downstream consumers (infeasibility response
    formatters) can surface the actual cause of capability resolution failure
    rather than blaming the backend generically.

    resolution_path values:
        "any_pass" — at least one guarded step resolved with ANTHROPIC_BASE_URL;
            capability flipped to "true".
        "none_pass" — no guarded step had ANTHROPIC_BASE_URL;
            capability remains "false".
        "no_guarded_steps" — recipe has no run_skill steps gated by
            backend_supports_git_write; nothing to flip.
        "claude_backend" — backend is anthropic_provider_capable=True;
            no provider override needed.
        "graceful_degradation" — backend, config_providers, or recipe_steps
            was None; no resolution attempted.
        "baseline_already_true" — backend_supports_git_write was already "true"
            from the baseline capability; no provider override needed.
        "capability_route" — skill capability scan detected a step requiring
            ``git_metadata_write``; capability flipped to "true" without
            requiring provider config (REQ-ADMIT-002).
        "capability_route_no_binary" — skill capability scan detected a step
            requiring ``git_metadata_write`` but ``shutil.which("claude")``
            returned ``None``; capability remains "false" (fail-closed, REQ-ROUTE-004).
        "no_providers_configured" — ``config_providers`` was ``None`` and no
            skill capability route applied; capability remains "false".
    """

    resolved_steps: tuple[tuple[str, str, bool], ...]
    bail_step: str | None
    resolution_path: str

    @property
    def missing_provider_steps(self) -> tuple[str, ...]:
        """Step names whose resolved provider profile lacked ANTHROPIC_BASE_URL."""
        return tuple(name for name, _, has_base_url in self.resolved_steps if not has_base_url)

    @classmethod
    def empty(cls, resolution_path: str) -> CapabilityResolutionDetail:
        """Construct a detail with no resolved-step data."""
        return cls(resolved_steps=(), bail_step=None, resolution_path=resolution_path)


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
            "api_retry_count": self.api_retry.count,
            "api_retry_last_error": self.api_retry.last_error,
            "api_retry_last_status": self.api_retry.last_status,
            "api_retry_exhausted": self.api_retry.exhausted,
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


@dataclass
class CleanupResult:
    deleted: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.failed) == 0

    def to_dict(self) -> dict:  # type: ignore[type-arg]
        return {
            "success": self.success,
            "deleted": self.deleted,
            "failed": [{"path": p, "error": e} for p, e in self.failed],
            "skipped": self.skipped,
        }


class CloneSuccessResult(TypedDict):
    """Typed return contract for a successful clone_repo invocation.

    Precedent: PRFetchState(TypedDict) in execution/merge_queue.py for
    typed discriminated returns in the same codebase.
    """

    clone_path: str
    source_dir: str
    remote_url: str
    clone_source_type: Literal["remote", "local"]
    clone_source_reason: str


class CloneGateUncommitted(TypedDict):
    """Returned by clone_repo when uncommitted changes are detected (strategy="")."""

    uncommitted_changes: Literal["true"]
    source_dir: str
    branch: str
    changed_files: str
    total_changed: str


class CloneGateUnpublished(TypedDict):
    """Returned by clone_repo when the branch is unpublished (strategy="")."""

    unpublished_branch: Literal["true"]
    branch: str
    source_dir: str


CloneResult = CloneSuccessResult | CloneGateUncommitted | CloneGateUnpublished


class ModelTotalEntry(TypedDict):
    """Per-model aggregate token counts produced by compute_model_totals().

    In-memory only — never written to or read from disk; use TokenUsageFileEntry
    for the on-disk schema. Unlike TokenUsageFileEntry, this type carries only
    v2 canonical cache keys (cache_write_tokens, cache_read_tokens).
    """

    model: str
    step_count: int
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    elapsed_seconds: float


class TokenUsageFileEntry(TypedDict):
    """Schema contract for token_usage.json written by flush_session_log."""

    session_label: str
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    peak_context: int
    turn_count: int
    timing_seconds: float
    order_id: str
    loc_insertions: int
    loc_deletions: int
    provider_used: str
    model_identifier: str
    configured_model: str
    profile_name: str
    dispatch_id: str
    campaign_id: str
    schema_version: int


class SessionIndexEntry(TypedDict):
    """Schema contract for sessions.jsonl entries written by flush_session_log."""

    session_id: str
    dir_name: str
    timestamp: str
    cwd: str
    kitchen_id: str
    order_id: str
    campaign_id: str
    dispatch_id: str
    claude_code_log: str | None  # path to Claude Code JSONL, or None for non-Claude-Code sessions
    codex_log: str | None  # path to Codex rollout NDJSON, or None for non-Codex sessions
    backend: str  # "claude-code" or "codex" — unambiguous backend identifier
    backend_override_source: (
        str | None
    )  # "explicit_config" | "skill_requirement" | "provider_profile" | None
    skill_command: str
    success: bool
    subtype: str
    cli_subtype: str
    exit_code: int
    snapshot_count: int
    anomaly_count: int
    peak_rss_kb: int
    peak_oom_score: int
    step_name: str
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    write_call_count: int
    fs_writes_detected: bool
    git_writes_detected: bool
    file_changes_count: int
    tracked_comm: str | None
    tracked_comm_drift: bool
    autoskillit_version: str
    claude_code_version: str
    codex_version: str
    recipe_name: str
    recipe_content_hash: str
    recipe_composite_hash: str
    recipe_version: str
    duration_seconds: float
    github_api_requests: int
    provider_used: str
    provider_fallback: bool
    model_identifier: str
    configured_model: str
    profile_name: str
    caller_session_id: str
    api_retry_count: int
    api_retry_exhausted: bool
    api_retry_last_error: str
    api_retry_last_status: int | None
    ndjson_unknown_event_count: int
    ndjson_unknown_item_count: int
    outcome_fields: dict[str, int | str] | None
    outcome_invariant_violated: bool
    outcome_qualifier: str | None
    native_shell_capture: dict[str, object] | None
    schema_version: int
