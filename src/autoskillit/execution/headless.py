"""Headless Claude Code session orchestration.

L1 module (execution/). Owns the full lifecycle of a headless claude CLI session:
command preparation, subprocess invocation via the injected runner, and
SkillResult construction.

Public API:
    run_headless_core(skill_command, cwd, ctx, *, ...) -> SkillResult
"""

from __future__ import annotations

import dataclasses
import os
import re
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from autoskillit.core import (
    ClaudeFlags,
    FailureRecord,
    RetryReason,
    SessionOutcome,
    SkillResult,
    TerminationReason,
    claude_code_project_dir,
    get_logger,
)
from autoskillit.execution.commands import build_headless_cmd
from autoskillit.execution.process import _marker_is_standalone
from autoskillit.execution.session import (
    ClaudeSessionResult,
    _compute_outcome,
    _compute_success,
    _truncate,
    parse_session_result,
)

if TYPE_CHECKING:
    from autoskillit.config import (
        AutomationConfig,
    )
    from autoskillit.core import AuditStore, SubprocessResult
    from autoskillit.pipeline.context import (
        ToolContext,
    )

logger = get_logger(__name__)


def _ensure_skill_prefix(skill_command: str) -> str:
    """Prompt-formatting helper: prepend 'Use ' to slash-commands for headless session loading.

    This is NOT a validator. Non-slash input passes through unchanged by design —
    runtime validation is enforced by the skill_command_guard PreToolUse hook.
    """
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
    log_dir = claude_code_project_dir(cwd)
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
    if not any(
        _marker_is_standalone(msg, completion_marker) for msg in session.assistant_messages
    ):
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
        logger.debug("model_resolved", tier="override", model=config.model.override)
        return config.model.override
    if step_model:
        logger.debug("model_resolved", tier="step", model=step_model)
        return step_model
    if config.model.default:
        logger.debug("model_resolved", tier="default", model=config.model.default)
        return config.model.default
    logger.debug("model_resolved", tier="none", model=None)
    return None


_WORKTREE_PATH_PATTERN: re.Pattern[str] = re.compile(r"^worktree_path=(.+)$", re.MULTILINE)


def _extract_worktree_path(assistant_messages: list[str]) -> str | None:
    """Return the last absolute path emitted as worktree_path=<value>."""
    last: str | None = None
    for msg in assistant_messages:
        m = _WORKTREE_PATH_PATTERN.search(msg)
        if m:
            candidate = m.group(1).strip()
            if os.path.isabs(candidate):
                last = candidate
    return last


def _apply_budget_guard(
    sr: SkillResult,
    skill_command: str,
    audit: AuditStore | None,
    max_consecutive_retries: int,
) -> SkillResult:
    """Override needs_retry to False when the consecutive-failure budget is exhausted.

    The audit log records the raw failure (needs_retry=True) before this guard
    runs; the guard is a post-processing filter on the returned SkillResult only.
    """
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


def _build_skill_result(
    result: SubprocessResult,
    completion_marker: str = "",
    skill_command: str = "",
    audit: AuditStore | None = None,
    max_consecutive_retries: int = 3,
    expected_output_patterns: Sequence[str] = (),
) -> SkillResult:
    """Route SubprocessResult fields into the standard run_skill response."""
    branch = (
        "stale"
        if result.termination == TerminationReason.STALE
        else "timed_out"
        if result.termination == TerminationReason.TIMED_OUT
        else "normal"
    )
    logger.debug(
        "build_skill_result_entry",
        termination=str(result.termination),
        returncode=result.returncode,
        channel=str(result.channel_confirmation),
        pid=result.pid,
        stdout_len=len(result.stdout),
        stderr_len=len(result.stderr),
        branch=branch,
    )
    if result.termination == TerminationReason.STALE:
        # Attempt to recover from stdout before declaring stale failure.
        stale_session = parse_session_result(result.stdout)
        stale_returncode = result.returncode if result.returncode is not None else -1
        can_attempt_stale_recovery = (
            stale_session.subtype == "success"
            and stale_session.result.strip()
            and not stale_session.is_error
        )
        if can_attempt_stale_recovery:
            success = _compute_success(
                stale_session,
                stale_returncode,
                TerminationReason.COMPLETED,
                completion_marker=completion_marker,
                channel_confirmation=result.channel_confirmation,
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
        stale_sr = SkillResult(
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
        return _apply_budget_guard(stale_sr, skill_command, audit, max_consecutive_retries)

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

    # Recovery check: attempt before _compute_outcome so the recovered session
    # is the input for outcome computation rather than the original.
    if completion_marker:
        recovered = _recover_from_separate_marker(session, completion_marker)
        if recovered is not None:
            session = recovered

    outcome, retry_reason = _compute_outcome(
        session,
        returncode,
        result.termination,
        completion_marker,
        channel_confirmation=result.channel_confirmation,
        expected_output_patterns=expected_output_patterns,
    )
    success = outcome == SessionOutcome.SUCCEEDED
    needs_retry = outcome == SessionOutcome.RETRIABLE

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

    extracted_worktree_path: str | None = None
    if needs_retry:
        extracted_worktree_path = _extract_worktree_path(session.assistant_messages)

    sr = SkillResult(
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
        worktree_path=extracted_worktree_path,
    )
    sr = _apply_budget_guard(sr, skill_command, audit, max_consecutive_retries)
    logger.debug(
        "build_skill_result_exit",
        success=sr.success,
        subtype=sr.subtype,
        needs_retry=sr.needs_retry,
        retry_reason=str(sr.retry_reason),
        is_error=sr.is_error,
        result_len=len(sr.result),
    )
    return sr


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
    expected_output_patterns: Sequence[str] = (),
) -> SkillResult:
    """Shared headless runner used by run_skill.

    Does NOT check open_kitchen gate — callers in server.py are responsible.
    Accepts explicit ToolContext so this module has no server.py dependency.
    """
    cfg = ctx.config.run_skill
    original_skill_command = skill_command

    with structlog.contextvars.bound_contextvars(
        skill_command=original_skill_command[:100],
        step_name=step_name or None,
    ):
        skill_command = _inject_completion_directive(
            _ensure_skill_prefix(skill_command), cfg.completion_marker
        )
        effective_plugin_dir = ctx.plugin_dir
        resolved_model = _resolve_model(model, ctx.config)
        spec = build_headless_cmd(skill_command, model=resolved_model)
        cmd = spec.cmd + [
            ClaudeFlags.PLUGIN_DIR,
            effective_plugin_dir,
            ClaudeFlags.OUTPUT_FORMAT,
            cfg.output_format.value,
        ]
        # Apply any CLI flags required by the chosen output format.
        for flag in cfg.output_format.required_cli_flags:
            if flag not in cmd:
                cmd.append(flag)
        if add_dir:
            cmd.extend([ClaudeFlags.ADD_DIR, add_dir])

        env_vars = ["AUTOSKILLIT_HEADLESS=1"]
        delay_ms = cfg.exit_after_stop_delay_ms
        if delay_ms > 0:
            env_vars.append(f"CLAUDE_CODE_EXIT_AFTER_STOP_DELAY={delay_ms}")
        cmd = ["env"] + env_vars + cmd

        effective_timeout = timeout if timeout is not None else cfg.timeout
        effective_stale = stale_threshold if stale_threshold is not None else cfg.stale_threshold

        logger.debug(
            "run_headless_core_entry",
            cwd=cwd,
            resolved_model=resolved_model,
            timeout=effective_timeout,
            stale_threshold=effective_stale,
            plugin_dir=str(effective_plugin_dir),
            add_dir=add_dir or None,
        )

        runner = ctx.runner
        assert runner is not None, "No subprocess runner configured"

        linux_tracing_cfg = ctx.config.linux_tracing
        _start_ts = datetime.now(UTC).isoformat()
        _start_mono = time.monotonic()

        _result: SubprocessResult | None = None
        try:
            _result = await runner(
                cmd,
                cwd=Path(cwd),
                timeout=effective_timeout,
                pty_mode=True,
                session_log_dir=_session_log_dir(cwd),
                completion_marker=cfg.completion_marker,
                stale_threshold=effective_stale,
                completion_drain_timeout=cfg.completion_drain_timeout,
                linux_tracing_config=linux_tracing_cfg,
            )
        finally:
            if _result is None:
                # Runner raised — write a crash entry so sessions.jsonl stays consistent
                _log_dir = ctx.config.linux_tracing.log_dir
                try:
                    from autoskillit.execution import flush_session_log

                    flush_session_log(
                        log_dir=_log_dir,
                        cwd=str(cwd),
                        session_id="",
                        pid=0,
                        skill_command=skill_command,
                        success=False,
                        subtype="crashed",
                        exit_code=-1,
                        start_ts=_start_ts,
                        proc_snapshots=None,
                        termination_reason="CRASHED",
                    )
                except Exception:
                    logger.debug("flush_session_log during crash failed", exc_info=True)
        if _result is None:
            raise RuntimeError("runner() did not return a result — cannot build SkillResult")
        _elapsed = time.monotonic() - _start_mono
        _end_ts = (datetime.fromisoformat(_start_ts) + timedelta(seconds=_elapsed)).isoformat()
        result = dataclasses.replace(  # type: ignore[arg-type]
            _result, start_ts=_start_ts, end_ts=_end_ts, elapsed_seconds=_elapsed
        )

        audit_count_before = len(ctx.audit.get_report())
        skill_result = _build_skill_result(
            result,
            completion_marker=cfg.completion_marker,
            skill_command=original_skill_command,
            audit=ctx.audit,
            expected_output_patterns=expected_output_patterns,
        )

        # Use monotonic elapsed_seconds — authoritative wall-clock timing set by time.monotonic()
        # brackets in run_managed_async. Never re-derive from ISO strings (backward-clock risk).
        timing_seconds: float = result.elapsed_seconds

        # Extract the audit record (if any) added by this session
        new_audit_records = ctx.audit.get_report_as_dicts()[audit_count_before:]
        audit_record = new_audit_records[0] if new_audit_records else None

        # Resolve effective session ID: prefer stdout-parsed, fall back to Channel B discovery
        effective_session_id = skill_result.session_id or result.channel_b_session_id

        if result.proc_snapshots is not None or not skill_result.success or bool(step_name):
            from autoskillit.execution.session_log import flush_session_log

            try:
                flush_session_log(
                    log_dir=ctx.config.linux_tracing.log_dir,
                    cwd=cwd,
                    session_id=effective_session_id,
                    pid=result.pid,
                    skill_command=original_skill_command,
                    success=skill_result.success,
                    subtype=skill_result.subtype,
                    exit_code=skill_result.exit_code,
                    start_ts=result.start_ts,
                    end_ts=result.end_ts,
                    elapsed_seconds=result.elapsed_seconds,
                    termination_reason=result.termination.value,
                    snapshot_interval_seconds=ctx.config.linux_tracing.proc_interval,
                    proc_snapshots=result.proc_snapshots,
                    step_name=step_name,
                    token_usage=skill_result.token_usage,
                    timing_seconds=timing_seconds,
                    audit_record=audit_record,
                )
            except Exception:
                logger.debug("session_log_flush_failed", exc_info=True)

        logger.debug(
            "run_headless_core_exit",
            success=skill_result.success,
            needs_retry=skill_result.needs_retry,
            subtype=skill_result.subtype,
            session_id=skill_result.session_id,
        )

        if step_name:
            ctx.token_log.record(
                step_name,
                skill_result.token_usage,
                start_ts=result.start_ts,
                end_ts=result.end_ts,
                elapsed_seconds=result.elapsed_seconds,
            )
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
        expected_output_patterns: Sequence[str] = (),
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
            expected_output_patterns=expected_output_patterns,
        )
