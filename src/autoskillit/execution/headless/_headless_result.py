"""SkillResult construction and adjudication for headless Claude sessions."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, assert_never

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    AgentSessionResult,
    ApiRetryOutcome,
    ChannelConfirmation,
    CliSubtype,
    FailureRecord,
    InfraExitCategory,
    InfraOutcome,
    KillReason,
    ProviderOutcome,
    RetryReason,
    SessionOutcome,
    SessionTelemetry,
    SkillResult,
    TerminationReason,
    WriteBehaviorSpec,
    WriteEvidence,
    get_logger,
    truncate_text,
    validate_worktree_path,
)
from autoskillit.execution.headless._headless_path_tokens import (
    _extract_output_paths,
    _extract_worktree_path,
    _validate_output_paths,
)
from autoskillit.execution.headless._headless_recovery import (
    _CHANNEL_B_RECOVERABLE_SUBTYPES,
    _recover_block_from_assistant_messages,
    _recover_from_separate_marker,
    _synthesize_from_write_artifacts,
)
from autoskillit.execution.headless._headless_scan import _scan_jsonl_write_paths
from autoskillit.execution.session._exit_classification import classify_infra_exit
from autoskillit.execution.session._session_content import _check_expected_patterns
from autoskillit.execution.session._session_model import (
    ClaudeSessionResult,
    parse_session_result,
)
from autoskillit.execution.session._session_outcome import (
    _compute_outcome,
    _compute_success,
)

if TYPE_CHECKING:
    from autoskillit.core import AuditLog, CodingAgentBackend, GitHubApiLog, SubprocessResult

logger = get_logger(__name__)
_truncate = truncate_text

__all__ = [
    "_adapt_agent_result",
    "_build_error_path_telemetry",
    "_build_session_telemetry",
    "_capture_failure",
    "_apply_budget_guard",
    "_resolve_skill_session_id",
    "_build_skill_result",
]


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


def _resolve_skill_session_id(
    session: ClaudeSessionResult | None,
    result: SubprocessResult,
) -> str:
    """Return the best-available Claude session UUID."""
    if session is not None and session.session_id:
        return session.session_id
    return result.session_id or result.channel_b_session_id


def _parse_stdout(stdout: str, backend: CodingAgentBackend | None = None) -> ClaudeSessionResult:
    if backend is None or backend.name == AGENT_BACKEND_CLAUDE_CODE:
        return parse_session_result(stdout)
    agent_result = backend.result_parser().parse_stdout(stdout)
    return _adapt_agent_result(agent_result)


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
    backend: CodingAgentBackend | None = None,
) -> WriteEvidence:
    write_names = (
        backend.write_tool_names() if backend is not None else frozenset({"Write", "Edit"})
    )
    write_call_count = sum(1 for t in session.tool_uses if t.get("name") in write_names)
    return WriteEvidence(
        write_call_count=write_call_count,
        fs_writes_detected=fs_writes_detected,
        git_writes_detected=git_writes_detected,
    )


def _stdout_mentions_write_tools(stdout: str) -> bool:
    return '"Edit"' in stdout or '"Write"' in stdout


def _build_api_retry_outcome(session: ClaudeSessionResult) -> ApiRetryOutcome:
    return ApiRetryOutcome(
        count=session.api_retry_count,
        last_error=session.api_retry_last_error,
        last_status=session.api_retry_last_status,
        exhausted=session.api_retry_exhausted,
    )


def _make_terminated_result(
    *,
    result: SubprocessResult,
    session: ClaudeSessionResult,
    success: bool,
    result_text: str,
    subtype: str,
    needs_retry: bool,
    retry_reason: RetryReason,
    evidence: WriteEvidence,
    provider_used: str = "",
    infra: InfraOutcome = InfraOutcome(),
    api_retry: ApiRetryOutcome = ApiRetryOutcome(),
) -> SkillResult:
    """Construct SkillResult for infrastructure-terminated sessions (stale/idle_stall)."""
    return SkillResult(
        success=success,
        result=result_text,
        session_id=session.session_id or _resolve_skill_session_id(session, result),
        subtype=subtype,
        is_error=session.is_error if success else False,
        exit_code=result.returncode if result.returncode is not None else -1,
        needs_retry=needs_retry,
        retry_reason=retry_reason,
        stderr=result.stderr if result.stderr else "",
        token_usage=session.token_usage,
        evidence=evidence,
        kill_reason=result.kill_reason,
        last_stop_reason=session.last_stop_reason,
        lifespan_started=session.lifespan_started,
        provider=ProviderOutcome(provider_used=provider_used, fallback_activated=False),
        infra=infra,
        api_retry=api_retry,
    )


def _build_skill_result(
    result: SubprocessResult,
    completion_marker: str = "",
    skill_command: str = "",
    audit: AuditLog | None = None,
    max_consecutive_retries: int = 3,
    expected_output_patterns: Sequence[str] = (),
    cwd: str = "",
    write_behavior: WriteBehaviorSpec | None = None,
    fs_writes_detected: bool = False,
    git_writes_detected: bool = False,
    prior_completion_markers: Sequence[str] | None = None,
    *,
    provider_used: str = "",
    supports_claude_format_stdout: bool = True,
    backend: CodingAgentBackend | None = None,
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
        stale_session = _parse_stdout(result.stdout, backend=backend)
        stale_evidence = _compute_write_evidence(
            stale_session, fs_writes_detected, git_writes_detected, backend
        )
        stale_api_retry = _build_api_retry_outcome(stale_session)
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
                return _make_terminated_result(
                    result=result,
                    session=stale_session,
                    success=True,
                    result_text=_truncate(stale_session.agent_result),
                    subtype="recovered_from_stale",
                    needs_retry=False,
                    retry_reason=RetryReason.NONE,
                    evidence=stale_evidence,
                    provider_used=provider_used,
                    api_retry=stale_api_retry,
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
        stale_infra = (
            InfraOutcome(exit_category=InfraExitCategory.API_ERROR.value)
            if stale_session.api_retry_exhausted
            else InfraOutcome()
        )
        stale_sr = _make_terminated_result(
            result=result,
            session=stale_session,
            success=False,
            result_text=(
                "Session went stale (no activity for configured threshold). "
                "Partial progress may have been made. Retry to continue."
            ),
            subtype="stale",
            needs_retry=True,
            retry_reason=RetryReason.STALE,
            evidence=stale_evidence,
            provider_used=provider_used,
            infra=stale_infra,
            api_retry=stale_api_retry,
        )
        return _apply_budget_guard(stale_sr, skill_command, audit, max_consecutive_retries)

    if result.termination == TerminationReason.IDLE_STALL:
        idle_session = _parse_stdout(result.stdout, backend=backend)
        idle_evidence = _compute_write_evidence(
            idle_session, fs_writes_detected, git_writes_detected, backend
        )
        idle_api_retry = _build_api_retry_outcome(idle_session)
        idle_returncode = result.returncode if result.returncode is not None else -1
        can_attempt_idle_stall_recovery = (
            idle_session.subtype == CliSubtype.SUCCESS
            and idle_session.result.strip()
            and not idle_session.is_error
        )
        if can_attempt_idle_stall_recovery:
            success = _compute_success(
                idle_session,
                idle_returncode,
                TerminationReason.COMPLETED,
                completion_marker=completion_marker,
                channel_confirmation=result.channel_confirmation,
            )
            if success:
                logger.warning(
                    "Session idle-stalled but stdout contained a valid result; recovering"
                )
                return _make_terminated_result(
                    result=result,
                    session=idle_session,
                    success=True,
                    result_text=_truncate(idle_session.agent_result),
                    subtype="recovered_from_idle_stall",
                    needs_retry=False,
                    retry_reason=RetryReason.NONE,
                    evidence=idle_evidence,
                    provider_used=provider_used,
                    api_retry=idle_api_retry,
                )
        _capture_failure(
            skill_command,
            exit_code=result.returncode if result.returncode is not None else -1,
            subtype="idle_stall",
            needs_retry=True,
            retry_reason=RetryReason.IDLE_STALL,
            stderr=result.stderr if result.stderr else "",
            audit=audit,
        )
        logger.warning(
            "Headless session killed: stdout idle for configured threshold (IDLE_STALL)"
        )
        idle_infra = (
            InfraOutcome(exit_category=InfraExitCategory.API_ERROR.value)
            if idle_session.api_retry_exhausted
            else InfraOutcome()
        )
        idle_sr = _make_terminated_result(
            result=result,
            session=idle_session,
            success=False,
            result_text=(
                "Session killed: stdout idle for configured threshold (no output growth). "
                "Partial progress may have been made. Retry to continue."
            ),
            subtype="idle_stall",
            needs_retry=True,
            retry_reason=RetryReason.IDLE_STALL,
            evidence=idle_evidence,
            provider_used=provider_used,
            infra=idle_infra,
            api_retry=idle_api_retry,
        )
        return _apply_budget_guard(idle_sr, skill_command, audit, max_consecutive_retries)

    if result.termination == TerminationReason.TIMED_OUT:
        returncode = -1
        if result.stdout.strip():
            session = _parse_stdout(result.stdout, backend=backend)
            if session.subtype != CliSubtype.TIMEOUT:
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
        session = _parse_stdout(result.stdout, backend=backend)

    evidence = _compute_write_evidence(session, fs_writes_detected, git_writes_detected, backend)
    _has_write_evidence = evidence.has_evidence

    if evidence.write_call_count == 0 and _stdout_mentions_write_tools(result.stdout):
        logger.warning(
            "write_call_count_cross_check_mismatch",
            stdout_length=len(result.stdout),
            tool_use_count=len(session.tool_uses),
        )

    # Channel B drain-race: recover from assistant_messages if type=result was not flushed.
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
        write_names = (
            backend.write_tool_names() if backend is not None else frozenset({"Write", "Edit"})
        )
        if (
            expected_output_patterns
            and _has_write_evidence
            and result.channel_confirmation == ChannelConfirmation.UNMONITORED
            and not _check_expected_patterns(session.result.strip(), expected_output_patterns)
        ):
            artifact_recovered = _synthesize_from_write_artifacts(
                session,
                list(expected_output_patterns),
                evidence.write_call_count,
                evidence.fs_writes_detected,
                write_tool_names=write_names,
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
        prior_completion_markers=prior_completion_markers,
    )
    success = outcome == SessionOutcome.SUCCEEDED
    needs_retry = outcome == SessionOutcome.RETRIABLE

    infra_category = classify_infra_exit(session, result)
    api_retry = _build_api_retry_outcome(session)

    # API error override: when the session failed due to an API infrastructure error
    # (overload, 529, ECONNRESET), promote to RESUME so the orchestrator routes to
    # on_context_limit instead of on_failure (partial progress may exist).
    if not success and infra_category == InfraExitCategory.API_ERROR:
        logger.info(
            "api_error_override",
            original_retry_reason=retry_reason.value,
            promoted_to="resume",
        )
        retry_reason = RetryReason.RESUME
        needs_retry = True

    # Process kill override: external kills (SIGKILL/OOM, not autoskillit-initiated)
    # route to RESUME so the orchestrator can attempt recovery.
    # TIMED_OUT uses a synthetic returncode=-1 but is a wall-clock timeout (non-recoverable).
    if (
        not success
        and not needs_retry
        and infra_category == InfraExitCategory.PROCESS_KILLED
        and result.kill_reason == KillReason.NATURAL_EXIT
        and result.termination != TerminationReason.TIMED_OUT
    ):
        retry_reason = RetryReason.RESUME
        needs_retry = True
        outcome = SessionOutcome.RETRIABLE

    normalized_subtype = session.normalize_subtype(
        outcome, completion_marker, prior_completion_markers
    )

    if (
        normalized_subtype == "missing_completion_marker"
        and expected_output_patterns
        and infra_category == InfraExitCategory.COMPLETED
        and _check_expected_patterns(session.result.strip(), expected_output_patterns)
    ):
        normalized_subtype = CliSubtype.SUCCESS.value
        outcome = SessionOutcome.SUCCEEDED
        success = True
        needs_retry = False
        retry_reason = RetryReason.NONE

    # Invariant: TIMED_OUT sessions must produce subtype='timeout'.
    if (
        result.termination == TerminationReason.TIMED_OUT
        and normalized_subtype != CliSubtype.TIMEOUT.value
    ):
        expected = CliSubtype.TIMEOUT.value
        raise RuntimeError(
            f"TIMED_OUT session produced subtype={normalized_subtype!r}, expected {expected!r}"
        )

    # For adjudicated_failure + write evidence: record as retriable so the consecutive
    # chain is intact for the CONTRACT_RECOVERY budget guard (genuinely retriable).
    _audit_needs_retry = needs_retry
    _audit_retry_reason = retry_reason
    if (
        not success
        and not needs_retry
        and normalized_subtype == "adjudicated_failure"
        and _has_write_evidence
    ):
        _audit_needs_retry = True
        _audit_retry_reason = RetryReason.CONTRACT_RECOVERY
    if retry_reason == RetryReason.EMPTY_OUTPUT and _has_write_evidence:
        _audit_retry_reason = RetryReason.COMPLETED_NO_FLUSH

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
    validated_wt = (
        validate_worktree_path(extracted_worktree_path) if extracted_worktree_path else None
    )
    if extracted_worktree_path and validated_wt is None:
        logger.warning(
            "worktree_path_validation_failed",
            extracted=extracted_worktree_path,
            reason="path does not exist on disk",
        )
    effective_worktree_path = validated_wt.path if validated_wt else None

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
    if cwd and supports_claude_format_stdout:
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
            worktree_path=effective_worktree_path,
            cli_subtype=session.subtype,
            write_path_warnings=write_path_warnings,
            evidence=evidence,
            kill_reason=result.kill_reason,
            last_stop_reason=session.last_stop_reason,
            lifespan_started=session.lifespan_started,
            provider=ProviderOutcome(provider_used=provider_used, fallback_activated=False),
            infra=InfraOutcome(exit_category=infra_category.value),
            api_retry=api_retry,
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
            worktree_path=effective_worktree_path,
            cli_subtype=session.subtype,
            write_path_warnings=write_path_warnings,
            evidence=evidence,
            kill_reason=result.kill_reason,
            last_stop_reason=session.last_stop_reason,
            lifespan_started=session.lifespan_started,
            provider=ProviderOutcome(provider_used=provider_used, fallback_activated=False),
            infra=InfraOutcome(exit_category=infra_category.value),
            api_retry=api_retry,
        )
    sr = _apply_budget_guard(sr, skill_command, audit, max_consecutive_retries)

    # CONTRACT_RECOVERY gate: when the session was classified as adjudicated_failure but
    # write evidence exists, the model wrote the artifact but omitted the structured output
    # token — promote to RETRIABLE(CONTRACT_RECOVERY). Re-apply budget_guard after
    # promoting so budget exhaustion can still cap CONTRACT_RECOVERY retries.
    # The first _apply_budget_guard skips this case because needs_retry is False then.
    if (
        not sr.success
        and not sr.needs_retry
        and sr.subtype == "adjudicated_failure"
        and _has_write_evidence
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
    if sr.success and not _has_write_evidence and write_behavior is not None:
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

    if sr.needs_retry and sr.retry_reason == RetryReason.EMPTY_OUTPUT and _has_write_evidence:
        sr = dataclasses.replace(
            sr,
            subtype="completed_no_flush",
            retry_reason=RetryReason.COMPLETED_NO_FLUSH,
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
        write_call_count=sr.evidence.write_call_count,
    )
    return sr


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
