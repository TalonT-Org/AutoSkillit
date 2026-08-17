"""SkillResult construction and adjudication for headless Claude sessions."""

from __future__ import annotations

import dataclasses
import errno
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    ApiRetryOutcome,
    ChannelConfirmation,
    CliSubtype,
    ClosureAuthoritySpec,
    InfraExitCategory,
    InfraOutcome,
    KillReason,
    NdjsonDriftOutcome,
    ProviderOutcome,
    RetryReason,
    SessionOutcome,
    SkillResult,
    TerminationReason,
    WriteBehaviorSpec,
    WriteEvidence,
    extract_skill_name,
    get_logger,
    validate_worktree_path,
)
from autoskillit.execution.headless._headless_evidence import (
    _adapt_agent_result,
    _apply_budget_guard,
    _capture_failure,
    _compute_write_evidence,
    _extract_file_changes,
    _stdout_mentions_write_tools,
)
from autoskillit.execution.headless._headless_outcome import (
    evaluate_outcome_invariants,
    parse_outcome_fields,
)
from autoskillit.execution.headless._headless_path_tokens import (
    _extract_branch_name,
    _extract_output_paths,
    _extract_worktree_path,
    _is_path_outside_cwd,
    _normalize_messages,
    _select_output_path_tokens,
    _validate_output_paths,
)
from autoskillit.execution.headless._headless_recovery import (
    _infer_enum_token_from_write_contract,
    _recover_block_from_assistant_messages,
    _recover_from_separate_marker,
    _scan_jsonl_write_paths,
    _synthesize_from_write_artifacts,
)
from autoskillit.execution.process import (
    fold_lifecycle_evidence,
    fold_lifecycle_evidence_path,
)
from autoskillit.execution.session._exit_classification import (
    classify_infra_exit,
    has_rate_limit_signal,
)
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
    from autoskillit.core import AuditLog, CodingAgentBackend, SubprocessResult
    from autoskillit.recipe._contracts_types import SkillContract

logger = get_logger(__name__)

_EVIDENCE_RECOVERABLE_SUBTYPES: frozenset[str] = frozenset({"adjudicated_failure", "unparseable"})

__all__ = [
    "_build_skill_result",
    "_make_terminated_result",
    "_parse_stdout",
    "_resolve_skill_session_id",
    "_build_api_retry_outcome",
]


def _resolve_skill_session_id(
    session: ClaudeSessionResult | None,
    result: SubprocessResult,
) -> str:
    """Return the best-available Claude session UUID."""
    if session is not None and session.session_id:
        return session.session_id
    return result.session_id or result.channel_b_session_id


def _parse_stdout(stdout: str, backend: CodingAgentBackend) -> ClaudeSessionResult:
    if backend.name == AGENT_BACKEND_CLAUDE_CODE:
        return parse_session_result(stdout)
    agent_result = backend.result_parser().parse_stdout(stdout)
    return _adapt_agent_result(agent_result)


def _build_api_retry_outcome(session: ClaudeSessionResult) -> ApiRetryOutcome:
    return ApiRetryOutcome(
        count=session.api_retry_count,
        last_error=session.api_retry_last_error,
        last_status=session.api_retry_last_status,
        exhausted=session.api_retry_exhausted,
    )


def _should_flag_cleanup_incomplete(result: SubprocessResult, *, subtype: str) -> bool:
    """Single canonical home for the cleanup-evidence contract:

    Set ``cleanup_incomplete=True`` on InfraOutcome when an owned-process-group
    teardown produced incomplete evidence (a survivor or access-denied PID)
    even though the workload's own outcome was determined independently. This
    is diagnostic only — does not affect needs_retry. ``SubprocessResult.cleanup_evidence``
    and ``InfraOutcome.cleanup_incomplete`` both forward here so the contract
    is documented exactly once.
    """
    evidence = result.cleanup_evidence
    if evidence is None or evidence.complete:
        return False
    logger.warning(
        "headless_cleanup_evidence_incomplete", subtype=subtype, evidence=evidence.to_dict()
    )
    return True


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
    if _should_flag_cleanup_incomplete(result, subtype=subtype):
        infra = dataclasses.replace(infra, cleanup_incomplete=True)
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
        ndjson_drift=NdjsonDriftOutcome(
            unknown_event_count=session.seen_ndjson_unknown_event_count,
            unknown_item_count=session.seen_ndjson_unknown_item_count,
        ),
    )


def _has_out_of_cwd_file_change(file_changes: Sequence[str], cwd: str) -> bool:
    """Return True iff any raw Codex FILE_CHANGE path lexically resolves outside cwd.

    Empty/invalid entries are ignored. If cwd is missing, relative, or ``/``,
    no boundary proof is produced — matching the validator's safety contract.
    """
    for path in file_changes:
        if not isinstance(path, str) or not path:
            continue
        if _is_path_outside_cwd(path, cwd, allow_relative=True):
            return True
    return False


def _apply_post_session_adjudication(
    sr: SkillResult,
    evidence: WriteEvidence,
    write_behavior: WriteBehaviorSpec | None,
    skill_contract: SkillContract | None,
    cwd: str,
) -> SkillResult:
    """Apply write, invariant, and declared-artifact contract checks.

    Invoked as the last adjudication step before each success-finalizing
    return. Makes "a success path that skips adjudication" unrepresentable.
    """
    fields = parse_outcome_fields(sr.result, skill_contract) if skill_contract else {}
    if fields:
        sr = dataclasses.replace(sr, outcome_fields=fields)

    if not sr.success:
        return sr

    if not evidence.has_implementation_evidence and write_behavior is not None:
        write_expected = False
        if write_behavior.mode == "always":
            write_expected = True
        elif write_behavior.mode == "conditional" and write_behavior.expected_when:
            write_expected = _check_expected_patterns(
                sr.result,
                write_behavior.expected_when,
            )
        if write_expected:
            return dataclasses.replace(
                sr,
                success=False,
                subtype="zero_writes",
                needs_retry=True,
                retry_reason=RetryReason.ZERO_WRITES,
            )

    if skill_contract is not None and skill_contract.outcome_invariants:
        violated, detail = evaluate_outcome_invariants(fields, skill_contract.outcome_invariants)
        if violated:
            logger.warning("outcome_invariant_violated", detail=detail)
            return dataclasses.replace(
                sr,
                success=False,
                subtype="outcome_invariant_violation",
                needs_retry=True,
                retry_reason=RetryReason.OUTCOME_INVARIANT,
                outcome_fields=None,
            )

    if skill_contract is not None:
        for output in skill_contract.outputs:
            value = fields.get(output.name)
            if output.type != "file_path" or value is None:
                continue
            failure = _validate_declared_artifact(cwd, output.name, cast(str, value))
            if failure is not None:
                subtype, detail = failure
                retry_reason = (
                    RetryReason.CONTRACT_RECOVERY
                    if subtype == "artifact_contract_violation"
                    else RetryReason.RESUME
                )
                return dataclasses.replace(
                    sr,
                    success=False,
                    is_error=True,
                    subtype=subtype,
                    needs_retry=True,
                    retry_reason=retry_reason,
                    result=detail,
                    outcome_fields=None,
                )

    return sr


def _validate_declared_artifact(cwd: str, field_name: str, value: str) -> tuple[str, str] | None:
    """Validate one emitted ``file_path`` without exposing unsafe paths."""
    safe_name = "."
    producer_detail = (
        f"Skill output '{field_name}' did not identify a contained regular file: {safe_name}"
    )
    infrastructure_detail = (
        f"Could not validate skill output '{field_name}' because filesystem access failed."
    )
    try:
        root = Path(cwd).resolve()
        candidate = Path(value)
        safe_name = candidate.name or "."
        producer_detail = (
            f"Skill output '{field_name}' did not identify a contained regular file: {safe_name}"
        )
        target = (
            (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        )
        try:
            target.relative_to(root)
        except ValueError:
            return "artifact_contract_violation", producer_detail
        target_stat = target.stat()
        if not stat.S_ISREG(target_stat.st_mode):
            return "artifact_contract_violation", producer_detail
    except (TypeError, ValueError, RuntimeError):
        return "artifact_contract_violation", producer_detail
    except OSError as exc:
        if exc.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
            return "artifact_contract_violation", producer_detail
        logger.warning(
            "artifact_adjudication_error",
            field_name=field_name,
            artifact_name=safe_name,
            exc_info=True,
        )
        return "artifact_adjudication_error", infrastructure_detail
    return None


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
    completion_required: bool = False,
    write_watch_dirs: Sequence[Path] = (),
    *,
    provider_used: str = "",
    supports_claude_format_stdout: bool = True,
    backend: CodingAgentBackend,
    readonly_skill: bool = False,
    closure_spec: ClosureAuthoritySpec | None = None,
    closure_report_root: Path | None = None,
    skill_contract: SkillContract | None = None,
) -> SkillResult:
    """Route SubprocessResult fields into the standard run_skill response."""
    file_changes = _extract_file_changes(result.stdout, backend)

    lifecycle_gate_enabled = result.lifecycle_observation_enabled
    obligation_pending = result.pending_task_ids
    obligation_wakeup = result.schedule_wakeup_violation
    observation_complete = result.lifecycle_observation_complete
    if lifecycle_gate_enabled and not observation_complete:
        parser = backend.stream_parser(completion_marker=completion_marker)
        defensive_evidence = (
            fold_lifecycle_evidence(result.stdout.splitlines(), parser)
            if result.stdout
            else (
                fold_lifecycle_evidence_path(result.stdout_path, parser)
                if result.stdout_path is not None
                else None
            )
        )
        if defensive_evidence is not None:
            defensive_pending, defensive_wakeup = defensive_evidence
            observation_complete = True
            obligation_pending = tuple(sorted(set(obligation_pending) | set(defensive_pending)))
            obligation_wakeup = obligation_wakeup or defensive_wakeup

    obligation_failure = lifecycle_gate_enabled and (
        not observation_complete
        or bool(obligation_pending)
        or obligation_wakeup
        or result.completion_ceiling_expired
    )
    provenance_failure = result.termination in {
        TerminationReason.STALE,
        TerminationReason.TIMED_OUT,
        TerminationReason.IDLE_STALL,
        TerminationReason.HEALTH_INSPECTOR,
        TerminationReason.SIGNAL_DEATH,
    }
    if obligation_failure and not provenance_failure:
        obligation_session = _parse_stdout(result.stdout, backend=backend)
        obligation_evidence = _compute_write_evidence(
            obligation_session,
            fs_writes_detected,
            git_writes_detected,
            backend,
            file_changes=file_changes,
            write_watch_dirs=write_watch_dirs,
            cwd=cwd,
            skill_command=skill_command,
        )
        diagnostics = [f"pending={','.join(obligation_pending)}"] if obligation_pending else []
        if obligation_wakeup:
            diagnostics.append("schedule_wakeup=true")
        if result.completion_ceiling_expired:
            diagnostics.append("completion_ceiling_expired=true")
        if not observation_complete:
            diagnostics.append("lifecycle_observation=unavailable")
        return _make_terminated_result(
            result=result,
            session=obligation_session,
            success=False,
            result_text="Unresolved async obligation: " + "; ".join(diagnostics),
            subtype="async_obligation",
            needs_retry=True,
            retry_reason=RetryReason.ASYNC_OBLIGATION,
            evidence=obligation_evidence,
            provider_used=provider_used,
        )

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
            stale_session,
            fs_writes_detected,
            git_writes_detected,
            backend,
            file_changes=file_changes,
            write_watch_dirs=write_watch_dirs,
            cwd=cwd,
            skill_command=skill_command,
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
                expected_output_patterns=expected_output_patterns,
                completion_required=completion_required,
            )
            if success:
                logger.warning(
                    "Session went stale but stdout contained a valid result; recovering"
                )
                _stale_success_sr = _make_terminated_result(
                    result=result,
                    session=stale_session,
                    success=True,
                    result_text=stale_session.agent_result,
                    subtype="recovered_from_stale",
                    needs_retry=False,
                    retry_reason=RetryReason.NONE,
                    evidence=stale_evidence,
                    provider_used=provider_used,
                    api_retry=stale_api_retry,
                )
                _stale_success_sr = _apply_post_session_adjudication(
                    _stale_success_sr, stale_evidence, write_behavior, skill_contract, cwd
                )
                return _stale_success_sr
        # No valid result in stdout — fall through to original stale response
        _stale_is_rate_limited = has_rate_limit_signal(stale_session, result)
        _stale_is_api_error = stale_session.api_retry_exhausted or (
            stale_session.api_error_status is not None and stale_session.api_error_status >= 400
        )
        _stale_retry_reason = (
            RetryReason.RATE_LIMITED if _stale_is_rate_limited else RetryReason.STALE
        )
        _capture_failure(
            skill_command,
            exit_code=result.returncode if result.returncode is not None else -1,
            subtype="stale",
            needs_retry=True,
            retry_reason=_stale_retry_reason,
            stderr=result.stderr if result.stderr else "",
            audit=audit,
        )
        if _stale_is_rate_limited:
            stale_infra = InfraOutcome(exit_category=InfraExitCategory.RATE_LIMITED.value)
        elif _stale_is_api_error:
            stale_infra = InfraOutcome(exit_category=InfraExitCategory.API_ERROR.value)
        else:
            stale_infra = InfraOutcome()
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
            retry_reason=_stale_retry_reason,
            evidence=stale_evidence,
            provider_used=provider_used,
            infra=stale_infra,
            api_retry=stale_api_retry,
        )
        return _apply_budget_guard(stale_sr, skill_command, audit, max_consecutive_retries)

    if result.termination == TerminationReason.IDLE_STALL:
        idle_session = _parse_stdout(result.stdout, backend=backend)
        idle_evidence = _compute_write_evidence(
            idle_session,
            fs_writes_detected,
            git_writes_detected,
            backend,
            file_changes=file_changes,
            write_watch_dirs=write_watch_dirs,
            cwd=cwd,
            skill_command=skill_command,
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
                expected_output_patterns=expected_output_patterns,
                completion_required=completion_required,
            )
            if success:
                logger.warning(
                    "Session idle-stalled but stdout contained a valid result; recovering"
                )
                _idle_success_sr = _make_terminated_result(
                    result=result,
                    session=idle_session,
                    success=True,
                    result_text=idle_session.agent_result,
                    subtype="recovered_from_idle_stall",
                    needs_retry=False,
                    retry_reason=RetryReason.NONE,
                    evidence=idle_evidence,
                    provider_used=provider_used,
                    api_retry=idle_api_retry,
                )
                _idle_success_sr = _apply_post_session_adjudication(
                    _idle_success_sr, idle_evidence, write_behavior, skill_contract, cwd
                )
                return _idle_success_sr
        _idle_is_rate_limited = has_rate_limit_signal(idle_session, result)
        _idle_is_api_error = idle_session.api_retry_exhausted or (
            idle_session.api_error_status is not None and idle_session.api_error_status >= 400
        )
        _idle_retry_reason = (
            RetryReason.RATE_LIMITED if _idle_is_rate_limited else RetryReason.IDLE_STALL
        )
        _capture_failure(
            skill_command,
            exit_code=result.returncode if result.returncode is not None else -1,
            subtype="idle_stall",
            needs_retry=True,
            retry_reason=_idle_retry_reason,
            stderr=result.stderr if result.stderr else "",
            audit=audit,
        )
        logger.warning(
            "Headless session killed: stdout idle for configured threshold (IDLE_STALL)"
        )
        if _idle_is_rate_limited:
            idle_infra = InfraOutcome(exit_category=InfraExitCategory.RATE_LIMITED.value)
        elif _idle_is_api_error:
            idle_infra = InfraOutcome(exit_category=InfraExitCategory.API_ERROR.value)
        else:
            idle_infra = InfraOutcome()
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
            retry_reason=_idle_retry_reason,
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

    evidence = _compute_write_evidence(
        session,
        fs_writes_detected,
        git_writes_detected,
        backend,
        file_changes=file_changes,
        write_watch_dirs=write_watch_dirs,
        cwd=cwd,
        skill_command=skill_command,
    )
    _has_write_evidence = evidence.has_evidence

    try:
        _backend_write_names = set(backend.write_tool_names())
    except Exception:
        logger.warning("backend_write_tool_names_failed", exc_info=True)
        _backend_write_names = {"Write", "Edit"}
    _parsed_has_write_tools = any(
        tu.get("name") in _backend_write_names for tu in session.tool_uses
    )
    if (
        evidence.write_call_count == 0
        and _backend_write_names & {"Write", "Edit"}
        and _stdout_mentions_write_tools(result.stdout)
        and not _parsed_has_write_tools
    ):
        logger.warning(
            "write_call_count_cross_check_mismatch",
            stdout_length=len(result.stdout),
            tool_use_count=len(session.tool_uses),
        )
        evidence = dataclasses.replace(evidence, write_call_count=1)
        _has_write_evidence = True

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
        write_names = backend.write_tool_names()
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
                file_changes=file_changes,
            )
            if artifact_recovered is not None:
                session = artifact_recovered

        # Enum inference: contract-aware recovery for enum-typed outputs whose value is
        # mechanically implied by observed write evidence (see _parse_single_enum_binding).
        # Runs for ALL channel confirmations — unlike artifact-aware synthesis above, this
        # derives the token from evidence the agent DID observably produce (emitted
        # companion path-token line + file on disk), not fabricated evidence, so it is not
        # gated to UNMONITORED sessions.
        if (
            expected_output_patterns
            and _has_write_evidence
            and not _check_expected_patterns(session.result.strip(), expected_output_patterns)
        ):
            enum_inferred = _infer_enum_token_from_write_contract(
                session,
                list(expected_output_patterns),
                skill_contract,
                evidence.write_call_count,
                file_changes=file_changes,
            )
            if enum_inferred is not None:
                session = enum_inferred

    exit_code_is_terminal = backend.capabilities.exit_code_is_terminal
    outcome, retry_reason = _compute_outcome(
        session,
        returncode,
        result.termination,
        completion_marker,
        channel_confirmation=result.channel_confirmation,
        expected_output_patterns=expected_output_patterns,
        prior_completion_markers=prior_completion_markers,
        exit_code_is_terminal=exit_code_is_terminal,
        completion_required=completion_required,
    )
    success = outcome == SessionOutcome.SUCCEEDED
    needs_retry = outcome == SessionOutcome.RETRIABLE

    infra_category = classify_infra_exit(session, result, capabilities=backend.capabilities)
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

    # Rate-limit override: HTTP 429 is a transient rate limit, not structural context
    # exhaustion. Produce RATE_LIMITED so the orchestrator can route to on_rate_limit
    # instead of on_context_limit, enabling wait-and-retry rather than escalation.
    if not success and infra_category == InfraExitCategory.RATE_LIMITED:
        logger.info(
            "rate_limit_override",
            original_retry_reason=retry_reason.value,
            promoted_to="rate_limited",
        )
        retry_reason = RetryReason.RATE_LIMITED
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
        and not completion_required
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

    # For evidence-recoverable subtypes (adjudicated_failure, unparseable) + write evidence:
    # record as retriable so the consecutive chain is intact for the CONTRACT_RECOVERY
    # budget guard (genuinely retriable).
    _audit_needs_retry = needs_retry
    _audit_retry_reason = retry_reason
    if (
        not success
        and not needs_retry
        and normalized_subtype in _EVIDENCE_RECOVERABLE_SUBTYPES
        and _has_write_evidence
        and not readonly_skill
        and (normalized_subtype == "adjudicated_failure" or returncode == 0)
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

    result_text = session.agent_result
    if completion_marker:
        result_text = result_text.replace(completion_marker, "").strip()

    normalized_msgs = _normalize_messages(session.assistant_messages)
    extracted_worktree_path = _extract_worktree_path(normalized_msgs)
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
    extracted_branch_name = _extract_branch_name(normalized_msgs)

    # Path contamination detection (two-factor contract — see plan #4150).
    # Factor 1: contract-scoped text candidate (an assistant-text token path outside CWD,
    #           selected from the running skill's own file_path* outputs).
    # Factor 2: boundary-specific write proof (Claude write_path_warnings OR Codex
    #           completed out-of-CWD FILE_CHANGE with implementation evidence).
    # Both factors must hold for terminal classification; text alone is never proof.
    text_path_violation: str | None = None
    write_path_warnings: list[str] = []
    skill_name = extract_skill_name(skill_command)

    if not cwd:
        logger.debug("path_contamination_check_skipped", reason="cwd not provided")
    else:
        selected_tokens = _select_output_path_tokens(skill_name)
        extracted_paths = _extract_output_paths(normalized_msgs, token_scope=selected_tokens)
        text_path_violation = _validate_output_paths(extracted_paths, cwd)
        if text_path_violation:
            logger.debug(
                "text_path_candidate_detected",
                detail=text_path_violation,
                cwd=cwd,
                skill_name=skill_name,
                scope_size=len(selected_tokens),
            )

        if supports_claude_format_stdout:
            _wtn = backend.capabilities.write_guard_tool_names
            if _wtn:
                write_path_warnings = _scan_jsonl_write_paths(
                    result.stdout,
                    cwd,
                    write_tool_names=_wtn,
                )
            else:
                write_path_warnings = _scan_jsonl_write_paths(result.stdout, cwd)
            if write_path_warnings:
                logger.warning(
                    "write_path_warnings_detected",
                    count=len(write_path_warnings),
                    cwd=cwd,
                    warnings=write_path_warnings[:5],
                )

    # Factor 2: boundary-specific write proof (path-bearing, not just counts).
    claude_boundary_proof = bool(write_path_warnings)
    codex_boundary_proof = (
        backend.capabilities.write_detection_strategy == "file_changes"
        and _has_out_of_cwd_file_change(file_changes, cwd)
        and evidence.has_implementation_evidence
    )
    is_path_contamination = bool(text_path_violation) and (
        claude_boundary_proof or codex_boundary_proof
    )

    if text_path_violation and not is_path_contamination:
        # Recurrence analysis signal: text alone is a hint, not a verdict.
        logger.info(
            "text_path_candidate_uncorroborated",
            detail=text_path_violation,
            cwd=cwd,
            skill_name=skill_name,
            claude_boundary_proof=claude_boundary_proof,
            codex_boundary_proof=codex_boundary_proof,
        )

    _cleanup_incomplete = _should_flag_cleanup_incomplete(result, subtype=normalized_subtype)

    sr = SkillResult(
        success=success,
        result=result_text,
        session_id=session.session_id or result.session_id,
        subtype=normalized_subtype,
        is_error=session.is_error,
        exit_code=returncode,
        needs_retry=needs_retry,
        retry_reason=retry_reason,
        stderr=result.stderr,
        token_usage=session.token_usage,
        worktree_path=effective_worktree_path,
        branch_name=extracted_branch_name,
        cli_subtype=session.subtype,
        write_path_warnings=write_path_warnings,
        evidence=evidence,
        kill_reason=result.kill_reason,
        last_stop_reason=session.last_stop_reason,
        lifespan_started=session.lifespan_started,
        provider=ProviderOutcome(provider_used=provider_used, fallback_activated=False),
        infra=InfraOutcome(
            exit_category=infra_category.value, cleanup_incomplete=_cleanup_incomplete
        ),
        api_retry=api_retry,
        ndjson_drift=NdjsonDriftOutcome(
            unknown_event_count=session.seen_ndjson_unknown_event_count,
            unknown_item_count=session.seen_ndjson_unknown_item_count,
        ),
        completion_required=completion_required,
    )
    if is_path_contamination:
        sr = dataclasses.replace(
            sr,
            success=False,
            subtype="path_contamination",
            needs_retry=True,
            retry_reason=RetryReason.PATH_CONTAMINATION,
        )
    sr = _apply_budget_guard(sr, skill_command, audit, max_consecutive_retries)

    # CONTRACT_RECOVERY gate: when the session was classified as a terminal failure
    # (adjudicated_failure or unparseable) but write evidence exists and the process
    # exited cleanly, the model wrote the artifact but the structured output token was
    # missing or the stdout stream was truncated — promote to RETRIABLE(CONTRACT_RECOVERY).
    # Re-apply budget_guard after promoting so budget exhaustion can still cap retries.
    # The first _apply_budget_guard skips this case because needs_retry is False then.
    if (
        not sr.success
        and not sr.needs_retry
        and sr.subtype in _EVIDENCE_RECOVERABLE_SUBTYPES
        and _has_write_evidence
        and not readonly_skill
        and (sr.subtype == "adjudicated_failure" or returncode == 0)
    ):
        sr = dataclasses.replace(
            sr,
            needs_retry=True,
            retry_reason=RetryReason.CONTRACT_RECOVERY,
        )
        sr = _apply_budget_guard(sr, skill_command, audit, max_consecutive_retries)

    sr = _apply_post_session_adjudication(sr, evidence, write_behavior, skill_contract, cwd)

    # Closure verification gate: when a ClosureAuthoritySpec is active, independently
    # verify the canonical closure report. On failure, demote to execution error so
    # the recipe's on_failure route fires. This gate cannot be bypassed by the LLM
    # orchestrator — it is enforced programmatically after session completion.
    if closure_spec is not None and closure_report_root is not None:
        from autoskillit.core import verify_closure_report

        report_file = closure_report_root / "closure_report.json"
        verification = verify_closure_report(
            report_path=report_file,
            authority_path=Path(closure_spec.authority_path),
            authority_hash=closure_spec.authority_hash,
            output_root=closure_report_root,
            plan_paths=tuple(Path(p) for p in closure_spec.plan_paths),
            base_sha=closure_spec.base_sha,
            diff_sha=closure_spec.diff_sha,
            target_sha=closure_spec.target_sha,
        )
        if not verification.success:
            error_detail = "; ".join(verification.errors)
            sr = dataclasses.replace(
                sr,
                success=False,
                is_error=True,
                subtype="closure_verification_failed",
                result=f"Closure verification failed: {error_detail}",
            )
        else:
            if sr.retry_reason == RetryReason.EMPTY_OUTPUT:
                sr = dataclasses.replace(sr, is_error=False)

    if sr.needs_retry and sr.retry_reason == RetryReason.EMPTY_OUTPUT and _has_write_evidence:
        sr = dataclasses.replace(
            sr,
            subtype="completed_no_flush",
            retry_reason=RetryReason.COMPLETED_NO_FLUSH,
        )
        sr = _apply_budget_guard(sr, skill_command, audit, max_consecutive_retries)

    if skill_contract is not None and skill_contract.outputs:
        _parsed_fields = dict(sr.outcome_fields or {})
        _qualifier: str | None = None
        if sr.success and skill_contract.success_qualifiers:
            from autoskillit.execution.headless._headless_outcome import (
                evaluate_success_qualifier,
            )

            _qualifier = evaluate_success_qualifier(
                _parsed_fields, skill_contract.success_qualifiers
            )
        sr = dataclasses.replace(
            sr,
            outcome_invariant_violated=sr.retry_reason == RetryReason.OUTCOME_INVARIANT,
            outcome_qualifier=_qualifier,
        )

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
