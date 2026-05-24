"""Evidence computation, audit recording, and telemetry builders for headless sessions."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    AgentSessionResult,
    CliSubtype,
    FailureRecord,
    RetryReason,
    SessionTelemetry,
    SkillResult,
    WriteEvidence,
    get_logger,
)
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


def _adapt_agent_result(agent_result: AgentSessionResult) -> ClaudeSessionResult:
    raw = agent_result.raw

    session_id = agent_result.session_id or ""
    is_error = raw.get("is_error", not agent_result.success)
    result_text = agent_result.output
    subtype = CliSubtype.from_cli(raw.get("subtype", "unknown"))
    stop_reasons: list[str] = raw.get("stop_reasons", [])

    error_subtypes = {
        CliSubtype.ERROR_DURING_EXECUTION,
        CliSubtype.ERROR_MAX_TURNS,
        CliSubtype.UNKNOWN,
    }
    jsonl_context_exhausted = subtype in error_subtypes and "context_length_exceeded" in (
        agent_result.error or ""
    )

    token_usage = raw.get("canonical_token_usage") or raw.get("token_usage")

    command_executions: list[dict[str, Any]] = raw.get("command_executions", [])
    mcp_tool_calls: list[dict[str, Any]] = raw.get("mcp_tool_calls", [])
    file_change_paths: list[str] = raw.get("file_changes", [])
    file_change_entries = [
        {"name": "file_change", "type": "file_change", "path": p} for p in file_change_paths
    ]
    tool_uses = command_executions + mcp_tool_calls + file_change_entries

    assistant_messages: list[str] = raw.get("agent_messages", [])

    return ClaudeSessionResult(
        subtype=subtype,
        is_error=is_error,
        result=result_text,
        session_id=session_id,
        token_usage=token_usage,
        assistant_messages=assistant_messages,
        tool_uses=tool_uses,
        jsonl_context_exhausted=jsonl_context_exhausted,
        stop_reasons=stop_reasons,
        has_thinking_only_turn=False,
        seen_block_types=frozenset(),
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
    if backend.name == AGENT_BACKEND_CLAUDE_CODE:
        return []
    agent_result = backend.result_parser().parse_stdout(stdout)
    return list(agent_result.raw.get("file_changes", []))


def _stdout_mentions_write_tools(stdout: str) -> bool:
    return '"Edit"' in stdout or '"Write"' in stdout


def _build_session_telemetry(
    *,
    skill_result: SkillResult,
    timing_seconds: float | None,
    audit_record: dict | None,
    github_api_log: GitHubApiLog | None,
    loc_insertions: int,
    loc_deletions: int,
) -> SessionTelemetry:
    _api_usage = (
        github_api_log.drain(skill_result.session_id) if github_api_log is not None else None
    )
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
) -> SessionTelemetry:
    """Build SessionTelemetry for crash/cancel paths where no SkillResult exists."""
    _api_usage = github_api_log.drain(session_id) if github_api_log is not None else None
    return SessionTelemetry(
        token_usage=None,
        timing_seconds=None,
        audit_record=None,
        github_api_usage=_api_usage,
        github_api_requests=_api_usage.get("total_requests", 0) if _api_usage else 0,
        loc_insertions=0,
        loc_deletions=0,
    )
