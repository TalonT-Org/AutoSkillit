"""Evidence computation, audit recording, and telemetry builders for headless sessions."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    CODEX_CONTEXT_EXHAUSTION_MARKER,
    AgentSessionResult,
    CliSubtype,
    FailureRecord,
    LifecycleDecision,
    RetryReason,
    SessionOutcome,
    SessionTelemetry,
    SkillResult,
    WriteEvidence,
    get_logger,
)
from autoskillit.execution.session._session_content import _check_expected_patterns
from autoskillit.execution.session._session_model import (
    ClaudeSessionResult,
    _is_parent_assistant_record,
)

if TYPE_CHECKING:
    from autoskillit.core import AuditLog, CodingAgentBackend, GitHubApiLog, SubprocessResult

logger = get_logger(__name__)


def _capture_failure(
    skill_command: str,
    exit_code: int,
    subtype: str,
    needs_retry: bool,
    retry_reason: str,
    stderr: str,
    audit: AuditLog | None,
) -> None:
    """Record a failure in the audit log. No-op if skill_command is empty or audit is None."""
    if not skill_command or audit is None:
        return
    audit.record_failure(
        FailureRecord(
            timestamp=datetime.now(UTC).isoformat(),
            skill_command=skill_command,
            exit_code=exit_code,
            subtype=subtype,
            needs_retry=needs_retry,
            retry_reason=retry_reason,
            stderr=stderr,
        )
    )


def _apply_budget_guard(
    sr: SkillResult,
    skill_command: str,
    audit: AuditLog | None,
    max_consecutive_retries: int,
) -> SkillResult:
    """Override needs_retry to False when the consecutive-failure budget is exhausted."""
    if not sr.needs_retry or audit is None or not skill_command:
        return sr
    consecutive = audit.consecutive_failures(skill_command)
    # current failure already recorded; consecutive count includes this attempt
    if consecutive > max_consecutive_retries:
        logger.warning(
            "retry_budget_exhausted",
            skill_command=skill_command,
            consecutive_failures=consecutive,
            max_consecutive_retries=max_consecutive_retries,
        )
        return dataclasses.replace(
            sr,
            needs_retry=False,
            retry_reason=RetryReason.BUDGET_EXHAUSTED,
        )
    return sr


def _retry_precedence(
    result: SubprocessResult,
    outcome: SessionOutcome,
    retry_reason: RetryReason,
) -> tuple[SessionOutcome, RetryReason]:
    cleanup_failed = result.cleanup_outcome is not None and not result.cleanup_outcome.succeeded
    lifecycle_failed = result.lifecycle_decision in {
        LifecycleDecision.CHILD_WORK_FAILED,
        LifecycleDecision.CATCH_UP_FAILED,
    }
    if cleanup_failed or lifecycle_failed:
        return SessionOutcome.RETRIABLE, RetryReason.RESUME
    return outcome, retry_reason


def _adjudicate_optional_completion(
    result: SubprocessResult,
    session: ClaudeSessionResult,
    outcome: SessionOutcome,
    retry_reason: RetryReason,
    needs_retry: bool,
    completion_marker: str,
    prior_completion_markers: Sequence[str] | None,
    completion_required: bool,
    expected_output_patterns: Sequence[str],
    infra_completed: bool,
) -> tuple[SessionOutcome, RetryReason, bool, bool, str]:
    """Apply optional marker recovery before terminal retry precedence."""
    normalized_subtype = session.normalize_subtype(
        outcome, completion_marker, prior_completion_markers
    )
    if (
        normalized_subtype == "missing_completion_marker"
        and not completion_required
        and expected_output_patterns
        and infra_completed
        and _check_expected_patterns(session.result.strip(), expected_output_patterns)
    ):
        outcome = SessionOutcome.SUCCEEDED
        retry_reason = RetryReason.NONE
        needs_retry = False

    precedence = _retry_precedence(result, outcome, retry_reason)
    if precedence != (outcome, retry_reason):
        outcome, retry_reason = precedence
        needs_retry = outcome == SessionOutcome.RETRIABLE
    success = outcome == SessionOutcome.SUCCEEDED
    normalized_subtype = session.normalize_subtype(
        outcome, completion_marker, prior_completion_markers
    )
    return outcome, retry_reason, success, needs_retry, normalized_subtype


_CODEX_ERROR_CODE_API_STATUS: dict[str, int] = {
    "rate_limit_exceeded": 429,
}


def _adapt_agent_result(agent_result: AgentSessionResult) -> ClaudeSessionResult:
    raw = agent_result.raw

    session_id = agent_result.session_id or ""
    is_error = raw.get("is_error", not agent_result.success)
    result_text = agent_result.output
    subtype = CliSubtype.from_cli(raw.get("subtype", "unknown"))
    stop_reasons: list[str] = raw.get("stop_reasons", [])

    error_code: str = raw.get("error_code", "")

    error_subtypes = {
        CliSubtype.ERROR_DURING_EXECUTION,
        CliSubtype.ERROR_MAX_TURNS,
        CliSubtype.UNKNOWN,
    }
    jsonl_context_exhausted = subtype in error_subtypes and (
        error_code == CODEX_CONTEXT_EXHAUSTION_MARKER
        or CODEX_CONTEXT_EXHAUSTION_MARKER in (agent_result.error or "")
    )

    errors: list[str] = []
    if agent_result.error:
        errors.append(agent_result.error)

    api_error_status: int | None = _CODEX_ERROR_CODE_API_STATUS.get(error_code)

    token_usage = raw.get("canonical_token_usage") or raw.get("token_usage")

    command_executions: list[dict[str, Any]] = raw.get("command_executions", [])
    mcp_tool_calls: list[dict[str, Any]] = raw.get("mcp_tool_calls", [])
    file_change_paths: list[str] = raw.get("file_changes", [])
    file_change_entries = [
        {"name": "file_change", "type": "file_change", "file_path": p} for p in file_change_paths
    ]
    tool_uses = command_executions + mcp_tool_calls + file_change_entries

    assistant_messages: list[str] = raw.get("agent_messages", [])

    seen_ndjson_unknown_event_count: int = raw.get("ndjson_unknown_event_count", 0)
    seen_ndjson_unknown_item_count: int = raw.get("ndjson_unknown_item_count", 0)

    return ClaudeSessionResult(
        subtype=subtype,
        is_error=is_error,
        result=result_text,
        session_id=session_id,
        errors=errors,
        token_usage=token_usage,
        assistant_messages=assistant_messages,
        tool_uses=tool_uses,
        jsonl_context_exhausted=jsonl_context_exhausted,
        stop_reasons=stop_reasons,
        has_thinking_only_turn=False,
        seen_block_types=frozenset(),
        api_error_status=api_error_status,
        seen_ndjson_unknown_event_count=seen_ndjson_unknown_event_count,
        seen_ndjson_unknown_item_count=seen_ndjson_unknown_item_count,
    )


def _compute_write_evidence(
    session: ClaudeSessionResult,
    fs_writes_detected: bool,
    git_writes_detected: bool,
    backend: CodingAgentBackend,
    file_changes: Sequence[str] = (),
) -> WriteEvidence:
    write_names = backend.write_tool_names()
    write_call_count = sum(1 for t in session.tool_uses if t.get("name") in write_names)
    # Codex fallback: only count file_changes when no Write/Edit tool calls provide evidence.
    file_changes_count = len(file_changes) if write_call_count == 0 else 0
    return WriteEvidence(
        write_call_count=write_call_count,
        fs_writes_detected=fs_writes_detected,
        git_writes_detected=git_writes_detected,
        file_changes_count=file_changes_count,
    )


def _extract_file_changes(stdout: str, backend: CodingAgentBackend) -> list[str]:
    if backend.capabilities.write_detection_strategy == "tool_names":
        return []
    agent_result = backend.result_parser().parse_stdout(stdout)
    return list(agent_result.raw.get("file_changes", []))


def _stdout_mentions_write_tools(stdout: str) -> bool:
    """For truncated lines that fail JSON parsing, falls back to prefix + substring check."""
    _write_names = {"Write", "Edit"}
    for line in stdout.splitlines():
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            if (
                (line.startswith('{"type":"assistant"') or line.startswith('{"type": "assistant"'))
                and '"subagent_type"' not in line
                and '"tool_use"' in line
                and ('"Write"' in line or '"Edit"' in line)
                and not line.endswith("}}")
            ):
                return True
            continue
        if not _is_parent_assistant_record(obj):
            continue
        for block in obj.get("message", {}).get("content", []):
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") in _write_names
            ):
                return True
    return False


def _build_session_telemetry(
    *,
    skill_result: SkillResult,
    timing_seconds: float | None,
    audit_record: dict | None,
    github_api_log: GitHubApiLog | None,
    loc_insertions: int,
    loc_deletions: int,
    step_name: str = "",
    order_id: str = "",
) -> SessionTelemetry:
    if github_api_log is not None:
        _api_usage = github_api_log.drain_step(skill_result.session_id, step_name, order_id)
    else:
        _api_usage = None
    return SessionTelemetry(
        token_usage=skill_result.token_usage,
        timing_seconds=timing_seconds,
        audit_record=audit_record,
        github_api_usage=_api_usage,
        github_api_requests=_api_usage.get("total_requests", 0) if _api_usage else 0,
        loc_insertions=loc_insertions,
        loc_deletions=loc_deletions,
    )


def _build_error_path_telemetry(
    github_api_log: GitHubApiLog | None,
    session_id: str = "",
    step_name: str = "",
    order_id: str = "",
) -> SessionTelemetry:
    """Build SessionTelemetry for crash/cancel paths where no SkillResult exists."""
    if github_api_log is not None:
        _api_usage = github_api_log.drain_step(session_id, step_name, order_id)
    else:
        _api_usage = None
    return SessionTelemetry(
        token_usage=None,
        timing_seconds=None,
        audit_record=None,
        github_api_usage=_api_usage,
        github_api_requests=_api_usage.get("total_requests", 0) if _api_usage else 0,
        loc_insertions=0,
        loc_deletions=0,
    )
