"""Terminal candidate selection projection for headless launches."""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable
from typing import Any

from autoskillit.core import (
    CodingAgentBackend,
    ContinuationRecommendation,
    ExecutionSelection,
    KillReason,
    ModelIdentity,
    NativeShellCaptureDiagnostic,
    ProviderOutcome,
    RecipeIdentity,
    ResolvedLaunchContract,
    RetryReason,
    SessionTelemetry,
    SessionType,
    SkillResult,
    SubprocessResult,
)


def reconcile_token_evidence(
    skill_result: SkillResult,
    otlp_token_usage: dict[str, Any] | None,
    provider_outcome: ProviderOutcome,
) -> SkillResult:
    """Select correlated OTLP accounting while retaining parser-only turn metadata."""
    if otlp_token_usage is None:
        return dataclasses.replace(skill_result, provider=provider_outcome)
    selected = dict(otlp_token_usage)
    parser_usage = skill_result.token_usage or {}
    if "turn_count" in parser_usage:
        selected["turn_count"] = parser_usage["turn_count"]
    return dataclasses.replace(skill_result, provider=provider_outcome, token_usage=selected)


def finalize_terminal_selection(
    *,
    execution_selection: ExecutionSelection | None,
    execution_selection_provider: Callable[[], ExecutionSelection | None] | None,
    current_launch_contract: ResolvedLaunchContract | None,
    provider_name: str,
    same_binding_nudge_attempted: bool,
    execution_started: bool,
    skill_result: SkillResult,
    backend: CodingAgentBackend,
) -> tuple[ExecutionSelection | None, ProviderOutcome]:
    """Complete the admitted candidate and attach a safe continuation recommendation."""
    selection = (
        execution_selection_provider()
        if execution_selection_provider is not None
        else execution_selection
    )
    if selection is None:
        selection = execution_selection
    if same_binding_nudge_attempted and selection is not None:
        if selection.remaining_retry_budget is not None:
            selection = dataclasses.replace(
                selection,
                remaining_retry_budget=max(0, selection.remaining_retry_budget - 1),
            )

    terminal_attempt = selection.attempts[-1] if selection and selection.attempts else None
    terminal_provider = provider_name
    if current_launch_contract is not None:
        contract_provider = current_launch_contract.provider
        if contract_provider and contract_provider != current_launch_contract.effective_backend:
            terminal_provider = contract_provider
        elif not terminal_provider:
            terminal_provider = contract_provider
    provider_fallback = selection.provider_fallback if selection is not None else False
    terminal_binding_matches = bool(
        terminal_attempt is not None
        and current_launch_contract is not None
        and terminal_attempt.effective_backend == current_launch_contract.effective_backend
        and terminal_attempt.effective_provider == current_launch_contract.provider
    )
    if terminal_attempt is not None:
        assert selection is not None
        terminal_attempt = dataclasses.replace(
            terminal_attempt,
            effective_backend=(
                current_launch_contract.effective_backend
                if current_launch_contract is not None
                else terminal_attempt.effective_backend
            ),
            effective_provider=terminal_provider,
            execution_started=execution_started,
            child_session_id=skill_result.session_id or None,
        )
        selection = dataclasses.replace(
            selection,
            attempts=(*selection.attempts[:-1], terminal_attempt),
            terminal_candidate_id=terminal_attempt.candidate_id,
            completed=True,
            provider_fallback=provider_fallback,
        )
        terminal_provider = terminal_attempt.effective_provider

    if selection is not None and skill_result.retry_reason == RetryReason.RATE_LIMITED:
        now_epoch = time.time()
        deadline_epoch = selection.invocation_deadline_epoch
        remaining_deadline_seconds = max(
            0,
            int(deadline_epoch - now_epoch) if deadline_epoch is not None else 0,
        )
        reset_epoch = skill_result.api_failure.rate_limit.resets_at_epoch
        reset_after_seconds = (
            max(0, int(reset_epoch - now_epoch)) if reset_epoch is not None else 0
        )
        unavailable_reason: str | None = None
        if not skill_result.session_id:
            unavailable_reason = "missing_terminal_session"
        elif terminal_attempt is None or not terminal_attempt.execution_started:
            unavailable_reason = "missing_terminal_binding"
        elif terminal_attempt.child_session_id != skill_result.session_id:
            unavailable_reason = "missing_terminal_binding"
        elif current_launch_contract is None or not terminal_binding_matches:
            unavailable_reason = "launch_contract_mismatch"
        elif not backend.capabilities.session_resume_capable:
            unavailable_reason = "backend_resume_unsupported"
        elif selection.remaining_retry_budget is None or selection.remaining_retry_budget <= 0:
            unavailable_reason = "provider_retry_disabled"
        elif deadline_epoch is None or remaining_deadline_seconds == 0:
            unavailable_reason = "invocation_deadline_elapsed"
        elif reset_epoch is None:
            unavailable_reason = "rate_limit_reset_unavailable"
        elif reset_after_seconds > 60 or remaining_deadline_seconds <= reset_after_seconds:
            unavailable_reason = "rate_limit_reset_outside_deadline"
        selection = dataclasses.replace(
            selection,
            continuation=ContinuationRecommendation(
                resume_session_id=(
                    None if unavailable_reason is not None else skill_result.session_id
                ),
                reason=unavailable_reason,
                reset_after_seconds=(reset_after_seconds if reset_after_seconds <= 60 else 0),
                remaining_deadline_seconds=remaining_deadline_seconds,
            ),
        )
    elif selection is not None:
        selection = dataclasses.replace(
            selection,
            completed=True,
            provider_fallback=provider_fallback,
        )

    return selection, ProviderOutcome(
        provider_used=terminal_provider,
        fallback_activated=provider_fallback,
    )


def build_recipe_identity(
    *,
    name: str,
    content_hash: str,
    composite_hash: str,
    version: str,
) -> RecipeIdentity:
    """Construct the immutable recipe evidence attached to a terminal flush."""
    return RecipeIdentity(
        name=name,
        content_hash=content_hash,
        composite_hash=composite_hash,
        version=version,
    )


def build_terminal_flush_kwargs(
    *,
    ctx: Any,
    result: SubprocessResult | None,
    skill_result: SkillResult,
    cwd: str,
    kitchen_id: str,
    caller_session_id: str,
    order_id: str,
    campaign_id: str,
    dispatch_id: str,
    project_dir: str,
    session_id: str,
    skill_command: str,
    step_name: str,
    start_ts: str,
    termination_reason: str,
    exception_text: str,
    versions: dict[str, Any],
    provider_outcome: ProviderOutcome,
    recipe_identity: RecipeIdentity,
    model_identity: ModelIdentity,
    backend: str,
    channel_b_capable: bool,
    comm_aliases: frozenset[str],
    telemetry: SessionTelemetry,
    backend_authority: dict[str, object],
    launch_contract_digest: str,
    native_shell_capture: NativeShellCaptureDiagnostic | None,
    session_type: SessionType | None,
    execution_selection: ExecutionSelection | None,
    clone_contamination_reverted: bool,
    is_resume: bool,
) -> dict[str, Any]:
    """Build the diagnostic flush payload from a completed terminal attempt."""
    flush_kwargs: dict[str, Any] = {
        "log_dir": ctx.config.linux_tracing.log_dir,
        "cwd": cwd,
        "kitchen_id": kitchen_id,
        "caller_session_id": caller_session_id,
        "order_id": order_id,
        "campaign_id": campaign_id,
        "dispatch_id": dispatch_id,
        "project_dir": project_dir,
        "build_protected_campaign_ids": ctx.build_protected_campaign_ids,
        "session_id": session_id,
        "pid": result.pid if result is not None else 0,
        "skill_command": skill_command,
        "success": skill_result.success,
        "needs_retry": skill_result.needs_retry,
        "retry_reason": skill_result.retry_reason.value,
        "infra": skill_result.infra,
        "api_error_status": skill_result.api_failure.status,
        "is_error": skill_result.is_error,
        "subtype": skill_result.subtype,
        "exit_code": skill_result.exit_code,
        "start_ts": result.start_ts if result is not None else start_ts,
        "proc_snapshots": result.proc_snapshots if result is not None else None,
        "termination_reason": (
            result.termination.value if result is not None else termination_reason
        ),
        "exception_text": exception_text,
        "versions": versions,
        "provider_outcome": provider_outcome,
        "recipe_identity": recipe_identity,
        "max_sessions": ctx.config.linux_tracing.max_sessions,
        "model_identity": model_identity,
        "backend": backend,
        "channel_b_capable": channel_b_capable,
        "comm_aliases": comm_aliases,
        "telemetry": telemetry,
        "backend_authority": backend_authority,
        "launch_contract_digest": launch_contract_digest,
        "native_shell_capture": native_shell_capture,
        "session_type": session_type,
        "execution_selection": execution_selection,
    }
    if result is not None:
        flush_kwargs.update(
            {
                "cli_subtype": skill_result.cli_subtype,
                "end_ts": result.end_ts,
                "elapsed_seconds": result.elapsed_seconds,
                "kill_reason": skill_result.kill_reason.value,
                "snapshot_interval_seconds": ctx.config.linux_tracing.proc_interval,
                "step_name": step_name,
                "api_retry_count": skill_result.api_retry.count,
                "api_retry_last_error": skill_result.api_retry.last_error,
                "api_retry_last_status": skill_result.api_retry.last_status,
                "api_retry_exhausted": skill_result.api_retry.exhausted,
                "ndjson_unknown_event_count": skill_result.ndjson_drift.unknown_event_count,
                "ndjson_unknown_item_count": skill_result.ndjson_drift.unknown_item_count,
                "write_path_warnings": skill_result.write_path_warnings,
                "write_call_count": skill_result.evidence.write_call_count,
                "fs_writes_detected": skill_result.evidence.fs_writes_detected,
                "git_writes_detected": skill_result.evidence.git_writes_detected,
                "file_changes_count": skill_result.evidence.file_changes_count,
                "clone_contamination_reverted": clone_contamination_reverted,
                "tracked_comm": result.tracked_comm,
                "orphaned_tool_result": result.orphaned_tool_result,
                "raw_stdout": (
                    result.stdout
                    if not skill_result.success
                    or skill_result.kill_reason != KillReason.NATURAL_EXIT
                    else ""
                ),
                "last_stop_reason": skill_result.last_stop_reason,
                "is_resume": is_resume,
                "outcome_fields": skill_result.outcome_fields,
                "outcome_invariant_violated": skill_result.outcome_invariant_violated,
                "outcome_qualifier": skill_result.outcome_qualifier,
                "adjudication_verdict": (
                    skill_result.adjudication_verdict.to_dict()
                    if skill_result.adjudication_verdict is not None
                    else None
                ),
            }
        )
    return flush_kwargs
