"""Server tool response TypedDicts — typed contracts for MCP tool JSON responses.

These types define the shape of JSON payloads returned by server tool handlers.
They live here (IL-3) because their only structured consumers are test infrastructure
and server internals — not cross-layer protocols.
"""

from __future__ import annotations

from typing import Any, TypedDict

from autoskillit.core import ModelTotalEntry, RetryReason

__all__ = [
    "RunSkillResult",
    "RunCmdResult",
    "TestCheckResult",
    "MergeWorktreeResult",
    "TokenSummaryResult",
    "TimingSummaryResult",
    "KitchenStatusResult",
]


class _RunSkillResultBase(TypedDict):
    """Required fields always present in every run_skill response."""

    success: bool
    exit_code: int


class RunSkillResult(_RunSkillResultBase, total=False):
    """Typed return contract for run_skill — mirrors SkillResult.to_json() output keys."""

    result: str
    session_id: str
    subtype: str
    cli_subtype: str
    is_error: bool
    kill_reason: str
    needs_retry: bool
    retry_reason: str
    stderr: str
    token_usage: dict[str, Any] | None
    write_path_warnings: list[str]
    write_call_count: int
    fs_writes_detected: bool
    git_writes_detected: bool
    file_changes_count: int
    last_stop_reason: str
    lifespan_started: bool
    worktree_path: str
    order_id: str
    infra_exit_category: str
    has_progress_evidence: bool
    has_implementation_progress: bool
    completion_required: bool
    provider_fallback: bool
    provider_used: str
    api_retry_count: int
    api_retry_last_error: str
    api_retry_last_status: int
    api_retry_exhausted: bool
    pre_contamination_retry_reason: RetryReason
    pre_contamination_subtype: str


class _RunCmdResultBase(TypedDict):
    """Required fields always present in every run_cmd response."""

    success: bool
    exit_code: int


class RunCmdResult(_RunCmdResultBase, total=False):
    """Typed return contract for run_cmd."""

    stdout: str
    stderr: str
    error: str


class _TestCheckResultBase(TypedDict):
    """Required field always present in every test_check response."""

    passed: bool


class TestCheckResult(_TestCheckResultBase, total=False):
    """Typed return contract for test_check."""

    stdout: str
    stderr: str
    duration_seconds: float
    filter_mode: str
    tests_selected: int
    tests_deselected: int
    full_run_reason: str
    error: str
    infrastructure_missing: bool


class MergeWorktreeResult(TypedDict, total=False):
    """Typed return contract for merge_worktree — union of all success and error path keys."""

    merge_succeeded: bool
    merged_branch: str
    into_branch: str
    worktree_removed: bool
    branch_deleted: bool
    cleanup_succeeded: bool
    error: str
    failed_step: str
    state: str
    worktree_path: str
    stderr: str
    base_branch: str
    dirty_files: list[str]
    merge_commits: list[str]
    test_stdout: str
    test_stderr: str
    abort_failed: bool
    abort_stderr: str
    poisoned_installs: list[str]


class TokenSummaryResult(TypedDict, total=False):
    """Typed return contract for get_token_summary (JSON payload path)."""

    steps: list[dict[str, Any]]
    total: dict[str, Any]
    mcp_responses: dict[str, Any]
    model_totals: list[ModelTotalEntry]
    success: bool
    error: str


class TimingSummaryResult(TypedDict, total=False):
    """Typed return contract for get_timing_summary (JSON payload path)."""

    steps: list[dict[str, Any]]
    total: dict[str, Any]
    success: bool
    error: str


class KitchenStatusResult(TypedDict, total=False):
    """Typed return contract for kitchen_status."""

    package_version: str
    plugin_json_version: str
    versions_match: bool
    tools_enabled: bool
    token_usage_verbosity: str
    quota_guard_enabled: bool
    github_token_configured: bool
    github_default_repo: str
    warning: str
    success: bool
    error: str
