"""Evidence computation, audit recording, and telemetry builders for headless sessions."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    CODEX_CONTEXT_EXHAUSTION_MARKER,
    WORKTREE_SKILLS,
    AgentSessionResult,
    CliSubtype,
    FailureRecord,
    RetryReason,
    SessionTelemetry,
    SkillResult,
    WriteEvidence,
    extract_skill_name,
    get_logger,
)
from autoskillit.execution.session._session_model import (
    ClaudeSessionResult,
    _is_parent_assistant_record,
)

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


_CODEX_ERROR_CODE_API_STATUS: dict[str, int] = {
    "rate_limit_exceeded": 429,
    "server_error": 500,
    "insufficient_quota": 402,
    "model_not_found": 404,
}


def _adapt_agent_result(agent_result: AgentSessionResult) -> ClaudeSessionResult:
    raw = agent_result.raw

    session_id = agent_result.session_id or ""
    is_error = raw.get("is_error", not agent_result.success)
    result_text = agent_result.output
    subtype = CliSubtype.from_cli(raw.get("subtype", "unknown"))
    stop_reasons: list[str] = raw.get("stop_reasons", [])

    error_code: str = raw.get("error_code", "")
    saw_failure: bool = raw.get("saw_failure", False)

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
        api_retry_exhausted=saw_failure and error_code != "",
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
    write_watch_dirs: Sequence[Path] = (),
    cwd: str = "",
    skill_command: str = "",
) -> WriteEvidence:
    write_names = backend.write_tool_names()

    # Determine if this dispatch requires tracked-tree writes:
    # A worktree skill's implementation evidence must come from outside
    # the .autoskillit/temp/ tree, regardless of how write_watch_dirs
    # was constructed (output_dir="." or default temp fallback).
    # For ~40 non-worktree skills, all writes count (temp IS their target).
    extracted = extract_skill_name(skill_command) if skill_command else None
    is_worktree_dispatch = bool(
        extracted and extracted in WORKTREE_SKILLS and cwd and write_watch_dirs
    )

    if is_worktree_dispatch:
        resolved_cwd = str(Path(cwd).resolve())
        temp_prefix = resolved_cwd + "/.autoskillit/temp/"
        tracked_write_count = 0
        for t in session.tool_uses:
            if t.get("name") not in write_names:
                continue
            tool_id = t.get("id")
            if tool_id is not None and tool_id in session.denied_tool_use_ids:
                continue
            file_path = t.get("file_path", "")
            if not file_path:
                continue
            resolved = str((Path(resolved_cwd) / file_path).resolve())
            if resolved.startswith(temp_prefix):
                continue
            tracked_write_count += 1
        write_call_count = tracked_write_count
    else:
        write_call_count = sum(
            1
            for t in session.tool_uses
            if t.get("name") in write_names and t.get("id") not in session.denied_tool_use_ids
        )

    # Codex fallback: file_changes paths also need filtering for worktree skills
    if write_call_count == 0 and file_changes:
        if is_worktree_dispatch:
            resolved_cwd = str(Path(cwd).resolve())
            temp_prefix = resolved_cwd + "/.autoskillit/temp/"
            tracked_file_changes = [
                fc
                for fc in file_changes
                if not str((Path(resolved_cwd) / fc).resolve()).startswith(temp_prefix)
            ]
            file_changes_count = len(tracked_file_changes)
        else:
            file_changes_count = len(file_changes)
    else:
        file_changes_count = 0

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
