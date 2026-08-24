"""Leaf result and persisted-index record contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict

from ._type_execution_identity import ChildExecutionIdentityDict

__all__ = [
    "CapturedStream",
    "SpilledOutput",
    "FailureRecord",
    "CleanupResult",
    "CloneSuccessResult",
    "CloneGateUncommitted",
    "CloneGateUnpublished",
    "CloneResult",
    "ModelTotalEntry",
    "TokenUsageFileEntry",
    "SessionIndexEntry",
]


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
class CleanupResult:
    deleted: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.failed) == 0

    def to_dict(self) -> dict[str, Any]:
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
    repository_identity_url: str
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
    backend_authority: dict[str, object] | None
    launch_contract_digest: str
    requested_parent_backend: str
    effective_parent_backend: str
    requested_parent_model: str
    effective_parent_model: str
    requested_parent_effort: str
    effective_parent_effort: str
    execution_cli_version: str
    backend_override_tier: str
    backend_override_key_path: str
    parent_session_id: str
    child_executions: list[ChildExecutionIdentityDict]
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
