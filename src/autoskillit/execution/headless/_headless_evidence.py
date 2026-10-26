"""Evidence computation, audit recording, and telemetry builders for headless sessions."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    CODEX_CONTEXT_EXHAUSTION_MARKER,
    AgentSessionResult,
    ChildOutcomeDict,
    CliSubtype,
    ExecutionIdentity,
    FailureRecord,
    RetryReason,
    SessionTelemetry,
    SkillResult,
    SubagentModelOutcomeDict,
    get_logger,
    is_parent_assistant_record,
)
from autoskillit.execution.session._exit_classification import _CODEX_ERROR_CODE_API_STATUS
from autoskillit.execution.session._session_model import ClaudeSessionResult

if TYPE_CHECKING:
    from autoskillit.core import AuditLog, CodingAgentBackend, GitHubApiLog

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


def _adapt_agent_result(agent_result: AgentSessionResult) -> ClaudeSessionResult:
    raw = agent_result.raw

    session_id = agent_result.session_id or ""
    is_error = raw.get("is_error", not agent_result.success)
    result_text = agent_result.output
    subtype = CliSubtype.from_cli(raw.get("subtype", "unknown"))
    stop_reasons: list[str] = raw.get("stop_reasons", [])

    raw_error_code = raw.get("error_code", "")
    error_code = raw_error_code if isinstance(raw_error_code, str) else ""

    error_subtypes = {
        CliSubtype.ERROR_DURING_EXECUTION,
        CliSubtype.ERROR_MAX_TURNS,
        CliSubtype.UNKNOWN,
    }
    jsonl_context_exhausted = subtype in error_subtypes and (
        error_code == CODEX_CONTEXT_EXHAUSTION_MARKER
        or CODEX_CONTEXT_EXHAUSTION_MARKER in (agent_result.error or "")
    )
    errors: list[str] = [agent_result.error] if agent_result.error else []
    errors.extend(raw.get("item_error_messages", []))
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
        turn_usage=raw.get("turn_usage", []),
        assistant_messages=assistant_messages,
        tool_uses=tool_uses,
        jsonl_context_exhausted=jsonl_context_exhausted,
        stop_reasons=stop_reasons,
        has_thinking_only_turn=False,
        seen_block_types=frozenset(),
        api_error_status=api_error_status,
        provider_error_code=error_code,
        seen_ndjson_unknown_event_count=seen_ndjson_unknown_event_count,
        seen_ndjson_unknown_item_count=seen_ndjson_unknown_item_count,
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
        if not is_parent_assistant_record(obj):
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
    session_id: str,
    subagent_model_outcomes: tuple[SubagentModelOutcomeDict, ...],
    child_outcomes: tuple[ChildOutcomeDict, ...],
    step_name: str = "",
    order_id: str = "",
) -> SessionTelemetry:
    if github_api_log is not None:
        _api_usage = github_api_log.drain_step(session_id, step_name, order_id)
    else:
        _api_usage = None
    return SessionTelemetry(
        token_usage=skill_result.token_usage,
        turn_usage=skill_result.turn_usage,
        timing_seconds=timing_seconds,
        audit_record=audit_record,
        github_api_usage=_api_usage,
        github_api_requests=_api_usage.get("total_requests", 0) if _api_usage else 0,
        loc_insertions=loc_insertions,
        loc_deletions=loc_deletions,
        subagent_model_outcomes=subagent_model_outcomes,
        child_outcomes=child_outcomes,
        execution_identity=skill_result.execution_identity,
    )


def _build_error_path_telemetry(
    github_api_log: GitHubApiLog | None,
    session_id: str = "",
    step_name: str = "",
    order_id: str = "",
    execution_identity: ExecutionIdentity = ExecutionIdentity(),
    subagent_model_outcomes: tuple[SubagentModelOutcomeDict, ...] = (),
    child_outcomes: tuple[ChildOutcomeDict, ...] = (),
    token_usage: dict[str, Any] | None = None,
) -> SessionTelemetry:
    """Build crash/cancel telemetry while retaining any drained token evidence."""
    _api_usage = None
    if github_api_log is not None:
        _api_usage = github_api_log.drain_step(session_id, step_name, order_id)
    return SessionTelemetry(
        token_usage=token_usage,
        turn_usage=[],
        timing_seconds=None,
        audit_record=None,
        github_api_usage=_api_usage,
        github_api_requests=_api_usage.get("total_requests", 0) if _api_usage else 0,
        loc_insertions=0,
        loc_deletions=0,
        subagent_model_outcomes=subagent_model_outcomes,
        child_outcomes=child_outcomes,
        execution_identity=execution_identity,
    )
