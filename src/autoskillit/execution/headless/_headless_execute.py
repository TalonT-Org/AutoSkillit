"""Shared subprocess execution core for headless Claude sessions."""

from __future__ import annotations

import dataclasses
import os
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import anyio

from autoskillit.core import (
    CAMPAIGN_ID_ENV_VAR,
    DISPATCH_ID_ENV_VAR,
    CmdSpec,
    CodingAgentBackend,
    KillReason,
    ModelIdentity,
    ProviderOutcome,
    RecipeIdentity,
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
    _resolve_pty_mode,
    _resolve_session_log_dir,
    _stat_snapshot,
    assert_headless_cmd,
)
from autoskillit.execution.headless._headless_recovery import _attempt_contract_nudge
from autoskillit.execution.headless._headless_result import _build_skill_result

if TYPE_CHECKING:
    from autoskillit.core import SubprocessResult
    from autoskillit.pipeline.context import ToolContext

logger = get_logger(__name__)


async def _execute_claude_headless(
    spec: CmdSpec,
    cwd: str,
    ctx: ToolContext,
    *,
    skill_command: str = "",
    step_name: str = "",
    kitchen_id: str = "",
    caller_session_id: str = "",
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
    provider_fallback_env: dict[str, str] | None = None,
    provider_fallback_name: str = "",
    provider_extras: Mapping[str, str] | None = None,
    enable_deadline_extension: bool = False,
    max_extension_seconds: float = 7200,
    marker_dir: Path | None = None,
    session_id: str | None = None,
    step_backend: CodingAgentBackend | None = None,
    model_identity: ModelIdentity = ModelIdentity.unknown(),
) -> SkillResult:
    """Shared subprocess execution for headless Claude sessions.

    Accepts an already-built CmdSpec and handles runner invocation,
    exception handling, _build_skill_result, and session log flushing.
    Used by both run_headless_core (leaf path) and
    DefaultHeadlessExecutor.dispatch_food_truck (food truck path).
    """
    campaign_id = campaign_id or os.environ.get(CAMPAIGN_ID_ENV_VAR, "")
    dispatch_id = dispatch_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")

    cfg = ctx.config.run_skill
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
    effective_idle: float | None = _raw_idle if _raw_idle > 0.0 else None

    current_provider_name: str = provider_name
    fallback_activated: bool = False
    remaining_attempts = ctx.config.providers.provider_retry_limit if provider_fallback_env else 0

    runner = ctx.runner
    if runner is None:
        raise RuntimeError("No subprocess runner configured")

    _step_backend: CodingAgentBackend = (
        step_backend if step_backend is not None else cast(CodingAgentBackend, ctx.backend)
    )

    if spec.cmd:
        _binary = Path(spec.cmd[0]).stem
        _expected = _step_backend.capabilities.process_name
        if isinstance(_expected, str) and _expected and _binary != _expected:
            from autoskillit.execution.backends import BACKEND_REGISTRY  # noqa: PLC0415

            _known = {b().capabilities.process_name for b in BACKEND_REGISTRY.values()}
            if _binary in _known:
                raise RuntimeError(
                    f"Backend coherence violation: expected process_name="
                    f"{_expected!r} but binary is {_binary!r}"
                )
    assert_headless_cmd(spec)

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
            await validate_pre_session_index(
                cwd,
                runner,
                pre_session_sha=_pre_sha,
                exclude_prefix=_derived_prefix or GUARD_EXCLUDE_PREFIX,
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
    result: SubprocessResult
    skill_result: SkillResult
    _stream_parser = _step_backend.stream_parser(completion_marker=completion_marker)
    while True:
        try:
            _result = await runner(
                list(spec.cmd),
                cwd=Path(cwd),
                timeout=timeout,
                env=spec.env,
                pty_mode=(
                    pty_override if pty_override is not None else _resolve_pty_mode(_step_backend)
                ),
                session_log_dir=_resolve_session_log_dir(cwd, _step_backend),
                completion_marker=completion_marker,
                stale_threshold=stale_threshold,
                completion_drain_timeout=cfg.completion_drain_timeout,
                linux_tracing_config=linux_tracing_cfg,
                idle_output_timeout=effective_idle,
                max_suppression_seconds=cfg.max_suppression_seconds,
                on_pid_resolved=on_spawn,
                enable_deadline_extension=enable_deadline_extension,
                max_extension_seconds=max_extension_seconds,
                marker_dir=marker_dir,
                session_id=session_id,
                stream_parser=_stream_parser,
                completion_record_types=_step_backend.capabilities.completion_record_types,
                session_record_types=_step_backend.capabilities.session_record_types,
            )
        except Exception as exc:
            logger.error("headless_runner_crashed", exc_info=True)
            _exc_text = traceback.format_exc()
            _log_dir = ctx.config.linux_tracing.log_dir
            try:
                from autoskillit.execution import flush_session_log

                flush_session_log(
                    log_dir=_log_dir,
                    cwd=str(cwd),
                    kitchen_id=kitchen_id,
                    caller_session_id=caller_session_id,
                    order_id=order_id,
                    campaign_id=campaign_id,
                    dispatch_id=dispatch_id,
                    project_dir=project_dir,
                    build_protected_campaign_ids=ctx.build_protected_campaign_ids,
                    session_id="",
                    pid=0,
                    skill_command=skill_command,
                    success=False,
                    subtype="crashed",
                    exit_code=-1,
                    start_ts=_start_ts,
                    proc_snapshots=None,
                    termination_reason="CRASHED",
                    exception_text=_exc_text,
                    versions=_versions,
                    provider_outcome=ProviderOutcome(
                        provider_used=current_provider_name,
                        fallback_activated=fallback_activated,
                    ),
                    recipe_identity=RecipeIdentity(
                        name=recipe_name,
                        content_hash=recipe_content_hash,
                        composite_hash=recipe_composite_hash,
                        version=recipe_version,
                    ),
                    max_sessions=ctx.config.linux_tracing.max_sessions,
                    model_identity=model_identity,
                    telemetry=_build_error_path_telemetry(
                        ctx.github_api_log,
                        session_id="",
                        step_name=step_name,
                        order_id=order_id,
                    ),
                )
            except Exception:
                logger.debug("flush_session_log during crash failed", exc_info=True)
            _crashed = SkillResult.crashed(
                exception=exc,
                skill_command=skill_command,
                order_id=order_id,
            )
            return dataclasses.replace(
                _crashed,
                provider=ProviderOutcome(
                    provider_used=current_provider_name, fallback_activated=fallback_activated
                ),
            )
        except BaseException:
            logger.warning("headless_runner_cancelled", exc_info=True)
            _exc_text = traceback.format_exc()
            _log_dir = ctx.config.linux_tracing.log_dir
            try:
                from autoskillit.execution import flush_session_log

                with anyio.CancelScope(shield=True):
                    flush_session_log(
                        log_dir=_log_dir,
                        cwd=str(cwd),
                        kitchen_id=kitchen_id,
                        caller_session_id=caller_session_id,
                        order_id=order_id,
                        campaign_id=campaign_id,
                        dispatch_id=dispatch_id,
                        project_dir=project_dir,
                        build_protected_campaign_ids=ctx.build_protected_campaign_ids,
                        session_id="",
                        pid=0,
                        skill_command=skill_command,
                        success=False,
                        subtype="cancelled",
                        exit_code=-1,
                        start_ts=_start_ts,
                        proc_snapshots=None,
                        termination_reason="CANCELLED",
                        exception_text=_exc_text,
                        versions=_versions,
                        provider_outcome=ProviderOutcome(
                            provider_used=current_provider_name,
                            fallback_activated=fallback_activated,
                        ),
                        recipe_identity=RecipeIdentity(
                            name=recipe_name,
                            content_hash=recipe_content_hash,
                            composite_hash=recipe_composite_hash,
                            version=recipe_version,
                        ),
                        max_sessions=ctx.config.linux_tracing.max_sessions,
                        model_identity=model_identity,
                        telemetry=_build_error_path_telemetry(
                            ctx.github_api_log,
                            session_id="",
                            step_name=step_name,
                            order_id=order_id,
                        ),
                    )
            except Exception:
                logger.debug("flush_session_log during cancel failed", exc_info=True)
            raise
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
            provider_used=current_provider_name,
            supports_claude_format_stdout=_supports_fmt,
            backend=_step_backend,
            readonly_skill=_readonly_skill,
        )

        if (
            skill_result.needs_retry
            and skill_result.session_id
            and skill_result.retry_reason
            in (RetryReason.CONTRACT_RECOVERY, RetryReason.EARLY_STOP)
        ):
            nudge_success = await _attempt_contract_nudge(
                skill_result,
                result,
                expected_output_patterns,
                completion_marker,
                cwd,
                runner,
                backend=_step_backend,
                result_parser=_step_backend.result_parser(),
                provider_extras=provider_extras,
                retry_reason=skill_result.retry_reason,
                pty_override=pty_override,
            )
            if nudge_success is not None:
                skill_result = nudge_success

        _clone_reverted = False
        if _clone_snapshot is not None:
            _exclude_prefix = _derived_prefix or GUARD_EXCLUDE_PREFIX
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

        if (
            skill_result.retry_reason in {RetryReason.STALE, RetryReason.BUDGET_EXHAUSTED}
            and provider_fallback_env is not None
            and remaining_attempts > 0
            and provider_name
            and is_feature_enabled("providers", ctx.config.features)
        ):
            if not fallback_activated:
                spec = dataclasses.replace(spec, env={**spec.env, **provider_fallback_env})
                if provider_fallback_name:
                    current_provider_name = provider_fallback_name
            fallback_activated = True
            remaining_attempts -= 1
            continue
        break

    _metrics = _compute_post_session_metrics(cwd, _pre_session_sha, skill_result)

    timing_seconds: float = result.elapsed_seconds

    # Extract the audit record (if any) added by this session
    new_audit_records = ctx.audit.get_report_as_dicts()[audit_count_before:]
    audit_record = new_audit_records[0] if new_audit_records else None

    if (
        result.proc_snapshots is not None
        or not skill_result.success
        or bool(step_name)
        or skill_result.token_usage is not None
    ):
        from autoskillit.execution.session_log import flush_session_log

        _codex_log: Path | None = None
        if not _step_backend.capabilities.channel_b_capable and skill_result.session_id:
            try:
                _codex_log = _step_backend.session_locator().locate_session(
                    skill_result.session_id
                )
            except Exception:
                logger.debug("codex_session_locate_failed", exc_info=True)

        try:
            flush_session_log(
                log_dir=ctx.config.linux_tracing.log_dir,
                cwd=cwd,
                kitchen_id=kitchen_id,
                caller_session_id=caller_session_id,
                order_id=order_id,
                campaign_id=campaign_id,
                dispatch_id=dispatch_id,
                project_dir=project_dir,
                build_protected_campaign_ids=ctx.build_protected_campaign_ids,
                session_id=skill_result.session_id,
                pid=result.pid,
                skill_command=skill_command,
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
                telemetry=_build_session_telemetry(
                    skill_result=skill_result,
                    timing_seconds=timing_seconds,
                    audit_record=audit_record,
                    github_api_log=ctx.github_api_log,
                    loc_insertions=_metrics.loc_insertions,
                    loc_deletions=_metrics.loc_deletions,
                    step_name=step_name,
                    order_id=order_id,
                ),
                api_retry_count=skill_result.api_retry.count,
                api_retry_last_error=skill_result.api_retry.last_error,
                api_retry_last_status=skill_result.api_retry.last_status,
                api_retry_exhausted=skill_result.api_retry.exhausted,
                write_path_warnings=skill_result.write_path_warnings,
                write_call_count=skill_result.evidence.write_call_count,
                fs_writes_detected=skill_result.evidence.fs_writes_detected,
                git_writes_detected=skill_result.evidence.git_writes_detected,
                file_changes_count=skill_result.evidence.file_changes_count,
                clone_contamination_reverted=_clone_reverted,
                tracked_comm=result.tracked_comm,
                orphaned_tool_result=result.orphaned_tool_result,
                raw_stdout=result.stdout
                if (
                    not skill_result.success or skill_result.kill_reason != KillReason.NATURAL_EXIT
                )
                else "",
                last_stop_reason=skill_result.last_stop_reason,
                versions=_versions,
                provider_outcome=ProviderOutcome(
                    provider_used=current_provider_name,
                    fallback_activated=fallback_activated,
                ),
                recipe_identity=RecipeIdentity(
                    name=recipe_name,
                    content_hash=recipe_content_hash,
                    composite_hash=recipe_composite_hash,
                    version=recipe_version,
                ),
                max_sessions=ctx.config.linux_tracing.max_sessions,
                model_identity=model_identity,
                is_resume=spec.is_resume,
                codex_log_path=_codex_log,
            )
        except Exception:
            logger.debug("session_log_flush_failed", exc_info=True)

    logger.debug(
        "headless_session_exit",
        success=skill_result.success,
        needs_retry=skill_result.needs_retry,
        subtype=skill_result.subtype,
        session_id=skill_result.session_id,
    )

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
    skill_result = dataclasses.replace(
        skill_result,
        provider=ProviderOutcome(
            provider_used=current_provider_name, fallback_activated=fallback_activated
        ),
    )
    return skill_result
