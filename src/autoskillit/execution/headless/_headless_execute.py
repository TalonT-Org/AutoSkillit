"""Shared subprocess execution core for headless Claude sessions."""

from __future__ import annotations

import dataclasses
import os
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal, cast

import anyio

from autoskillit.core import (
    CAMPAIGN_ID_ENV_VAR,
    DISPATCH_ID_ENV_VAR,
    ClosureAuthoritySpec,
    CmdSpec,
    CodingAgentBackend,
    ExecutionIdentity,
    KillReason,
    LaunchPreparation,
    LaunchResolver,
    ModelIdentity,
    PluginArtifactAuthority,
    PluginLaunchBinding,
    PluginLoadMode,
    ProviderOutcome,
    RecipeIdentity,
    ResolvedLaunchContract,
    RetryReason,
    SkillResult,
    WriteBehaviorSpec,
    collect_version_snapshot,
    get_logger,
    is_feature_enabled,
    is_git_main_checkout,
    is_git_worktree,
    is_in_git_repo,
)
from autoskillit.core import resolve_skill_temp_dir as _resolve_skill_temp_dir
from autoskillit.execution.clone_guard import (
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
    _stat_snapshot,
)
from autoskillit.execution.headless._headless_launch import (
    _attempt_contract_nudge,
    _bind_effective_execution_identity,
    _run_headless_attempt,
)
from autoskillit.execution.headless._headless_result import _build_skill_result
from autoskillit.execution.headless._managed import _attempt as _diag
from autoskillit.execution.headless._managed import (
    _LineageCallbacks,
    _ManagedLineageObserver,
)
from autoskillit.execution.process import DEFAULT_TETHER_CEILING_SECONDS

if TYPE_CHECKING:
    from autoskillit.core import SubprocessResult
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
    provider_fallback_env: dict[str, str] | None = None,
    provider_fallback_name: str = "",
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
) -> SkillResult:
    """Shared subprocess execution for headless Claude sessions.

    Acquires and retains one exact plugin binding for each physical provider
    attempt, builds that attempt's CmdSpec, and holds ownership until the
    subprocess has been reaped.
    """
    campaign_id = campaign_id or os.environ.get(CAMPAIGN_ID_ENV_VAR, "")
    dispatch_id = dispatch_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")

    cfg = ctx.config.run_skill
    # Read from the same authority the spec builders use, so adapter_digest and
    # CmdSpec.force_inactive_agent_teams cannot disagree.
    force_inactive_agent_teams = ctx.config.agent_backend.force_inactive_agent_teams
    if idle_output_timeout is not None:
        _raw_idle = idle_output_timeout
    else:
        env_idle = os.environ.get("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT")
        if env_idle is not None:
            try:
                _raw_idle = float(env_idle)
            except ValueError:
                logger.warning(
                    "AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT: invalid float — falling back to config",
                    env_value=env_idle,
                    fallback=cfg.idle_output_timeout,
                )
                _raw_idle = float(cfg.idle_output_timeout)
        else:
            _raw_idle = float(cfg.idle_output_timeout)
    base_effective_idle: float | None = _raw_idle if _raw_idle > 0.0 else None

    current_provider_name: str = provider_name
    current_provider_extras: dict[str, str] = dict(provider_extras or {})
    fallback_activated: bool = False
    remaining_attempts = ctx.config.providers.provider_retry_limit if provider_fallback_env else 0

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
            # {} sentinel: dir missing at pre-scan. Distinct from None (OSError): {} allows
            # post-scan comparison so session-created files are detected as writes.
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
    lineage_callbacks = _LineageCallbacks(managed_lineage_observer, on_session_id_resolved)
    launch_logged = False
    spec: CmdSpec | None = None
    current_launch_contract: ResolvedLaunchContract | None = None

    def observe_launch(contract: ResolvedLaunchContract) -> None:
        nonlocal current_launch_contract
        current_launch_contract = contract
        if on_launch_resolved is not None:
            on_launch_resolved(contract)

    while True:
        try:
            managed_attempt_id = (
                managed_lineage_observer.allocate_attempt()
                if managed_lineage_observer is not None
                else None
            )
            if not launch_logged:
                _diag.log_launch(managed_lineage_observer)
                launch_logged = True
            _result, spec = await _run_headless_attempt(
                build_spec,
                runner=runner,
                backend=_step_backend,
                launch_resolver=launch_resolver,
                launch_preparation=launch_preparation,
                expected_launch_contract=resume_launch_contract,
                plugin_authority=plugin_authority,
                plugin_load_mode=plugin_load_mode,
                provider_extras=current_provider_extras or None,
                timeout=timeout,
                pty_override=pty_override,
                completion_marker=completion_marker,
                stale_threshold=stale_threshold,
                completion_drain_timeout=cfg.completion_drain_timeout,
                linux_tracing_config=linux_tracing_cfg,
                idle_output_timeout=base_effective_idle,
                max_suppression_seconds=cfg.max_suppression_seconds,
                child_deferral_ceiling=cfg.completion_child_deferral_ceiling_seconds,
                on_spawn=on_spawn,
                enable_deadline_extension=enable_deadline_extension,
                max_extension_seconds=max_extension_seconds,
                ceiling_seconds=ceiling_seconds,
                systemd_scope_enabled=systemd_scope_enabled,
                marker_dir=marker_dir,
                session_id=session_id,
                on_session_id_resolved=lineage_callbacks.on_candidate,
                stream_parser=_stream_parser,
                backend_resume_session_id=backend_resume_session_id,
                lifecycle_observation_enabled=lifecycle_observation_enabled,
                on_launch_resolved=observe_launch,
                managed_attempt_id=managed_attempt_id,
                force_inactive_agent_teams=force_inactive_agent_teams,
                **lineage_callbacks.attempt_kwargs,
            )
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
            break
        except BaseException as exc:
            logger.warning("headless_runner_cancelled", exc_info=True)
            result = None
            skill_result = SkillResult.cancelled()
            defer_cancellation(exc)
            break
        assert _result is not None
        assert spec is not None
        _elapsed = time.monotonic() - _start_mono
        _end_ts = (datetime.fromisoformat(_start_ts) + timedelta(seconds=_elapsed)).isoformat()
        result = dataclasses.replace(  # type: ignore[arg-type]
            _result, start_ts=_start_ts, end_ts=_end_ts, elapsed_seconds=_elapsed
        )

        _fs_writes_detected = False
        for _wd in _watch_dirs:
            if _wd.is_dir():
                try:
                    _post = _stat_snapshot(_wd)
                except OSError:
                    logger.warning("watch_dir_post_scan_failed", watch_dir=str(_wd), exc_info=True)
                    continue
                _pre = _temp_snapshots_pre.get(_wd)
                if _pre is not None and _post != _pre:
                    _fs_writes_detected = True
                    break

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
        )

        if (
            skill_result.needs_retry
            and skill_result.session_id
            and skill_result.retry_reason
            in (RetryReason.CONTRACT_RECOVERY, RetryReason.EARLY_STOP)
        ):
            try:
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
                    session_env=spec.env,
                    launch_resolver=launch_resolver,
                    launch_preparation=launch_preparation,
                    expected_launch_contract=resume_launch_contract,
                    on_launch_resolved=observe_launch,
                    **lineage_callbacks.attempt_kwargs,
                )
            except BaseException as exc:
                logger.warning("headless_nudge_cancelled", exc_info=True)
                skill_result = SkillResult.cancelled()
                result = None
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
            except BaseException as exc:
                logger.warning("headless_clone_guard_cancelled", exc_info=True)
                skill_result = SkillResult.cancelled()
                result = None
                defer_cancellation(exc)
                break

        if (
            skill_result.retry_reason in {RetryReason.STALE, RetryReason.BUDGET_EXHAUSTED}
            and provider_fallback_env is not None
            and remaining_attempts > 0
            and provider_name
            and is_feature_enabled("providers", ctx.config.features)
        ):
            if not fallback_activated:
                current_provider_extras.update(provider_fallback_env)
                if provider_fallback_name:
                    current_provider_name = provider_fallback_name
            fallback_activated = True
            remaining_attempts -= 1
            continue
        lineage_callbacks.bind_final(skill_result.session_id)
        break

    assert skill_result is not None
    skill_result = _bind_effective_execution_identity(
        skill_result,
        _step_backend,
        execution_identity,
    )
    provider_outcome = ProviderOutcome(
        provider_used=current_provider_name,
        fallback_activated=fallback_activated,
    )
    recipe_identity = RecipeIdentity(
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

        from autoskillit.execution.session_log import _resolve_session_label

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
                model=model_identity.effective_model,
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
            step_name=step_name,
            order_id=order_id,
        )
    else:
        terminal_telemetry = _build_error_path_telemetry(
            ctx.github_api_log,
            session_id="",
            step_name=step_name,
            order_id=order_id,
            execution_identity=skill_result.execution_identity,
        )

    skill_result = dataclasses.replace(
        skill_result,
        provider=provider_outcome,
    )

    # One immutable value feeds the event, summary.json, and sessions.jsonl.
    terminal_capture_diagnostic = _diag.capture(managed_lineage_observer)
    if _diag.should_flush(result, skill_result, step_name, terminal_capture_diagnostic):
        if result is None:
            from autoskillit.execution import flush_session_log
        else:
            from autoskillit.execution.session_log import flush_session_log

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
            "session_id": skill_result.session_id if result is not None else "",
            "pid": result.pid if result is not None else 0,
            "skill_command": skill_command,
            "success": skill_result.success,
            "subtype": skill_result.subtype,
            "exit_code": skill_result.exit_code,
            "start_ts": result.start_ts if result is not None else _start_ts,
            "proc_snapshots": result.proc_snapshots if result is not None else None,
            "termination_reason": (
                result.termination.value if result is not None else terminal_reason_override
            ),
            "exception_text": terminal_exception_text,
            "versions": _versions,
            "provider_outcome": provider_outcome,
            "recipe_identity": recipe_identity,
            "max_sessions": ctx.config.linux_tracing.max_sessions,
            "model_identity": model_identity,
            "backend": cast(Literal["claude-code", "codex"], _step_backend.name),
            "channel_b_capable": _step_backend.capabilities.channel_b_capable,
            "comm_aliases": _step_backend.capabilities.process_name_aliases,
            "telemetry": terminal_telemetry,
            "backend_authority": dict(
                current_launch_contract.backend_authority.to_payload()
                if current_launch_contract is not None
                else launch_preparation.backend_authority.to_payload()
            ),
            "launch_contract_digest": (
                current_launch_contract.digest if current_launch_contract is not None else ""
            ),
            "native_shell_capture": terminal_capture_diagnostic,
        }
        if result is not None:
            assert spec is not None
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
                    "clone_contamination_reverted": _clone_reverted,
                    "tracked_comm": result.tracked_comm,
                    "orphaned_tool_result": result.orphaned_tool_result,
                    "raw_stdout": (
                        result.stdout
                        if (
                            not skill_result.success
                            or skill_result.kill_reason != KillReason.NATURAL_EXIT
                        )
                        else ""
                    ),
                    "last_stop_reason": skill_result.last_stop_reason,
                    "is_resume": spec.is_resume,
                    "outcome_fields": skill_result.outcome_fields,
                    "outcome_invariant_violated": skill_result.outcome_invariant_violated,
                    "outcome_qualifier": skill_result.outcome_qualifier,
                }
            )
        try:
            with anyio.CancelScope(shield=pending_cancel is not None):
                flush_session_log(**flush_kwargs)
        except Exception:
            logger.debug("session_log_flush_failed", exc_info=True)

    _diag.log_exit(terminal_capture_diagnostic, skill_result)
    if pending_cancel is not None:
        raise pending_cancel.with_traceback(pending_cancel_traceback)
    return skill_result
