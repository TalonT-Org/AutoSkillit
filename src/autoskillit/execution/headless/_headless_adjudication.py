"""Adjudication helpers for headless Claude session SkillResult construction.

Owns the post-session adjudication chain: parse-stdout,
build-api-retry-outcome, make-terminated-result, out-of-cwd-file-change
detection, post-session-adjudication, and declared-artifact validation.
"""

from __future__ import annotations

import dataclasses
import errno
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from autoskillit.core import (
    ApiFailureOutcome,
    ApiRetryOutcome,
    CliSubtype,
    InfraOutcome,
    NdjsonDriftOutcome,
    ProviderOutcome,
    RateLimitWindow,
    RetryReason,
    SkillResult,
    TerminationReason,
    WriteBehaviorSpec,
    WriteEvidence,
    extract_skill_name,
    get_logger,
)
from autoskillit.execution.backends._codex_parse import extract_codex_turn_usage
from autoskillit.execution.headless._headless_evidence import (
    _adapt_agent_result,
    _apply_budget_guard,
    _compute_write_evidence,
)
from autoskillit.execution.headless._headless_outcome import (
    evaluate_outcome_invariants,
    parse_outcome_fields,
)
from autoskillit.execution.headless._headless_path_tokens import (
    NormalizedMessages,
    _extract_output_paths,
    _is_path_outside_cwd,
    _select_output_path_tokens,
    _validate_output_paths,
)
from autoskillit.execution.headless._headless_recovery import _scan_jsonl_write_paths
from autoskillit.execution.session._session_content import _check_expected_patterns
from autoskillit.execution.session._session_model import (
    ClaudeSessionResult,
    parse_session_result,
)
from autoskillit.execution.session._session_outcome import _compute_success

if TYPE_CHECKING:
    from autoskillit.core import (
        AuditLog,
        ClosureAuthoritySpec,
        CodingAgentBackend,
        SubprocessResult,
    )
    from autoskillit.recipe._contracts_types import SkillContract

logger = get_logger(__name__)


def _resolve_skill_session_id(
    session: ClaudeSessionResult | None,
    result: SubprocessResult,
) -> str:
    """Return the best-available Claude session UUID."""
    if session is not None and session.session_id:
        return session.session_id
    return result.session_id or result.channel_b_session_id


def _parse_stdout(result: SubprocessResult, backend: CodingAgentBackend) -> ClaudeSessionResult:
    if backend.capabilities.supports_claude_format_stdout:
        return parse_session_result(result.stdout)
    agent_result = backend.result_parser().parse_stdout(result.stdout)
    rows = extract_codex_turn_usage(
        backend.session_locator(),
        agent_result.session_id or result.session_id or result.channel_b_session_id,
        result.start_ts,
        result.end_ts,
    )
    agent_result = dataclasses.replace(
        agent_result,
        raw={**agent_result.raw, "turn_usage": rows},
    )
    return _adapt_agent_result(agent_result)


def _build_api_retry_outcome(session: ClaudeSessionResult) -> ApiRetryOutcome:
    return ApiRetryOutcome(
        count=session.api_retry_count,
        last_error=session.api_retry_last_error,
        last_status=session.api_retry_last_status,
        exhausted=session.api_retry_exhausted,
    )


def _build_api_failure_outcome(session: ClaudeSessionResult) -> ApiFailureOutcome:
    """Project retained provider-failure evidence onto the public result bundle."""
    return ApiFailureOutcome(
        status=session.api_error_status,
        terminal_reason=session.terminal_reason,
        error_code=session.provider_error_code,
        api_error_message_seen=session.api_error_message_seen,
        rate_limit=RateLimitWindow(
            status=session.rate_limit_status,
            limit_type=session.rate_limit_type,
            resets_at_epoch=session.rate_limit_resets_at_epoch,
        ),
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
    logger.error("owned_group_cleanup_incomplete", subtype=subtype, evidence=evidence.to_dict())
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
        turn_usage=session.turn_usage,
        evidence=evidence,
        kill_reason=result.kill_reason,
        last_stop_reason=session.last_stop_reason,
        lifespan_started=session.lifespan_started,
        provider=ProviderOutcome(provider_used=provider_used, fallback_activated=False),
        infra=infra,
        api_retry=api_retry,
        api_failure=_build_api_failure_outcome(session),
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


_EVIDENCE_RECOVERABLE_SUBTYPES: frozenset[str] = frozenset({"adjudicated_failure", "unparseable"})


@dataclasses.dataclass(frozen=True, slots=True)
class _StallOutcomeSpec:
    """Distinguishes the STALE and IDLE_STALL branches of _attempt_stall_recovery.

    The retry-policy dispatch and failure-result construction for each branch
    stay in _headless_result.py so each keeps its own visible call to
    _apply_infra_retry_policy -- see
    tests/arch/test_infra_exit_retry_policy_exhaustive.py, which counts exactly
    three such call sites in that module.
    """

    recovered_subtype: str
    recovery_log_message: str


_STALE_SPEC = _StallOutcomeSpec(
    recovered_subtype="recovered_from_stale",
    recovery_log_message="Session went stale but stdout contained a valid result; recovering",
)

_IDLE_STALL_SPEC = _StallOutcomeSpec(
    recovered_subtype="recovered_from_idle_stall",
    recovery_log_message="Session idle-stalled but stdout contained a valid result; recovering",
)


def _attempt_stall_recovery(
    result: SubprocessResult,
    backend: CodingAgentBackend,
    spec: _StallOutcomeSpec,
    *,
    completion_marker: str,
    skill_command: str,
    expected_output_patterns: Sequence[str],
    completion_required: bool,
    provider_used: str,
    write_behavior: WriteBehaviorSpec | None,
    skill_contract: SkillContract | None,
    cwd: str,
    fs_writes_detected: bool,
    git_writes_detected: bool,
    file_changes: Sequence[str],
    write_watch_dirs: Sequence[Path],
) -> tuple[SkillResult | None, ClaudeSessionResult, WriteEvidence, ApiRetryOutcome]:
    """Parse a STALE/IDLE_STALL session's stdout and attempt success-recovery.

    Returns ``(recovered_sr, session, evidence, api_retry)``. When recovery
    succeeds, ``recovered_sr`` is the final adjudicated result and the caller
    must return it directly. Otherwise ``recovered_sr`` is ``None`` and the
    caller proceeds to its own retry-policy dispatch and failure construction,
    using the returned ``session``/``evidence``/``api_retry``.
    """
    session = _parse_stdout(result, backend=backend)
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
    api_retry = _build_api_retry_outcome(session)
    returncode = result.returncode if result.returncode is not None else -1
    can_attempt_recovery = (
        session.subtype == CliSubtype.SUCCESS and session.result.strip() and not session.is_error
    )
    if not can_attempt_recovery:
        return None, session, evidence, api_retry

    success = _compute_success(
        session,
        returncode,
        TerminationReason.COMPLETED,
        completion_marker=completion_marker,
        channel_confirmation=result.channel_confirmation,
        expected_output_patterns=expected_output_patterns,
        completion_required=completion_required,
    )
    if not success:
        return None, session, evidence, api_retry

    logger.warning(spec.recovery_log_message)
    recovered_sr = _make_terminated_result(
        result=result,
        session=session,
        success=True,
        result_text=session.agent_result,
        subtype=spec.recovered_subtype,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        evidence=evidence,
        provider_used=provider_used,
        api_retry=api_retry,
    )
    recovered_sr = _apply_post_session_adjudication(
        recovered_sr, evidence, write_behavior, skill_contract, cwd
    )
    return recovered_sr, session, evidence, api_retry


def _detect_path_contamination(
    result: SubprocessResult,
    backend: CodingAgentBackend,
    *,
    normalized_msgs: NormalizedMessages,
    cwd: str,
    skill_command: str,
    file_changes: Sequence[str],
    evidence: WriteEvidence,
    supports_claude_format_stdout: bool,
) -> tuple[list[str], bool]:
    """Two-factor path-contamination check (see plan #4150).

    Factor 1: a contract-scoped text candidate (an assistant-text token path
    outside CWD, selected from the running skill's own file_path* outputs).
    Factor 2: a boundary-specific write proof (Claude write_path_warnings OR
    Codex completed out-of-CWD FILE_CHANGE with implementation evidence).
    Both factors must hold for terminal classification; text alone is never
    proof. Returns ``(write_path_warnings, is_path_contamination)``.
    """
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
            write_guard_tool_names = backend.capabilities.write_guard_tool_names
            if write_guard_tool_names:
                write_path_warnings = _scan_jsonl_write_paths(
                    result.stdout,
                    cwd,
                    write_tool_names=write_guard_tool_names,
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
        logger.info(
            "text_path_candidate_uncorroborated",
            detail=text_path_violation,
            cwd=cwd,
            skill_name=skill_name,
            claude_boundary_proof=claude_boundary_proof,
            codex_boundary_proof=codex_boundary_proof,
        )

    return write_path_warnings, is_path_contamination


def _apply_contract_recovery_gate(
    sr: SkillResult,
    *,
    has_write_evidence: bool,
    readonly_skill: bool,
    returncode: int,
    skill_command: str,
    audit: AuditLog | None,
    max_consecutive_retries: int,
) -> SkillResult:
    """Promote a terminal adjudicated_failure/unparseable result to retriable.

    When the session was classified as a terminal failure but write evidence
    exists and the process exited cleanly, the model wrote the artifact but
    the structured output token was missing or the stdout stream was
    truncated -- promote to RETRIABLE(CONTRACT_RECOVERY). Re-applies the
    budget guard so budget exhaustion can still cap retries; the caller's own
    first _apply_budget_guard call skips this case because needs_retry is
    False at that point.
    """
    if not (
        not sr.success
        and not sr.needs_retry
        and sr.subtype in _EVIDENCE_RECOVERABLE_SUBTYPES
        and has_write_evidence
        and not readonly_skill
        and (sr.subtype == "adjudicated_failure" or returncode == 0)
    ):
        return sr
    sr = dataclasses.replace(
        sr,
        needs_retry=True,
        retry_reason=RetryReason.CONTRACT_RECOVERY,
    )
    return _apply_budget_guard(sr, skill_command, audit, max_consecutive_retries)


def _apply_closure_verification_gate(
    sr: SkillResult,
    closure_spec: ClosureAuthoritySpec | None,
    closure_report_root: Path | None,
) -> SkillResult:
    """Independently verify the canonical closure report when one is active.

    On failure, demote to execution error so the recipe's on_failure route
    fires. This gate cannot be bypassed by the LLM orchestrator -- it is
    enforced programmatically after session completion.
    """
    if closure_spec is None or closure_report_root is None:
        return sr

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
        return dataclasses.replace(
            sr,
            success=False,
            is_error=True,
            subtype="closure_verification_failed",
            result=f"Closure verification failed: {error_detail}",
        )
    if sr.retry_reason == RetryReason.EMPTY_OUTPUT:
        return dataclasses.replace(sr, is_error=False)
    return sr


def _apply_outcome_qualifier(
    sr: SkillResult,
    skill_contract: SkillContract | None,
) -> SkillResult:
    """Evaluate the skill contract's success qualifier and invariant-violation flag."""
    if skill_contract is None or not skill_contract.outputs:
        return sr
    parsed_fields = dict(sr.outcome_fields or {})
    qualifier: str | None = None
    if sr.success and skill_contract.success_qualifiers:
        from autoskillit.execution.headless._headless_outcome import evaluate_success_qualifier

        qualifier = evaluate_success_qualifier(parsed_fields, skill_contract.success_qualifiers)
    return dataclasses.replace(
        sr,
        outcome_invariant_violated=sr.retry_reason == RetryReason.OUTCOME_INVARIANT,
        outcome_qualifier=qualifier,
    )
