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
import traceback
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, assert_never

import anyio
import structlog

from autoskillit.core import (
    ChannelConfirmation,
    CliSubtype,
    FailureRecord,
    KillReason,
    RetryReason,
    SessionOutcome,
    SkillResult,
    TerminationReason,
    ValidatedAddDir,
    WriteBehaviorSpec,
    claude_code_project_dir,
    collect_version_snapshot,
    get_logger,
    is_git_worktree,
    load_yaml,
    pkg_root,
    temp_dir_display_str,
)
from autoskillit.execution._headless_scan import _scan_jsonl_write_paths
from autoskillit.execution.clone_guard import (
    check_and_revert_clone_contamination,
    is_worktree_skill,
    snapshot_clone_state,
)
from autoskillit.execution.commands import build_full_headless_cmd, build_headless_resume_cmd
from autoskillit.execution.process import _marker_is_standalone
from autoskillit.execution.recording import RecordingSubprocessRunner
from autoskillit.execution.session import (
    ClaudeSessionResult,
    _check_expected_patterns,
    _compute_outcome,
    _compute_success,
    _normalize_subtype,
    _truncate,
    parse_session_result,
)

if TYPE_CHECKING:
    from autoskillit.config import (
        AutomationConfig,
    )
    from autoskillit.core import AuditLog, SubprocessResult, SubprocessRunner
    from autoskillit.pipeline.context import (
        ToolContext,
    )

logger = get_logger(__name__)

# Subtypes eligible for Channel B drain-race recovery.
#
# These are the failure subtypes that can arise when Claude Code defers ``type=result``
# until all background agents finish.  The deferred record is never flushed if the
# process tree is killed after Channel B fires on the session JSONL marker.
#
# TIMEOUT is excluded — it indicates a genuine time limit breach, not a drain-race.
# UNKNOWN is excluded — it indicates unrecognised CLI behaviour, not a missing record.
_CHANNEL_B_RECOVERABLE_SUBTYPES: frozenset[CliSubtype] = frozenset(
    {CliSubtype.UNPARSEABLE, CliSubtype.EMPTY_OUTPUT}
)

_PATH_CAPTURE: re.Pattern[str] = re.compile(r"^(\w+)\\s\*=\\s\*/.+")


def _session_log_dir(cwd: str) -> Path:
    """Derive Claude Code session log directory from project cwd.

    Pre-creates the directory if absent so Channel B always has a directory
    to poll.  Without this, a fresh clone path whose encoded project dir
    doesn't exist yet causes ``_session_log_monitor`` to burn its entire
    phase-1 timeout absorbing ``OSError``, ultimately producing a false
    ``EMPTY_OUTPUT`` retry.
    """
    log_dir = claude_code_project_dir(cwd)
    logger.info("session_log_dir_computed", path=str(log_dir), cwd=cwd)
    if not log_dir.exists():
        logger.info("session_log_dir_precreating", path=str(log_dir), cwd=cwd)
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("session_log_dir_mkdir_failed", path=str(log_dir), cwd=cwd)
            raise
    return log_dir


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


def _recover_block_from_assistant_messages(
    session: ClaudeSessionResult,
    expected_output_patterns: Sequence[str],
) -> ClaudeSessionResult | None:
    """When session.result lacks expected_output_patterns (drain-race condition
    on either channel), attempt to find the patterns in session.assistant_messages.
    If found, return a new ClaudeSessionResult with result reconstructed from
    assistant_messages. Return None if patterns cannot be found there either.
    """
    if not session.assistant_messages or not expected_output_patterns:
        return None
    combined = "\n\n".join(session.assistant_messages)
    if not _check_expected_patterns(combined, expected_output_patterns):
        return None
    logger.warning(
        "pattern_recovered_from_assistant_messages",
        patterns=list(expected_output_patterns),
    )
    # Preserve any content already drained into session.result before appending
    # the recovered assistant_messages block.
    recovered = (session.result + "\n\n" + combined) if session.result else combined
    return dataclasses.replace(session, result=recovered)


def _synthesize_from_write_artifacts(
    session: ClaudeSessionResult,
    expected_output_patterns: list[str],
    write_call_count: int,
) -> ClaudeSessionResult | None:
    """Synthesize missing structured output tokens from Write/Edit tool_use file_path data.

    When the session has write evidence (write_call_count >= 1) and expected_output_patterns
    contain path-capture patterns (e.g., ``plan_path\\s*=\\s*/.+``), scan tool_uses for
    Write/Edit entries with absolute file_path values. For each pattern whose token name can
    be extracted, inject ``{token_name} = {file_path}`` into session.result so that
    _compute_outcome sees the token as if the model had emitted it.

    Returns a new ClaudeSessionResult with the injected line prepended to result, or None if
    synthesis is not possible (no matching file_path, no path-capture patterns, or pattern
    already satisfied).
    """
    if write_call_count == 0:
        return None

    # Only synthesize for path-capture patterns (token_name\s*=\s*/.+).
    # Non-path patterns (verdict=, merged=) must remain text-compliance-only.

    synthesized_lines: list[str] = []
    for pattern in expected_output_patterns:
        m = _PATH_CAPTURE.match(pattern)
        if not m:
            continue
        token_name = m.group(1)
        # Skip if the pattern is already satisfied in the current result.
        if re.search(pattern, session.result):
            continue
        # Collect ALL absolute Write/Edit paths; use the LAST one (final deliverable).
        # Multi-artifact skills write intermediate files first, final deliverable last.
        candidate_paths = [
            t.get("file_path", "")
            for t in session.tool_uses
            if t.get("name") in {"Write", "Edit"} and t.get("file_path", "").startswith("/")
        ]
        if candidate_paths:
            synthesized_lines.append(f"{token_name} = {candidate_paths[-1]}")

    if not synthesized_lines:
        return None

    injected = "\n".join(synthesized_lines) + "\n" + session.result
    return dataclasses.replace(session, result=injected)


def _extract_missing_token_hints(
    stdout: str,
    expected_output_patterns: Sequence[str],
) -> list[tuple[str, str]]:
    """Extract (token_name, write_path) pairs for patterns missing from the result.

    Parses raw NDJSON stdout to find Write/Edit tool_use file_path entries,
    then matches them against path-capture patterns that are NOT satisfied in
    the result text. Returns the hints needed to build the nudge prompt.
    """
    session = parse_session_result(stdout)
    hints: list[tuple[str, str]] = []

    for pattern in expected_output_patterns:
        m = _PATH_CAPTURE.match(pattern)
        if not m:
            continue
        token_name = m.group(1)
        # Skip if already satisfied
        if re.search(pattern, session.result):
            continue
        # Collect absolute Write/Edit paths; use the LAST one (final deliverable)
        candidate_paths = [
            t.get("file_path", "")
            for t in session.tool_uses
            if t.get("name") in {"Write", "Edit"} and t.get("file_path", "").startswith("/")
        ]
        if candidate_paths:
            hints.append((token_name, candidate_paths[-1]))

    return hints


_NUDGE_TIMEOUT: float = 60.0


async def _attempt_contract_nudge(
    skill_result: SkillResult,
    subprocess_result: SubprocessResult,
    expected_output_patterns: Sequence[str],
    completion_marker: str,
    cwd: str,
    runner: SubprocessRunner,
) -> SkillResult | None:
    """Attempt a lightweight resume nudge to recover missing structured output tokens.

    When ``_build_skill_result`` returns CONTRACT_RECOVERY, the model wrote the artifact
    but omitted the structured output token. Instead of a full retry, resume the same
    session with a short feedback prompt asking the model to emit the missing token.

    Returns a patched SkillResult(success=True) on success, or None to indicate the
    nudge failed and the caller should fall through to the original CONTRACT_RECOVERY path.
    """
    hints = _extract_missing_token_hints(subprocess_result.stdout, expected_output_patterns)
    if not hints:
        logger.debug("nudge_skip_no_hints")
        return None

    # Build the feedback prompt
    token_lines = "\n".join(f"{name} = {path}" for name, path in hints)
    prompt = (
        "You completed your task and wrote the output file, but you omitted the "
        "required structured output token in your final text response.\n\n"
        f"Please emit ONLY the following (no other text):\n"
        f"{token_lines}\n"
        f"{completion_marker}"
    )

    spec = build_headless_resume_cmd(
        resume_session_id=skill_result.session_id,
        prompt=prompt,
        output_format="json",
    )

    try:
        nudge_result = await runner(
            spec.cmd,
            cwd=Path(cwd),
            timeout=_NUDGE_TIMEOUT,
            env=spec.env,
        )
    except OSError:
        logger.debug("nudge_runner_failed", exc_info=True)
        return None
    except Exception:
        logger.warning("nudge_runner_failed_unexpected", exc_info=True)
        return None

    # Parse the nudge response and check for the missing patterns
    nudge_session = parse_session_result(nudge_result.stdout)
    combined_result = skill_result.result + "\n" + nudge_session.result

    if not _check_expected_patterns(combined_result, list(expected_output_patterns)):
        logger.debug(
            "nudge_patterns_not_found",
            nudge_result_len=len(nudge_session.result),
        )
        return None

    logger.info(
        "nudge_recovery_success",
        session_id=skill_result.session_id,
        nudge_output_count=nudge_session.token_usage.get("output_tokens", 0)
        if nudge_session.token_usage
        else 0,
    )
    return dataclasses.replace(
        skill_result,
        success=True,
        result=combined_result,
        subtype="success",
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        token_usage=_merge_token_usage(skill_result.token_usage, nudge_session.token_usage),
    )


def _merge_token_usage(
    base: dict[str, object] | None,
    nudge: dict[str, object] | None,
) -> dict[str, object] | None:
    """Additively merge token usage dicts from main session and nudge."""
    if base is None:
        return nudge
    if nudge is None:
        return base
    merged = dict(base)
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        base_val = base.get(key, 0)
        nudge_val = nudge.get(key, 0)
        if isinstance(base_val, (int, float)) and isinstance(nudge_val, (int, float)):
            merged[key] = base_val + nudge_val
    return merged


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


_WORKTREE_PATH_PATTERN: re.Pattern[str] = re.compile(r"^worktree_path\s*=\s*(.+)$", re.MULTILINE)


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


# Intentionally excluded: these tokens are handled by dedicated extractors
# (_WORKTREE_PATH_PATTERN for worktree_path; branch_name is used as a string,
# not for path-contamination checks).
_INTENTIONALLY_EXCLUDED_PATH_TOKENS: frozenset[str] = frozenset(
    {
        "worktree_path",
        "branch_name",
    }
)


def _build_path_token_set() -> frozenset[str]:
    """Derive the set of file-path output token names from skill_contracts.yaml.

    This replaces the manually-maintained frozenset and ensures new skills added
    to the contracts file are automatically included in path-contamination checks.
    Falls back to an empty frozenset if the manifest is unavailable (e.g., in
    test environments where the package is not installed).

    Filters outputs where type starts with "file_path" (covers both "file_path"
    and "file_path_list"). The outputs section in skill_contracts.yaml is a list
    of dicts with "name" and "type" keys — not a mapping.

    Loads the YAML directly via L0 core utilities to avoid an upward L1→L2 import.
    """
    try:
        manifest_path = pkg_root() / "recipe" / "skill_contracts.yaml"
        manifest = load_yaml(manifest_path)
        if not isinstance(manifest, dict):
            logger.debug(
                "skill_contracts.yaml is empty or non-dict; _OUTPUT_PATH_TOKENS will be empty"
            )
            return frozenset()
        result = (
            frozenset(
                out["name"]
                for skill_data in manifest.get("skills", {}).values()
                for out in skill_data.get("outputs", [])
                if isinstance(out, dict) and out.get("type", "").startswith("file_path")
            )
            - _INTENTIONALLY_EXCLUDED_PATH_TOKENS
        )
        logger.debug("_OUTPUT_PATH_TOKENS derived from contracts", count=len(result))
        return result
    except FileNotFoundError:
        logger.debug("skill_contracts.yaml not found; _OUTPUT_PATH_TOKENS will be empty")
        return frozenset()
    except Exception:
        logger.warning("Failed to derive _OUTPUT_PATH_TOKENS from contracts YAML", exc_info=True)
        return frozenset()


_OUTPUT_PATH_TOKENS: frozenset[str] = _build_path_token_set()

_OUTPUT_PATH_PATTERN: re.Pattern[str] = (
    re.compile(
        r"^(" + "|".join(re.escape(t) for t in sorted(_OUTPUT_PATH_TOKENS)) + r")\s*=\s*(.+)$",
        re.MULTILINE,
    )
    if _OUTPUT_PATH_TOKENS
    else re.compile(r"(?!)")  # never-matches sentinel when token set is empty
)


def _extract_output_paths(assistant_messages: list[str]) -> dict[str, str]:
    """Extract structured output path tokens from session output."""
    paths: dict[str, str] = {}
    for msg in assistant_messages:
        for m in _OUTPUT_PATH_PATTERN.finditer(msg):
            token, value = m.group(1), m.group(2).strip()
            if os.path.isabs(value):
                paths[token] = value
    return paths


def _validate_output_paths(
    extracted_paths: dict[str, str],
    cwd: str,
) -> str | None:
    """Return a diagnostic string if any path is outside cwd, else None."""
    if not os.path.isabs(cwd) or cwd == "/":
        return None
    cwd_prefix = cwd.rstrip("/") + "/"
    violations = []
    for token, path in extracted_paths.items():
        if not path.startswith(cwd_prefix) and path != cwd.rstrip("/"):
            violations.append(f"{token} '{path}' is outside session cwd '{cwd}'")
    return "; ".join(violations) if violations else None


def _apply_budget_guard(
    sr: SkillResult,
    skill_command: str,
    audit: AuditLog | None,
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


def _resolve_skill_session_id(
    session: ClaudeSessionResult | None,
    result: SubprocessResult,
) -> str:
    """Return the best-available Claude session UUID.

    Precedence: stdout-parsed session_id (Channel A) > transport-resolved
    session_id (process.py) > Channel B JSONL filename stem.
    Returns "" only when all sources are empty.
    """
    if session is not None and session.session_id:
        return session.session_id
    return result.session_id or result.channel_b_session_id


def _build_skill_result(
    result: SubprocessResult,
    completion_marker: str = "",
    skill_command: str = "",
    audit: AuditLog | None = None,
    max_consecutive_retries: int = 3,
    expected_output_patterns: Sequence[str] = (),
    cwd: str = "",
    write_behavior: WriteBehaviorSpec | None = None,
) -> SkillResult:
    """Route SubprocessResult fields into the standard run_skill response."""
    branch = (
        "idle_stall"
        if result.termination == TerminationReason.IDLE_STALL
        else "stale"
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
            stale_session.subtype == CliSubtype.SUCCESS
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
                    session_id=stale_session.session_id or _resolve_skill_session_id(None, result),
                    subtype="recovered_from_stale",
                    is_error=False,
                    exit_code=stale_returncode,
                    needs_retry=False,
                    retry_reason=RetryReason.NONE,
                    stderr=result.stderr if result.stderr else "",
                    token_usage=stale_session.token_usage,
                    last_stop_reason=stale_session.last_stop_reason,
                )
        # No valid result in stdout — fall through to original stale response
        _capture_failure(
            skill_command,
            exit_code=result.returncode if result.returncode is not None else -1,
            subtype="stale",
            needs_retry=True,
            retry_reason=RetryReason.STALE,
            stderr=result.stderr if result.stderr else "",
            audit=audit,
        )
        stale_sr = SkillResult(
            success=False,
            result=(
                "Session went stale (no activity for configured threshold). "
                "Partial progress may have been made. Retry to continue."
            ),
            session_id=_resolve_skill_session_id(None, result),
            subtype="stale",
            is_error=False,
            exit_code=-1,
            needs_retry=True,
            retry_reason=RetryReason.STALE,
            stderr="",
            token_usage=None,
        )
        return _apply_budget_guard(stale_sr, skill_command, audit, max_consecutive_retries)

    if result.termination == TerminationReason.IDLE_STALL:
        _capture_failure(
            skill_command,
            exit_code=result.returncode if result.returncode is not None else -1,
            subtype="idle_stall",
            needs_retry=True,
            retry_reason=RetryReason.STALE,
            stderr=result.stderr if result.stderr else "",
            audit=audit,
        )
        logger.warning(
            "Headless session killed: stdout idle for configured threshold (IDLE_STALL)"
        )
        idle_sr = SkillResult(
            success=False,
            result=(
                "Session killed: stdout idle for configured threshold (no output growth). "
                "Partial progress may have been made. Retry to continue."
            ),
            session_id=_resolve_skill_session_id(None, result),
            subtype="idle_stall",
            is_error=True,
            exit_code=-1,
            needs_retry=True,
            retry_reason=RetryReason.STALE,
            stderr="",
            token_usage=None,
        )
        return _apply_budget_guard(idle_sr, skill_command, audit, max_consecutive_retries)

    if result.termination == TerminationReason.TIMED_OUT:
        returncode = -1
        if result.stdout.strip():
            session = parse_session_result(result.stdout)
            if session.subtype == CliSubtype.SUCCESS:
                session = dataclasses.replace(session, subtype=CliSubtype.TIMEOUT, is_error=True)
        else:
            session = ClaudeSessionResult(
                subtype=CliSubtype.TIMEOUT,
                is_error=True,
                result="",
                session_id=_resolve_skill_session_id(None, result),
                errors=[],
            )
    else:
        returncode = result.returncode if result.returncode is not None else -1
        session = parse_session_result(result.stdout)

    # Moved earlier: needed by synthesis recovery step before _compute_outcome.
    write_call_count = sum(1 for t in session.tool_uses if t.get("name") in {"Write", "Edit"})

    # ── Channel B drain-race recovery ──────────────────────────────────────
    # When Channel B confirmed completion but stdout never received the
    # type=result record (UNPARSEABLE / EMPTY_OUTPUT), the session completed
    # but Claude Code deferred type=result until all background agents finished.
    # If we killed the process tree after Channel B fired, the deferred record
    # was never flushed to stdout.
    #
    # assistant_messages are accumulated from stdout NDJSON records of type
    # "assistant" — these are written BEFORE the deferred type=result. If the
    # completion marker is standalone in assistant_messages with substantive
    # content, reconstruct the result and promote the session so downstream
    # recovery paths and the Channel B bypass in _compute_success operate on
    # valid state.
    match result.channel_confirmation:
        case ChannelConfirmation.CHANNEL_B if (
            session.subtype in _CHANNEL_B_RECOVERABLE_SUBTYPES and completion_marker
        ):
            cb_recovered = _recover_from_separate_marker(session, completion_marker)
            if cb_recovered is not None:
                original_subtype = session.subtype
                session = dataclasses.replace(
                    cb_recovered,
                    subtype=CliSubtype.SUCCESS,
                    is_error=False,
                )
                logger.warning(
                    "channel_b_drain_race_recovery",
                    original_subtype=str(original_subtype),
                    assistant_message_count=len(session.assistant_messages),
                )
        case ChannelConfirmation.DIR_MISSING if (
            session.subtype in _CHANNEL_B_RECOVERABLE_SUBTYPES and completion_marker
        ):
            # Late-bind recovery: the directory may have been created by
            # Claude Code during the run even though it was absent at
            # monitor start.  Attempt the same marker-based recovery as
            # the CHANNEL_B arm.
            cb_recovered = _recover_from_separate_marker(session, completion_marker)
            if cb_recovered is not None:
                original_subtype = session.subtype
                session = dataclasses.replace(
                    cb_recovered,
                    subtype=CliSubtype.SUCCESS,
                    is_error=False,
                )
                logger.warning(
                    "dir_missing_late_bind_recovery",
                    original_subtype=str(original_subtype),
                    assistant_message_count=len(session.assistant_messages),
                )
            else:
                logger.warning(
                    "dir_missing_late_bind_recovery_failed",
                    subtype=str(session.subtype),
                    assistant_message_count=len(session.assistant_messages),
                )
        case (
            ChannelConfirmation.CHANNEL_B
            | ChannelConfirmation.CHANNEL_A
            | ChannelConfirmation.UNMONITORED
            | ChannelConfirmation.DIR_MISSING
        ):
            pass  # no drain-race recovery applicable
        case _ as _unreachable_cc:
            assert_never(_unreachable_cc)

    # Recovery is only valid for sessions that completed normally.
    # For incomplete sessions (UNPARSEABLE, TIMEOUT, etc.), any Write calls were
    # intermediate artifacts, not final deliverables. Recovery or synthesis on these
    # sessions would fabricate success evidence for a session that never finished.
    if session.session_complete:
        # Recovery check: attempt before _compute_outcome so the recovered session
        # is the input for outcome computation rather than the original.
        if completion_marker:
            recovered = _recover_from_separate_marker(session, completion_marker)
            if recovered is not None:
                session = recovered

        # Pattern recovery: when a drain-race occurs on either channel, expected_output_patterns
        # content may only exist in assistant_messages. Attempt recovery so that _compute_success
        # sees the block in session.result.
        if (
            result.channel_confirmation != ChannelConfirmation.UNMONITORED
            and expected_output_patterns
            and not _check_expected_patterns(session.result.strip(), expected_output_patterns)
        ):
            pattern_recovered = _recover_block_from_assistant_messages(
                session, expected_output_patterns
            )
            if pattern_recovered is not None:
                session = pattern_recovered

        # Artifact-aware synthesis: only for UNMONITORED sessions where
        # _recover_block_from_assistant_messages is unavailable. For CHANNEL_A/B
        # sessions, if the pattern was absent from assistant_messages the agent never
        # emitted it — synthesis would fabricate a token the agent did not produce.
        if (
            expected_output_patterns
            and write_call_count >= 1
            and result.channel_confirmation == ChannelConfirmation.UNMONITORED
            and not _check_expected_patterns(session.result.strip(), expected_output_patterns)
        ):
            artifact_recovered = _synthesize_from_write_artifacts(
                session, list(expected_output_patterns), write_call_count
            )
            if artifact_recovered is not None:
                session = artifact_recovered

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

    normalized_subtype = _normalize_subtype(session.subtype, outcome, session, completion_marker)

    # For adjudicated_failure with write evidence, record as retriable in the audit so
    # the consecutive chain is intact for the budget guard inside the CONTRACT_RECOVERY gate.
    # CONTRACT_RECOVERY failures are genuinely retriable (the gate promotes them), so
    # recording needs_retry=True is architecturally correct.
    _audit_needs_retry = needs_retry
    _audit_retry_reason = retry_reason
    if (
        not success
        and not needs_retry
        and normalized_subtype == "adjudicated_failure"
        and write_call_count >= 1
    ):
        _audit_needs_retry = True
        _audit_retry_reason = RetryReason.CONTRACT_RECOVERY

    if not success or needs_retry:
        _capture_failure(
            skill_command,
            exit_code=returncode,
            subtype=normalized_subtype,
            needs_retry=_audit_needs_retry,
            retry_reason=_audit_retry_reason.value,
            stderr=result.stderr if result.stderr else "",
            audit=audit,
        )

    result_text = _truncate(session.agent_result)
    if completion_marker:
        result_text = result_text.replace(completion_marker, "").strip()

    extracted_worktree_path = _extract_worktree_path(session.assistant_messages)

    # Path contamination detection
    path_contamination: str | None = None
    if not cwd:
        logger.debug("path_contamination_check_skipped", reason="cwd not provided")
    else:
        extracted_paths = _extract_output_paths(session.assistant_messages)
        path_contamination = _validate_output_paths(extracted_paths, cwd)
        if path_contamination:
            logger.warning("path_contamination_detected", detail=path_contamination, cwd=cwd)

    write_path_warnings: list[str] = []
    if cwd:
        write_path_warnings = _scan_jsonl_write_paths(result.stdout, cwd)
        if write_path_warnings:
            logger.warning(
                "write_path_warnings_detected",
                count=len(write_path_warnings),
                cwd=cwd,
                warnings=write_path_warnings[:5],
            )

    if path_contamination:
        sr = SkillResult(
            success=False,
            result=result_text,
            session_id=session.session_id or result.session_id,
            subtype="path_contamination",
            is_error=session.is_error,
            exit_code=returncode,
            needs_retry=True,
            retry_reason=RetryReason.PATH_CONTAMINATION,
            stderr=_truncate(result.stderr),
            token_usage=session.token_usage,
            worktree_path=extracted_worktree_path,
            cli_subtype=session.subtype,
            write_path_warnings=write_path_warnings,
            write_call_count=write_call_count,
            last_stop_reason=session.last_stop_reason,
        )
    else:
        sr = SkillResult(
            success=success,
            result=result_text,
            session_id=session.session_id or result.session_id,
            subtype=normalized_subtype,
            is_error=session.is_error,
            exit_code=returncode,
            needs_retry=needs_retry,
            retry_reason=retry_reason,
            stderr=_truncate(result.stderr),
            token_usage=session.token_usage,
            worktree_path=extracted_worktree_path,
            cli_subtype=session.subtype,
            write_path_warnings=write_path_warnings,
            write_call_count=write_call_count,
            kill_reason=result.kill_reason,
            last_stop_reason=session.last_stop_reason,
        )
    sr = _apply_budget_guard(sr, skill_command, audit, max_consecutive_retries)

    # CONTRACT_RECOVERY gate: when the session was classified as adjudicated_failure but
    # write evidence exists (write_call_count >= 1), the model wrote the artifact but
    # omitted the structured output token — an emission omission, not a structural contract
    # failure. Promote to RETRIABLE(CONTRACT_RECOVERY) so the pipeline can recover.
    # The first _apply_budget_guard call skips CONTRACT_VIOLATION cases because
    # needs_retry is False at that point. Re-apply budget_guard after promoting so that
    # budget exhaustion can still cap CONTRACT_RECOVERY retries (diagram: CRG → BG).
    if (
        not sr.success
        and not sr.needs_retry
        and sr.subtype == "adjudicated_failure"
        and write_call_count >= 1
    ):
        sr = dataclasses.replace(
            sr,
            needs_retry=True,
            retry_reason=RetryReason.CONTRACT_RECOVERY,
        )
        sr = _apply_budget_guard(sr, skill_command, audit, max_consecutive_retries)

    # Zero-write gate: demote success to retriable failure when a write-expected
    # skill produced zero Edit/Write calls (silent degradation detection).
    # Write expectation is resolved from skill_contracts.yaml via WriteBehaviorSpec.
    if sr.success and sr.write_call_count == 0 and write_behavior is not None:
        write_expected = False
        if write_behavior.mode == "always":
            write_expected = True
        elif write_behavior.mode == "conditional" and write_behavior.expected_when:
            write_expected = _check_expected_patterns(
                sr.result,
                write_behavior.expected_when,
            )
        if write_expected:
            sr = dataclasses.replace(
                sr,
                success=False,
                subtype="zero_writes",
                needs_retry=True,
                retry_reason=RetryReason.ZERO_WRITES,
            )

    logger.debug(
        "build_skill_result_exit",
        success=sr.success,
        subtype=sr.subtype,
        needs_retry=sr.needs_retry,
        retry_reason=str(sr.retry_reason),
        is_error=sr.is_error,
        result_len=len(sr.result),
        write_call_count=sr.write_call_count,
    )
    return sr


def _derive_step_name_from_skill_command(skill_command: str) -> str:
    """Extract a recording step name from a skill command string.

    Examples:
        "/autoskillit:smoke-task arg1" -> "smoke-task"
        "/investigate foo"             -> "investigate"
        "/autoskillit:make-plan"       -> "make-plan"
        ""                             -> ""
    """
    stripped = skill_command.strip()
    if not stripped:
        return ""
    token = stripped.split()[0].lstrip("/")
    if ":" in token:
        token = token.rsplit(":", 1)[-1]
    return token


async def run_headless_core(
    skill_command: str,
    cwd: str,
    ctx: ToolContext,
    *,
    model: str = "",
    step_name: str = "",
    kitchen_id: str = "",
    order_id: str = "",
    add_dirs: Sequence[ValidatedAddDir] = (),
    timeout: float | None = None,
    stale_threshold: float | None = None,
    idle_output_timeout: float | None = None,
    expected_output_patterns: Sequence[str] = (),
    write_behavior: WriteBehaviorSpec | None = None,
    completion_marker: str = "",
    recipe_name: str = "",
    recipe_content_hash: str = "",
    recipe_composite_hash: str = "",
    recipe_version: str | None = None,
) -> SkillResult:
    """Shared headless runner used by run_skill.

    Does NOT check open_kitchen gate — callers in server.py are responsible.
    Accepts explicit ToolContext so this module has no server.py dependency.
    """
    cfg = ctx.config.run_skill
    effective_marker = completion_marker or cfg.completion_marker
    original_skill_command = skill_command

    if not step_name and isinstance(ctx.runner, RecordingSubprocessRunner):
        step_name = _derive_step_name_from_skill_command(skill_command)

    with structlog.contextvars.bound_contextvars(
        skill_command=original_skill_command[:100],
        step_name=step_name or None,
    ):
        effective_plugin_dir = ctx.plugin_dir
        resolved_model = _resolve_model(model, ctx.config)
        spec = build_full_headless_cmd(
            skill_command,
            cwd=cwd,
            completion_marker=effective_marker,
            model=resolved_model,
            plugin_dir=effective_plugin_dir,
            output_format_value=cfg.output_format.value,
            output_format_required_flags=cfg.output_format.required_cli_flags,
            add_dirs=add_dirs,
            exit_after_stop_delay_ms=cfg.exit_after_stop_delay_ms,
            scenario_step_name=step_name,
            temp_dir_relpath=temp_dir_display_str(ctx.config.workspace.temp_dir),
        )

        effective_timeout = timeout if timeout is not None else cfg.timeout
        effective_stale = stale_threshold if stale_threshold is not None else cfg.stale_threshold
        _raw_idle = (
            idle_output_timeout
            if idle_output_timeout is not None
            else float(cfg.idle_output_timeout)
        )
        effective_idle: float | None = _raw_idle if _raw_idle > 0.0 else None

        logger.debug(
            "run_headless_core_entry",
            cwd=cwd,
            resolved_model=resolved_model,
            timeout=effective_timeout,
            stale_threshold=effective_stale,
            plugin_dir=str(effective_plugin_dir),
            add_dirs=list(add_dirs) if add_dirs else None,
        )

        runner = ctx.runner
        assert runner is not None, "No subprocess runner configured"

        linux_tracing_cfg = ctx.config.linux_tracing
        _start_ts = datetime.now(UTC).isoformat()
        _start_mono = time.monotonic()
        _versions = collect_version_snapshot()

        _clone_snapshot = None
        if is_worktree_skill(original_skill_command) and not is_git_worktree(Path(cwd)):
            _clone_snapshot = await snapshot_clone_state(cwd, runner)

        _result: SubprocessResult | None = None
        try:
            _result = await runner(
                spec.cmd,
                cwd=Path(cwd),
                timeout=effective_timeout,
                env=spec.env,
                pty_mode=True,
                session_log_dir=_session_log_dir(cwd),
                completion_marker=effective_marker,
                stale_threshold=effective_stale,
                completion_drain_timeout=cfg.completion_drain_timeout,
                linux_tracing_config=linux_tracing_cfg,
                idle_output_timeout=effective_idle,
                max_suppression_seconds=cfg.max_suppression_seconds,
            )
        except Exception as exc:
            logger.error("run_headless_core runner crashed", exc_info=True)
            _exc_text = traceback.format_exc()
            _log_dir = ctx.config.linux_tracing.log_dir
            try:
                # Deferred: autoskillit.execution.__init__ imports headless.py (L39-42);
                # a top-level import of autoskillit.execution would be circular.
                from autoskillit.execution import flush_session_log

                flush_session_log(
                    log_dir=_log_dir,
                    cwd=str(cwd),
                    kitchen_id=kitchen_id,
                    order_id=order_id,
                    session_id="",
                    pid=0,
                    skill_command=original_skill_command,
                    success=False,
                    subtype="crashed",
                    exit_code=-1,
                    start_ts=_start_ts,
                    proc_snapshots=None,
                    termination_reason="CRASHED",
                    exception_text=_exc_text,
                    versions=_versions,
                    recipe_name=recipe_name,
                    recipe_content_hash=recipe_content_hash,
                    recipe_composite_hash=recipe_composite_hash,
                    recipe_version=recipe_version,
                )
            except Exception:
                logger.debug("flush_session_log during crash failed", exc_info=True)
            return SkillResult.crashed(
                exception=exc,
                skill_command=original_skill_command,
                order_id=order_id,
            )
        except BaseException:
            logger.warning("run_headless_core cancelled", exc_info=True)
            _exc_text = traceback.format_exc()
            _log_dir = ctx.config.linux_tracing.log_dir
            try:
                from autoskillit.execution import flush_session_log

                with anyio.CancelScope(shield=True):
                    flush_session_log(
                        log_dir=_log_dir,
                        cwd=str(cwd),
                        kitchen_id=kitchen_id,
                        order_id=order_id,
                        session_id="",
                        pid=0,
                        skill_command=original_skill_command,
                        success=False,
                        subtype="cancelled",
                        exit_code=-1,
                        start_ts=_start_ts,
                        proc_snapshots=None,
                        termination_reason="CANCELLED",
                        exception_text=_exc_text,
                        versions=_versions,
                        recipe_name=recipe_name,
                        recipe_content_hash=recipe_content_hash,
                        recipe_composite_hash=recipe_composite_hash,
                        recipe_version=recipe_version,
                    )
            except Exception:
                logger.debug("flush_session_log during cancel failed", exc_info=True)
            raise
        if _result is None:
            return SkillResult.crashed(
                exception=RuntimeError("runner() did not return a result"),
                order_id=order_id,
            )
        _elapsed = time.monotonic() - _start_mono
        _end_ts = (datetime.fromisoformat(_start_ts) + timedelta(seconds=_elapsed)).isoformat()
        result = dataclasses.replace(  # type: ignore[arg-type]
            _result, start_ts=_start_ts, end_ts=_end_ts, elapsed_seconds=_elapsed
        )

        audit_count_before = len(ctx.audit.get_report())
        skill_result = _build_skill_result(
            result,
            completion_marker=effective_marker,
            skill_command=original_skill_command,
            audit=ctx.audit,
            expected_output_patterns=expected_output_patterns,
            cwd=cwd,
            write_behavior=write_behavior,
        )

        # CONTRACT NUDGE: lightweight resume recovery before full retry.
        # Fires only when _build_skill_result returns CONTRACT_RECOVERY with a
        # valid session_id (budget-exhausted cases have retry_reason=BUDGET_EXHAUSTED).
        if (
            skill_result.retry_reason == RetryReason.CONTRACT_RECOVERY
            and skill_result.needs_retry
            and skill_result.session_id
        ):
            nudge_success = await _attempt_contract_nudge(
                skill_result,
                result,
                expected_output_patterns,
                effective_marker,
                cwd,
                runner,
            )
            if nudge_success is not None:
                skill_result = nudge_success

        _clone_reverted = False
        if _clone_snapshot is not None:
            skill_result, _clone_reverted = await check_and_revert_clone_contamination(
                _clone_snapshot,
                skill_result,
                cwd,
                runner,
                ctx.audit,
                skill_command=original_skill_command,
            )

        # Use monotonic elapsed_seconds — authoritative wall-clock timing set by time.monotonic()
        # brackets in run_managed_async. Never re-derive from ISO strings (backward-clock risk).
        timing_seconds: float = result.elapsed_seconds

        # Extract the audit record (if any) added by this session
        new_audit_records = ctx.audit.get_report_as_dicts()[audit_count_before:]
        audit_record = new_audit_records[0] if new_audit_records else None

        if result.proc_snapshots is not None or not skill_result.success or bool(step_name):
            from autoskillit.execution.session_log import flush_session_log

            try:
                flush_session_log(
                    log_dir=ctx.config.linux_tracing.log_dir,
                    cwd=cwd,
                    kitchen_id=kitchen_id,
                    order_id=order_id,
                    session_id=skill_result.session_id,
                    pid=result.pid,
                    skill_command=original_skill_command,
                    success=skill_result.success,
                    subtype=skill_result.subtype,
                    cli_subtype=skill_result.cli_subtype,
                    exit_code=skill_result.exit_code,
                    start_ts=result.start_ts,
                    end_ts=result.end_ts,
                    elapsed_seconds=result.elapsed_seconds,
                    termination_reason=result.termination.value,
                    kill_reason=skill_result.kill_reason.value,
                    snapshot_interval_seconds=ctx.config.linux_tracing.proc_interval,
                    proc_snapshots=result.proc_snapshots,
                    step_name=step_name,
                    token_usage=skill_result.token_usage,
                    timing_seconds=timing_seconds,
                    audit_record=audit_record,
                    write_path_warnings=skill_result.write_path_warnings,
                    write_call_count=skill_result.write_call_count,
                    clone_contamination_reverted=_clone_reverted,
                    tracked_comm=result.tracked_comm,
                    orphaned_tool_result=result.orphaned_tool_result,
                    raw_stdout=result.stdout
                    if (
                        not skill_result.success
                        or skill_result.kill_reason != KillReason.NATURAL_EXIT
                    )
                    else "",
                    last_stop_reason=skill_result.last_stop_reason,
                    versions=_versions,
                    recipe_name=recipe_name,
                    recipe_content_hash=recipe_content_hash,
                    recipe_composite_hash=recipe_composite_hash,
                    recipe_version=recipe_version,
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
            try:
                ctx.token_log.record(
                    step_name,
                    skill_result.token_usage,
                    start_ts=result.start_ts,
                    end_ts=result.end_ts,
                    elapsed_seconds=result.elapsed_seconds,
                    order_id=order_id,
                )
            except Exception:
                logger.debug("token_log_record_failed", exc_info=True)
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
        kitchen_id: str = "",
        order_id: str = "",
        add_dirs: Sequence[ValidatedAddDir] = (),
        timeout: float | None = None,
        stale_threshold: float | None = None,
        idle_output_timeout: float | None = None,
        expected_output_patterns: Sequence[str] = (),
        write_behavior: WriteBehaviorSpec | None = None,
        completion_marker: str = "",
        recipe_name: str = "",
        recipe_content_hash: str = "",
        recipe_composite_hash: str = "",
        recipe_version: str | None = None,
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
            kitchen_id=kitchen_id,
            order_id=order_id,
            add_dirs=add_dirs,
            timeout=effective_timeout,
            stale_threshold=effective_stale,
            idle_output_timeout=idle_output_timeout,
            expected_output_patterns=expected_output_patterns,
            write_behavior=write_behavior,
            completion_marker=completion_marker,
            recipe_name=recipe_name,
            recipe_content_hash=recipe_content_hash,
            recipe_composite_hash=recipe_composite_hash,
            recipe_version=recipe_version,
        )
