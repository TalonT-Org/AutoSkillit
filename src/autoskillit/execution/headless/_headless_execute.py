"""Shared subprocess execution core for headless Claude sessions."""

from __future__ import annotations

import dataclasses
import os
import time
import traceback
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING

import anyio

import autoskillit.execution.headless._headless_terminal as _terminal
from autoskillit.core import (
    CAMPAIGN_ID_ENV_VAR,
    DISPATCH_ID_ENV_VAR,
    CandidatePreSpawnRejection,
    ClosureAuthoritySpec,
    CmdSpec,
    CodingAgentBackend,
    ExecutionIdentity,
    InfrastructureFaultError,
    LaunchPreparation,
    LaunchResolver,
    ModelIdentity,
    PluginArtifactAuthority,
    PluginLaunchBinding,
    PluginLoadMode,
    ResolvedLaunchContract,
    RetryReason,
    SkillResult,
    WriteBehaviorSpec,
    collect_version_snapshot,
    get_logger,
    is_git_main_checkout,
    is_git_worktree,
    is_in_git_repo,
    new_managed_attempt_id,
)
from autoskillit.core import resolve_skill_temp_dir as _resolve_skill_temp_dir
from autoskillit.execution.child_outcomes import collect_and_project_child_outcomes
from autoskillit.execution.evidence.otlp_sink import LocalOtlpSink
from autoskillit.execution.headless._headless_evidence import (
    _build_error_path_telemetry,
    _build_session_telemetry,
)
from autoskillit.execution.headless._headless_git import (
    _capture_git_head_sha,
    _detect_session_git_writes,
)
from autoskillit.execution.headless._headless_helpers import (
    _compute_post_session_metrics,
    _detect_fs_writes,
    _stat_snapshot,
)
from autoskillit.execution.headless._headless_launch import (
    _attempt_contract_nudge,
    _run_headless_attempt,
)
from autoskillit.execution.headless._headless_model_evidence import (
    _capture_native_session_ids,
    _drain_model_evidence,
)
from autoskillit.execution.headless._headless_result import _build_skill_result
from autoskillit.execution.headless._managed import _attempt as _diag
from autoskillit.execution.headless._managed import (
    _LineageCallbacks,
    _ManagedLineageObserver,
)
from autoskillit.execution.process import DEFAULT_TETHER_CEILING_SECONDS
from autoskillit.execution.quota._quota_observed import record_skill_result_rate_limit
from autoskillit.execution.runtime.clone_guard import (
    GUARD_EXCLUDE_PREFIX,
    build_clone_guard_policy,
    check_and_revert_clone_contamination,
    derive_exclude_prefix,
    is_clone_commit_skill,
    is_path_under_exclude,
    is_worktree_skill,
    snapshot_clone_state,
    validate_pre_session_index,
)

if TYPE_CHECKING:
    from autoskillit.core import ExecutionSelection, SubprocessResult
    from autoskillit.pipeline.context import ToolContext
    from autoskillit.recipe._contracts_types import SkillContract

logger = get_logger(__name__)


async def _execute_claude_headless(
    build_spec: Callable[
        [PluginLaunchBinding | None, Mapping[str, str] | None, str | None],
        CmdSpec,
    ],
    cwd: str,
    ctx: ToolContext,
    *,
    skill_command: str = "",
    step_name: str = "",
    kitchen_id: str = "",
    caller_session_id: str = "",
    backend_resume_session_id: str = "",
    order_id: str = "",
    campaign_id: str = "",
    dispatch_id: str = "",
    project_dir: str = "",
    timeout: float,
    stale_threshold: float,
    idle_output_timeout: float | None = None,
    natural_exit_grace_seconds: float = 3.0,
    expected_output_patterns: Sequence[str] = (),
    write_behavior: WriteBehaviorSpec | None = None,
    completion_marker: str = "",
    prior_completion_markers: Sequence[str] | None = None,
    recipe_name: str = "",
    recipe_content_hash: str = "",
    recipe_composite_hash: str = "",
    recipe_version: str = "",
    on_spawn: Callable[[int, int], None] | None = None,
    skip_clone_guard: bool = False,
    pty_override: bool | None = None,
    readonly_skill: bool = False,
    completion_required: bool = False,
    write_watch_dirs: Sequence[Path] = (),
    provider_name: str = "",
    plugin_authority: PluginArtifactAuthority | None = None,
    plugin_load_mode: PluginLoadMode = PluginLoadMode.NONE,
    retained_binding: PluginLaunchBinding | None = None,
    provider_extras: Mapping[str, str] | None = None,
    enable_deadline_extension: bool = False,
    max_extension_seconds: float = 7200,
    ceiling_seconds: float = DEFAULT_TETHER_CEILING_SECONDS,
    systemd_scope_enabled: bool = False,
    marker_dir: Path | None = None,
    session_id: str | None = None,
    launch_resolver: LaunchResolver,
    launch_preparation: LaunchPreparation,
    resume_launch_contract: ResolvedLaunchContract | None = None,
    model_identity: ModelIdentity = ModelIdentity.unknown(),
    inspector_eligible: bool = False,
    inspector_model: str = "",
    on_launch_resolved: Callable[[ResolvedLaunchContract], None] | None = None,
    on_session_id_resolved: Callable[[str], None] | None = None,
    closure_spec: ClosureAuthoritySpec | None = None,
    closure_report_root: Path | None = None,
    skill_contract: SkillContract | None = None,
    managed_lineage_observer: _ManagedLineageObserver | None = None,
    execution_identity: ExecutionIdentity = ExecutionIdentity(),
    execution_selection: ExecutionSelection | None = None,
    execution_selection_provider: Callable[[], ExecutionSelection | None] | None = None,
    pre_spawn_admission: Callable[
        [ResolvedLaunchContract], Awaitable[CandidatePreSpawnRejection | None]
    ]
    | None = None,
    mark_execution_started: Callable[[], None] | None = None,
    child_role: str | None = None,
    child_attribution_skill: str = "",
) -> SkillResult | CandidatePreSpawnRejection:
    """Shared subprocess execution for headless Claude sessions.

    Acquires one plugin binding per attempt (or reuses ``retained_binding`` when
    the caller owns one), builds that attempt's CmdSpec, and holds it until reaped.
    """
    campaign_id = campaign_id or os.environ.get(CAMPAIGN_ID_ENV_VAR, "")
    dispatch_id = dispatch_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")

    cfg = ctx.config.run_skill
    # Share the spec-builder authority for adapter digest and inactive-team policy.
    force_inactive_agent_teams = ctx.config.agent_backend.force_inactive_agent_teams
    base_effective_idle = _diag._resolve_idle_output_timeout(
        idle_output_timeout, cfg.idle_output_timeout
    )

    current_provider_name: str = provider_name
    current_provider_extras: dict[str, str] = dict(provider_extras or {})

    runner = ctx.runner
    if runner is None:
        raise RuntimeError("No subprocess runner configured")

    _step_backend: CodingAgentBackend = (
        ctx.backend
        if ctx.backend is not None and ctx.backend.name == launch_preparation.selected_backend
        else launch_resolver.backend_for(launch_preparation)
    )

    linux_tracing_cfg = ctx.config.linux_tracing
    _start_ts = datetime.now(UTC).isoformat()
    _start_mono = time.monotonic()
    _start_epoch = time.time()
    _versions = collect_version_snapshot(_step_backend)  # type: ignore[arg-type]

    _readonly_skill = readonly_skill
    _has_write_scope = bool(write_watch_dirs)
    _derived_prefix = derive_exclude_prefix(write_watch_dirs, Path(cwd))
    _output_dir_is_cwd = bool(write_watch_dirs) and write_watch_dirs[0] == Path(cwd)
    _writes_under_exclude = bool(
        write_watch_dirs
        and (
            _output_dir_is_cwd
            or all(
                is_path_under_exclude(d, Path(cwd), GUARD_EXCLUDE_PREFIX) for d in write_watch_dirs
            )
        )
    )
    _clone_guard_policy = build_clone_guard_policy(
        readonly_skill=_readonly_skill,
        has_write_scope=_has_write_scope,
        is_clone_commit=is_clone_commit_skill(skill_command),
        is_worktree=is_worktree_skill(skill_command),
        writes_under_exclude=_writes_under_exclude,
    )
    _clone_snapshot = None
    if (
        not skip_clone_guard
        and not is_git_worktree(Path(cwd))
        and _clone_guard_policy.should_snapshot
    ):
        _clone_snapshot = await snapshot_clone_state(cwd, runner)

    if not skip_clone_guard and is_git_main_checkout(Path(cwd)):
        _pre_sha = _clone_snapshot.head_sha if _clone_snapshot else ""
        try:
            _was_dirty = await validate_pre_session_index(
                cwd,
                runner,
                pre_session_sha=_pre_sha,
                exclude_prefix=_derived_prefix or GUARD_EXCLUDE_PREFIX,
            )
            if _was_dirty:
                logger.warning(
                    "pre_session_index_reset",
                    dirty=True,
                    pre_sha=_pre_sha,
                )
        except Exception:
            logger.warning("validate_pre_session_index_failed", exc_info=True)

    _watch_dirs: list[Path] = list(write_watch_dirs) if write_watch_dirs else []
    if not _watch_dirs:
        _default = _resolve_skill_temp_dir(cwd, skill_command)
        if _default:
            _watch_dirs.append(_default)

    _temp_snapshots_pre: dict[Path, dict[str, tuple[int, int]] | None] = {}
    for _wd in _watch_dirs:
        if _wd.is_dir():
            try:
                _temp_snapshots_pre[_wd] = _stat_snapshot(_wd)
            except OSError:
                logger.warning("watch_dir_pre_scan_failed", watch_dir=str(_wd), exc_info=True)
                _temp_snapshots_pre[_wd] = None
        else:
            # {} means missing at pre-scan; unlike None (OSError), compare it after the run.
            _temp_snapshots_pre[_wd] = {}

    _pre_session_sha = _capture_git_head_sha(cwd)
    _result: SubprocessResult | None = None
    result: SubprocessResult | None = None
    skill_result: SkillResult | None = None
    pending_cancel: BaseException | None = None
    pending_cancel_traceback: TracebackType | None = None
    terminal_exception_text = ""
    terminal_reason_override = ""
    _clone_reverted = False

    def defer_cancellation(exc: BaseException) -> None:
        nonlocal pending_cancel
        nonlocal pending_cancel_traceback
        nonlocal terminal_exception_text
        nonlocal terminal_reason_override
        pending_cancel = exc
        pending_cancel_traceback = exc.__traceback__
        terminal_exception_text = traceback.format_exc()
        terminal_reason_override = "CANCELLED"

    _stream_parser = _step_backend.stream_parser(completion_marker=completion_marker)
    lifecycle_observation_enabled = bool(skill_command) and (
        _step_backend.capabilities.supports_task_lifecycle_events
    )
    resolved_session_ids, capture_resolved_session_id = _capture_native_session_ids(
        on_session_id_resolved
    )
    lineage_callbacks = _LineageCallbacks(
        managed_lineage_observer,
        capture_resolved_session_id,
    )
    launch_logged = False
    spec: CmdSpec | None = None
    current_launch_contract: ResolvedLaunchContract | None = None
    execution_started = False

    def observe_launch(contract: ResolvedLaunchContract) -> None:
        nonlocal current_launch_contract
        current_launch_contract = contract
        if on_launch_resolved is not None:
            on_launch_resolved(contract)

    def observe_execution_started() -> None:
        nonlocal execution_started
        execution_started = True
        if mark_execution_started is not None:
            mark_execution_started()

    sink = LocalOtlpSink.start(ctx.config.linux_tracing.log_dir)
    physical_attempt = 0
    same_binding_nudge_attempted = False
    sink_env = dict(sink.env)
    current_provider_extras.update(sink_env)
    recorder, _observe_managed_spawn, _bind_managed_launch_alias = (
        _diag.build_managed_attempt_wiring(
            child_role=child_role,
            child_attribution_skill=child_attribution_skill,
            step_backend=_step_backend,
            session_id=session_id,
            diagnostic_log_dir=ctx.config.linux_tracing.log_dir,
            on_spawn=on_spawn,
            on_candidate=lineage_callbacks.on_candidate,
        )
    )

    try:
        while True:
            physical_child_id: str | None = None
            try:
                managed_attempt_id = (
                    managed_lineage_observer.allocate_attempt()
                    if managed_lineage_observer is not None
                    else None
                )
                if child_role is not None:
                    physical_child_id = managed_attempt_id or new_managed_attempt_id()
                recorder.start_attempt(physical_child_id)
                if not launch_logged:
                    _diag.log_launch(managed_lineage_observer)
                    launch_logged = True
                physical_attempt += 1
                attempt_result = await _run_headless_attempt(
                    build_spec,
                    runner=runner,
                    backend=_step_backend,
                    launch_resolver=launch_resolver,
                    launch_preparation=launch_preparation,
                    expected_launch_contract=resume_launch_contract,
                    plugin_authority=plugin_authority,
                    plugin_load_mode=plugin_load_mode,
                    retained_binding=retained_binding,
                    provider_extras=current_provider_extras or None,
                    timeout=timeout,
                    pty_override=pty_override,
                    completion_marker=completion_marker,
                    stale_threshold=stale_threshold,
                    completion_drain_timeout=cfg.completion_drain_timeout,
                    natural_exit_grace_seconds=natural_exit_grace_seconds,
                    linux_tracing_config=linux_tracing_cfg,
                    idle_output_timeout=base_effective_idle,
                    max_suppression_seconds=cfg.max_suppression_seconds,
                    child_deferral_ceiling=cfg.completion_child_deferral_ceiling_seconds,
                    on_spawn=_observe_managed_spawn,
                    enable_deadline_extension=enable_deadline_extension,
                    max_extension_seconds=max_extension_seconds,
                    ceiling_seconds=ceiling_seconds,
                    systemd_scope_enabled=systemd_scope_enabled,
                    marker_dir=marker_dir,
                    session_id=session_id,
                    on_session_id_resolved=_bind_managed_launch_alias,
                    stream_parser=_stream_parser,
                    backend_resume_session_id=backend_resume_session_id,
                    lifecycle_observation_enabled=lifecycle_observation_enabled,
                    on_launch_resolved=observe_launch,
                    pre_spawn_admission=pre_spawn_admission,
                    mark_execution_started=observe_execution_started,
                    managed_attempt_id=managed_attempt_id,
                    attempt=physical_attempt,
                    force_inactive_agent_teams=force_inactive_agent_teams,
                    **lineage_callbacks.launch_kwargs,
                )
                if isinstance(attempt_result, CandidatePreSpawnRejection):
                    return attempt_result
                _result, spec = attempt_result
            except InfrastructureFaultError as exc:
                logger.error("headless_runner_infrastructure_fault", exc_info=True)
                result = None
                terminal_exception_text = traceback.format_exc()
                terminal_reason_override = "CRASHED"
                skill_result = SkillResult.infrastructure_fault(
                    exception=exc,
                    skill_command=skill_command,
                    order_id=order_id,
                )
                recorder.record_exception_outcome(skill_result, "infrastructure_fault")
                break
            except Exception as exc:
                logger.error("headless_runner_crashed", exc_info=True)
                result = None
                terminal_exception_text = traceback.format_exc()
                terminal_reason_override = "CRASHED"
                skill_result = SkillResult.crashed(
                    exception=exc,
                    skill_command=skill_command,
                    order_id=order_id,
                )
                recorder.record_exception_outcome(skill_result, "crashed")
                break
            except BaseException as exc:
                logger.warning("headless_runner_cancelled", exc_info=True)
                result = None
                skill_result = SkillResult.cancelled()
                recorder.record_exception_outcome(skill_result, "cancelled")
                defer_cancellation(exc)
                break
            assert _result is not None
            assert spec is not None
            _elapsed = time.monotonic() - _start_mono
            _end_ts = (datetime.fromisoformat(_start_ts) + timedelta(seconds=_elapsed)).isoformat()
            result = dataclasses.replace(  # type: ignore[arg-type]
                _result, start_ts=_start_ts, end_ts=_end_ts, elapsed_seconds=_elapsed
            )

            _fs_writes_detected = _detect_fs_writes(_watch_dirs, _temp_snapshots_pre)

            _git_writes_detected = False
            if is_in_git_repo(Path(cwd)):
                _git_writes_detected = _detect_session_git_writes(cwd, _pre_session_sha)

            audit_count_before = len(ctx.audit.get_report())
            _supports_fmt = _step_backend.capabilities.supports_claude_format_stdout
            skill_result = _build_skill_result(
                result,
                completion_marker=completion_marker,
                skill_command=skill_command,
                audit=ctx.audit,
                expected_output_patterns=expected_output_patterns,
                cwd=cwd,
                write_behavior=write_behavior,
                fs_writes_detected=_fs_writes_detected,
                git_writes_detected=_git_writes_detected,
                prior_completion_markers=prior_completion_markers,
                completion_required=completion_required,
                write_watch_dirs=write_watch_dirs,
                provider_used=current_provider_name,
                supports_claude_format_stdout=_supports_fmt,
                backend=_step_backend,
                readonly_skill=_readonly_skill,
                closure_spec=closure_spec,
                closure_report_root=closure_report_root,
                skill_contract=skill_contract,
                backend_resume_session_id=backend_resume_session_id,
                outcome_ledger=ctx.workspace_outcome_ledger,
            )
            record_skill_result_rate_limit(
                skill_result,
                _step_backend.capabilities.anthropic_provider_capable,
                getattr(ctx.config, "quota_guard", None),
                credential_scope=(
                    current_launch_contract.quota_identity.get("credential_scope")
                    if current_launch_contract is not None
                    else None
                ),
            )

            nudge_selection = (
                execution_selection_provider()
                if execution_selection_provider is not None
                else execution_selection
            )
            nudge_deadline_epoch = (
                nudge_selection.invocation_deadline_epoch
                if nudge_selection is not None
                and nudge_selection.invocation_deadline_epoch is not None
                else _start_epoch + timeout
            )
            nudge_deadline_remaining = nudge_deadline_epoch - time.time()
            if (
                skill_result.needs_retry
                and skill_result.session_id
                and current_launch_contract is not None
                and nudge_deadline_remaining > 0
                and skill_result.retry_reason
                in (RetryReason.CONTRACT_RECOVERY, RetryReason.EARLY_STOP)
            ):
                try:
                    physical_attempt += 1
                    same_binding_nudge_attempted = True
                    app_server_plan = spec.app_server_plan
                    nudge_success = await _attempt_contract_nudge(
                        skill_result,
                        result,
                        expected_output_patterns,
                        completion_marker,
                        cwd,
                        runner,
                        backend=_step_backend,
                        result_parser=_step_backend.result_parser(),
                        provider_extras=current_provider_extras,
                        retry_reason=skill_result.retry_reason,
                        pty_override=pty_override,
                        skill_contract=skill_contract,
                        plugin_authority=plugin_authority,
                        plugin_load_mode=plugin_load_mode,
                        retained_binding=retained_binding,
                        session_home=app_server_plan.session_home if app_server_plan else None,
                        managed_skill_catalog=spec.managed_skill_catalog,
                        launch_resolver=launch_resolver,
                        launch_preparation=launch_preparation,
                        expected_launch_contract=resume_launch_contract,
                        on_launch_resolved=observe_launch,
                        pre_spawn_admission=pre_spawn_admission,
                        mark_execution_started=observe_execution_started,
                        on_session_id_resolved=capture_resolved_session_id,
                        natural_exit_grace_seconds=natural_exit_grace_seconds,
                        nudge_timeout=min(60.0, nudge_deadline_remaining),
                        attempt=physical_attempt,
                        ceiling_seconds=ceiling_seconds,
                        **lineage_callbacks.attempt_kwargs,
                    )
                    if isinstance(nudge_success, CandidatePreSpawnRejection):
                        return nudge_success
                except InfrastructureFaultError:
                    raise
                except BaseException as exc:
                    logger.warning("headless_nudge_cancelled", exc_info=True)
                    skill_result = SkillResult.cancelled()
                    result = None
                    recorder.record_exception_outcome(skill_result, "nudge_cancelled")
                    defer_cancellation(exc)
                    break
                if nudge_success is not None:
                    skill_result = nudge_success

            _clone_reverted = False
            if _clone_snapshot is not None:
                _exclude_prefix = _derived_prefix or GUARD_EXCLUDE_PREFIX
                try:
                    skill_result, _clone_reverted = await check_and_revert_clone_contamination(
                        _clone_snapshot,
                        skill_result,
                        cwd,
                        runner,
                        ctx.audit,
                        skill_command=skill_command,
                        policy=_clone_guard_policy,
                        exclude_prefix=_exclude_prefix,
                    )
                except InfrastructureFaultError:
                    raise
                except BaseException as exc:
                    logger.warning("headless_clone_guard_cancelled", exc_info=True)
                    skill_result = SkillResult.cancelled()
                    result = None
                    recorder.record_exception_outcome(skill_result, "clone_guard_cancelled")
                    defer_cancellation(exc)
                    break

            # skill_result is final now (post nudge/clone-guard); record before retry decides.
            recorder.record_outcome(skill_result, "attempt_final")

            lineage_callbacks.bind_final(skill_result.session_id)
            break

        assert skill_result is not None
        skill_result = _diag._bind_effective_execution_identity(
            skill_result,
            _step_backend,
            execution_identity,
        )
        (
            evidence_session_id,
            resolved_model_identity,
            subagent_model_outcomes,
        ) = _drain_model_evidence(
            sink,
            terminal_session_id=skill_result.session_id,
            captured_session_id=resolved_session_ids[0],
            model_identity=model_identity,
        )
        child_outcomes = collect_and_project_child_outcomes(
            step_backend=_step_backend,
            cwd=cwd,
            evidence_session_id=evidence_session_id,
            diagnostic_log_dir=ctx.config.linux_tracing.log_dir,
        )
        terminal_selection, provider_outcome = _terminal.finalize_terminal_selection(
            execution_selection=execution_selection,
            execution_selection_provider=execution_selection_provider,
            current_launch_contract=current_launch_contract,
            provider_name=current_provider_name,
            same_binding_nudge_attempted=same_binding_nudge_attempted,
            execution_started=execution_started,
            skill_result=skill_result,
            backend=_step_backend,
        )
        recipe_identity = _terminal.build_recipe_identity(
            name=recipe_name,
            content_hash=recipe_content_hash,
            composite_hash=recipe_composite_hash,
            version=recipe_version,
        )

        if result is not None:
            assert spec is not None
            _metrics = _compute_post_session_metrics(cwd, _pre_session_sha, skill_result)
            timing_seconds = result.elapsed_seconds

            # Extract the audit record (if any) added by this session.
            new_audit_records = ctx.audit.get_report_as_dicts()[audit_count_before:]
            audit_record = new_audit_records[0] if new_audit_records else None

            from autoskillit.execution.evidence.session_log import _resolve_session_label

            _token_label = _resolve_session_label(step_name, dispatch_id)
            try:
                ctx.token_log.record(
                    _token_label,
                    skill_result.token_usage,
                    start_ts=result.start_ts,
                    end_ts=result.end_ts,
                    elapsed_seconds=result.elapsed_seconds,
                    order_id=order_id,
                    loc_insertions=_metrics.loc_insertions,
                    loc_deletions=_metrics.loc_deletions,
                    model=resolved_model_identity.effective_model,
                )
            except Exception:
                logger.debug("token_log_record_failed", exc_info=True)
            terminal_telemetry = _build_session_telemetry(
                skill_result=skill_result,
                timing_seconds=timing_seconds,
                audit_record=audit_record,
                github_api_log=ctx.github_api_log,
                loc_insertions=_metrics.loc_insertions,
                loc_deletions=_metrics.loc_deletions,
                session_id=evidence_session_id,
                subagent_model_outcomes=subagent_model_outcomes,
                child_outcomes=child_outcomes,
                step_name=step_name,
                order_id=order_id,
            )
        else:
            terminal_telemetry = _build_error_path_telemetry(
                ctx.github_api_log,
                session_id=evidence_session_id,
                step_name=step_name,
                order_id=order_id,
                execution_identity=skill_result.execution_identity,
                subagent_model_outcomes=subagent_model_outcomes,
                child_outcomes=child_outcomes,
            )

        skill_result = dataclasses.replace(
            skill_result,
            provider=provider_outcome,
            execution_selection=terminal_selection,
        )

        if terminal_selection is not None:
            from autoskillit.execution.evidence.session_log import (
                write_execution_candidate_manifest,
            )

            try:
                write_execution_candidate_manifest(
                    terminal_selection,
                    ctx.config.linux_tracing.log_dir,
                    max_sessions=ctx.config.linux_tracing.max_sessions,
                    project_dir=str(ctx.project_dir),
                    build_protected_campaign_ids=ctx.build_protected_campaign_ids,
                )
            except Exception:
                logger.debug("execution_candidate_manifest_write_failed", exc_info=True)

        terminal_capture_diagnostic = _diag.capture(managed_lineage_observer)
        if _diag.should_flush(result, skill_result, step_name, terminal_capture_diagnostic):
            if result is None:
                from autoskillit.execution import flush_session_log
            else:
                from autoskillit.execution.evidence.session_log import flush_session_log

            flush_kwargs = _terminal.build_terminal_flush_kwargs(
                ctx=ctx,
                result=result,
                skill_result=skill_result,
                cwd=cwd,
                kitchen_id=kitchen_id,
                caller_session_id=caller_session_id,
                order_id=order_id,
                campaign_id=campaign_id,
                dispatch_id=dispatch_id,
                project_dir=project_dir,
                session_id=evidence_session_id,
                skill_command=skill_command,
                step_name=step_name,
                start_ts=_start_ts,
                termination_reason=terminal_reason_override,
                exception_text=terminal_exception_text,
                versions=_versions,
                provider_outcome=provider_outcome,
                recipe_identity=recipe_identity,
                model_identity=resolved_model_identity,
                backend=_step_backend.name,
                channel_b_capable=_step_backend.capabilities.channel_b_capable,
                comm_aliases=_step_backend.capabilities.process_name_aliases,
                telemetry=terminal_telemetry,
                backend_authority=dict(
                    current_launch_contract.backend_authority.to_payload()
                    if current_launch_contract is not None
                    else launch_preparation.backend_authority.to_payload()
                ),
                launch_contract_digest=(
                    current_launch_contract.digest if current_launch_contract is not None else ""
                ),
                native_shell_capture=terminal_capture_diagnostic,
                session_type=lineage_callbacks.session_type,
                execution_selection=terminal_selection,
                clone_contamination_reverted=_clone_reverted,
                is_resume=spec.is_resume if spec is not None else False,
            )
            try:
                with anyio.CancelScope(shield=pending_cancel is not None):
                    flush_session_log(**flush_kwargs)
            except Exception:
                logger.debug("session_log_flush_failed", exc_info=True)
    finally:
        try:
            sink.close()
        except Exception:
            logger.debug("local_otlp_sink_close_failed", exc_info=True)

    _diag.log_exit(terminal_capture_diagnostic, skill_result)
    if pending_cancel is not None:
        raise pending_cancel.with_traceback(pending_cancel_traceback)
    return skill_result
