"""Headless Claude Code session orchestration.

execution/ module. Owns the full lifecycle of a headless claude CLI session:
command preparation, subprocess invocation via the injected runner, and
SkillResult construction.

Public API:
    run_headless_core(skill_command, cwd, ctx, *, ...) -> SkillResult
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import FailureRecord, RetryReason, SkillResult, TerminationReason, get_logger
from autoskillit.execution.commands import build_headless_cmd
from autoskillit.execution.session import (
    ClaudeSessionResult,
    _compute_retry,
    _compute_success,
    _truncate,
    parse_session_result,
)

if TYPE_CHECKING:
    from autoskillit.config import AutomationConfig
    from autoskillit.core import AuditStore, SubprocessResult
    from autoskillit.pipeline.context import ToolContext

logger = get_logger(__name__)


def _ensure_skill_prefix(skill_command: str) -> str:
    """Ensure skill commands start with 'Use' for headless session loading."""
    stripped = skill_command.strip()
    if stripped.startswith("/"):
        return f"Use {stripped}"
    return skill_command


def _inject_completion_directive(skill_command: str, marker: str) -> str:
    """Append an orchestration directive to make the session write a completion marker."""
    directive = (
        f"\n\nORCHESTRATION DIRECTIVE: When your task is complete, "
        f"your final text output MUST end with: {marker}\n"
        f"CRITICAL: Append {marker} at the very end of your substantive response, "
        f"in the SAME message. Do NOT output {marker} as a separate standalone message."
    )
    return skill_command + directive


def _session_log_dir(cwd: str) -> Path:
    """Derive Claude Code session log directory from project cwd."""
    project_hash = cwd.replace("/", "-").replace("_", "-")
    log_dir = Path.home() / ".claude" / "projects" / project_hash
    logger.info("session_log_dir_computed", path=str(log_dir), cwd=cwd)
    if not log_dir.exists():
        logger.warning("session_log_dir_missing", path=str(log_dir), cwd=cwd)
    return log_dir


def _capture_failure(
    skill_command: str,
    exit_code: int,
    subtype: str,
    needs_retry: bool,
    retry_reason: str,
    stderr: str,
    audit: AuditStore | None,
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


def _recover_from_separate_marker(
    session: ClaudeSessionResult,
    completion_marker: str,
) -> ClaudeSessionResult | None:
    """Attempt recovery when the model emitted the completion marker as a standalone
    final message rather than inline with its substantive output.

    Returns a reconstructed ClaudeSessionResult whose result field contains the
    combined assistant message content (including the marker), or None if recovery
    is not possible (no assistant content, or no substantive content beyond the marker).
    """
    if not session.assistant_messages:
        return None
    if not any(completion_marker in msg for msg in session.assistant_messages):
        return None
    combined = "\n\n".join(session.assistant_messages)
    stripped = combined.replace(completion_marker, "").strip()
    if not stripped:
        return None  # only the marker exists — genuine failure, do not recover
    logger.warning(
        "completion_marker_in_separate_message",
        recovery="rebuilding result from assistant_messages",
    )
    return dataclasses.replace(session, result=combined)


def _resolve_model(step_model: str, config: AutomationConfig) -> str | None:
    """Resolve model selection: config override > step > config default."""
    if config.model.override:
        return config.model.override
    if step_model:
        return step_model
    if config.model.default:
        return config.model.default
    return None


def _build_skill_result(
    result: SubprocessResult,
    completion_marker: str = "",
    skill_command: str = "",
    audit: AuditStore | None = None,
) -> SkillResult:
    """Route SubprocessResult fields into the standard run_skill response."""
    if result.termination == TerminationReason.STALE:
        # Attempt to recover from stdout before declaring stale failure.
        stale_session = parse_session_result(result.stdout)
        if (
            stale_session.subtype == "success"
            and stale_session.result.strip()
            and not stale_session.is_error
        ):
            stale_returncode = result.returncode if result.returncode is not None else -1
            success = _compute_success(
                stale_session,
                stale_returncode,
                TerminationReason.COMPLETED,
                completion_marker=completion_marker,
            )
            if success:
                logger.warning(
                    "Session went stale but stdout contained a valid result; recovering"
                )
                return SkillResult(
                    success=True,
                    result=_truncate(stale_session.agent_result),
                    session_id=stale_session.session_id,
                    subtype="recovered_from_stale",
                    is_error=False,
                    exit_code=stale_returncode,
                    needs_retry=False,
                    retry_reason=RetryReason.NONE,
                    stderr=result.stderr if result.stderr else "",
                    token_usage=stale_session.token_usage,
                )
        # No valid result in stdout — fall through to original stale response
        _capture_failure(
            skill_command,
            exit_code=result.returncode if result.returncode is not None else -1,
            subtype="stale",
            needs_retry=True,
            retry_reason=RetryReason.RESUME,
            stderr=result.stderr if result.stderr else "",
            audit=audit,
        )
        return SkillResult(
            success=False,
            result=(
                "Session went stale (no activity for configured threshold). "
                "Partial progress may have been made. Retry to continue."
            ),
            session_id="",
            subtype="stale",
            is_error=False,
            exit_code=-1,
            needs_retry=True,
            retry_reason=RetryReason.RESUME,
            stderr="",
            token_usage=None,
        )

    if result.termination == TerminationReason.TIMED_OUT:
        returncode = -1
        session = ClaudeSessionResult(
            subtype="timeout",
            is_error=True,
            result=_truncate(result.stdout) if result.stdout.strip() else "",
            session_id="",
            errors=[],
        )
    else:
        returncode = result.returncode if result.returncode is not None else -1
        session = parse_session_result(result.stdout)

    success = _compute_success(session, returncode, result.termination, completion_marker)
    needs_retry, retry_reason = _compute_retry(session, returncode, result.termination)

    if not success and completion_marker:
        recovered = _recover_from_separate_marker(session, completion_marker)
        if recovered is not None:
            recovered_success = _compute_success(
                recovered, returncode, result.termination, completion_marker
            )
            if recovered_success:
                session = recovered
                success = True
                needs_retry = False
                retry_reason = RetryReason.NONE

    if not success or needs_retry:
        _capture_failure(
            skill_command,
            exit_code=returncode,
            subtype=session.subtype,
            needs_retry=needs_retry,
            retry_reason=retry_reason.value,
            stderr=result.stderr if result.stderr else "",
            audit=audit,
        )

    result_text = _truncate(session.agent_result)
    if completion_marker:
        result_text = result_text.replace(completion_marker, "").strip()

    return SkillResult(
        success=success,
        result=result_text,
        session_id=session.session_id,
        subtype=session.subtype,
        is_error=session.is_error,
        exit_code=returncode,
        needs_retry=needs_retry,
        retry_reason=retry_reason,
        stderr=_truncate(result.stderr),
        token_usage=session.token_usage,
    )


async def run_headless_core(
    skill_command: str,
    cwd: str,
    ctx: ToolContext,
    *,
    model: str = "",
    step_name: str = "",
    add_dir: str = "",
    timeout: float | None = None,
    stale_threshold: float | None = None,
) -> SkillResult:
    """Shared headless runner used by run_skill and run_skill_retry.

    Does NOT check open_kitchen gate — callers in server.py are responsible.
    Accepts explicit ToolContext so this module has no server.py dependency.
    """
    cfg = ctx.config.run_skill
    original_skill_command = skill_command
    skill_command = _inject_completion_directive(
        _ensure_skill_prefix(skill_command), cfg.completion_marker
    )
    effective_plugin_dir = ctx.plugin_dir
    resolved_model = _resolve_model(model, ctx.config)
    spec = build_headless_cmd(skill_command, model=resolved_model)
    cmd = spec.cmd + ["--plugin-dir", effective_plugin_dir, "--output-format", "json"]
    if add_dir:
        cmd.extend(["--add-dir", add_dir])

    delay_ms = cfg.exit_after_stop_delay_ms
    if delay_ms > 0:
        cmd = ["env", f"CLAUDE_CODE_EXIT_AFTER_STOP_DELAY={delay_ms}"] + cmd

    runner = ctx.runner
    assert runner is not None, "No subprocess runner configured"
    result = await runner(
        cmd,
        cwd=Path(cwd),
        timeout=timeout if timeout is not None else cfg.timeout,
        pty_mode=True,
        heartbeat_marker=cfg.heartbeat_marker,
        session_log_dir=_session_log_dir(cwd),
        completion_marker=cfg.completion_marker,
        stale_threshold=stale_threshold if stale_threshold is not None else cfg.stale_threshold,
        completion_drain_timeout=cfg.completion_drain_timeout,
    )

    skill_result = _build_skill_result(
        result,
        completion_marker=cfg.completion_marker,
        skill_command=original_skill_command,
        audit=ctx.audit,
    )
    if step_name:
        ctx.token_log.record(step_name, skill_result.token_usage)
    return skill_result


class DefaultHeadlessExecutor:
    """Concrete HeadlessExecutor backed by run_headless_core."""

    def __init__(self, ctx: ToolContext) -> None:
        self._ctx = ctx

    async def run(
        self,
        skill_command: str,
        cwd: str,
        *,
        model: str = "",
        step_name: str = "",
        add_dir: str = "",
        timeout: float | None = None,
        stale_threshold: float | None = None,
    ) -> SkillResult:
        cfg = self._ctx.config.run_skill
        effective_timeout = timeout if timeout is not None else cfg.timeout
        effective_stale = stale_threshold if stale_threshold is not None else cfg.stale_threshold
        return await run_headless_core(
            skill_command,
            cwd,
            self._ctx,
            model=model,
            step_name=step_name,
            add_dir=add_dir,
            timeout=effective_timeout,
            stale_threshold=effective_stale,
        )
