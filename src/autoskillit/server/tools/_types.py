"""Server tool response TypedDicts — typed contracts for MCP tool JSON responses.

These types define the shape of JSON payloads returned by server tool handlers.
They live here (IL-3) because their only structured consumers are test infrastructure
and server internals — not cross-layer protocols.
"""

from __future__ import annotations

import json
from typing import Any, Literal, TypedDict

from autoskillit.core import ModelTotalEntry, RetryReason

__all__ = [
    "RunSkillResult",
    "RunCmdResult",
    "TestCheckResult",
    "MergeWorktreeResult",
    "TokenSummaryResult",
    "TimingSummaryResult",
    "KitchenStatusResult",
    "DispatchEnvelopeResult",
    "ToolFailureEnvelope",
    "server_failure_envelope",
    "input_failure_envelope",
    "_validate_result",
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
    ndjson_unknown_event_count: int
    ndjson_unknown_item_count: int


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


class DispatchEnvelopeResult(TypedDict, total=False):
    """Typed return contract for dispatch_food_truck — union of all response paths.

    Covers DispatchCompleted.to_envelope(), DispatchRejected.to_envelope(),
    and fleet_error() output. The error paths do NOT carry a ``subtype`` key
    so they reach this formatter rather than the gate_error/tool_exception
    guards. Nested structured fields (l3_payload, health_report, token_usage,
    resume_checkpoint) must be rendered without size-based truncation to
    preserve dispatch_plan visibility for downstream orchestrators.
    """

    success: bool
    kind: str
    dispatch_status: str
    dispatch_id: str
    dispatched_session_id: str
    reason: str
    token_usage: dict[str, Any]
    l3_payload: dict[str, Any] | None
    l3_parse_source: str
    lifespan_started: bool
    l3_raw_body: str
    l3_parse_error: str
    resume_checkpoint: dict[str, Any]
    health_report: dict[str, Any] | None
    stderr: str
    elapsed_seconds: float
    error: str
    user_visible_message: str
    details: dict[str, Any] | None


class _ToolFailureEnvelopeRequired(TypedDict):
    """Required fields for the structured failure envelope."""

    success: Literal[False]
    error: str
    stage: str
    retriable: bool


class ToolFailureEnvelope(_ToolFailureEnvelopeRequired, total=False):
    """Typed failure envelope with retriable discriminator for orchestrator routing.

    Distinct from ``_kitchen_failure_envelope`` in tools_kitchen.py which returns
    a raw JSON string with a ``kitchen`` field. This TypedDict provides a typed
    dict contract with a ``retriable`` discriminator for P5-A4 orchestrator routing.
    """

    user_visible_message: str


def server_failure_envelope(
    exc: BaseException,
    stage: str,
) -> ToolFailureEnvelope:
    """Build a failure envelope for server/infrastructure errors (retriable=True)."""
    return ToolFailureEnvelope(
        success=False,
        error=f"{type(exc).__name__}: {exc}",
        stage=stage,
        retriable=True,
        user_visible_message=(
            f"Server error during {stage}: {type(exc).__name__}. "
            "This may be transient — the orchestrator may retry."
        ),
    )


def input_failure_envelope(
    message: str,
    stage: str,
) -> ToolFailureEnvelope:
    """Build a failure envelope for input/validation errors (retriable=False)."""
    return ToolFailureEnvelope(
        success=False,
        error=message,
        stage=stage,
        retriable=False,
    )


def _validate_result(
    result: dict[str, Any],
    *,
    required_keys: frozenset[str],
    tool_name: str,
    retriable: bool = False,
) -> str | None:
    """Validate a tool result dict and return a fail-closed envelope on violation.

    Returns ``None`` when all invariants hold, or a ``json.dumps``-serialized
    ``ToolFailureEnvelope`` on the first violation detected.
    """
    _stage = f"validate_result:{tool_name}"
    for key in sorted(required_keys):
        if key not in result:
            return json.dumps(
                ToolFailureEnvelope(
                    success=False,
                    error=f"Missing required key: {key}",
                    stage=_stage,
                    retriable=retriable,
                )
            )
        if result[key] is None:
            return json.dumps(
                ToolFailureEnvelope(
                    success=False,
                    error=f"Required key is None: {key}",
                    stage=_stage,
                    retriable=retriable,
                )
            )
    if "success" in result and result["success"] is not True:
        return json.dumps(
            ToolFailureEnvelope(
                success=False,
                error=f"Result reports failure: success={result['success']!r}",
                stage=_stage,
                retriable=retriable,
            )
        )
    if "content" in result and not result["content"]:
        return json.dumps(
            ToolFailureEnvelope(
                success=False,
                error="Result content is empty",
                stage=_stage,
                retriable=retriable,
            )
        )
    return None
