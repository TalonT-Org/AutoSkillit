"""Headless Claude Code session orchestration.

IL-1 module (execution/). Owns the full lifecycle of a headless claude CLI session:
command preparation, subprocess invocation via the injected runner, and
SkillResult construction.

Public API:
    run_headless_core(skill_command, cwd, ctx, *, ...) -> SkillResult
"""

from __future__ import annotations

import dataclasses
import os
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import structlog

from autoskillit.core import (
    CAMPAIGN_ID_ENV_VAR,
    DISPATCH_ID_ENV_VAR,
    KillReason,
    RetryReason,
    SessionTelemetry,
    SkillResult,
    ValidatedAddDir,
    WriteBehaviorSpec,
    claude_code_project_dir,
    collect_version_snapshot,
    extract_skill_name,
    get_logger,
    is_git_worktree,
    temp_dir_display_str,
)
from autoskillit.execution._headless_git import (
    _capture_git_head_sha,
    _compute_loc_changed,
)
from autoskillit.execution._headless_path_tokens import (  # noqa: F401
    _INTENTIONALLY_EXCLUDED_PATH_TOKENS,
    _OUTPUT_PATH_PATTERN,
    _OUTPUT_PATH_TOKENS,
    _WORKTREE_PATH_PATTERN,
    _build_path_token_set,
    _extract_output_paths,
    _extract_worktree_path,
    _validate_output_paths,
)
from autoskillit.execution._headless_recovery import (
    _CHANNEL_B_RECOVERABLE_SUBTYPES,  # noqa: F401
    _NUDGE_TIMEOUT,  # noqa: F401
    _PATH_CAPTURE,  # noqa: F401
    _attempt_contract_nudge,
    _extract_missing_token_hints,  # noqa: F401
    _merge_token_usage,  # noqa: F401
    _recover_block_from_assistant_messages,  # noqa: F401
    _recover_from_separate_marker,  # noqa: F401
    _synthesize_from_write_artifacts,  # noqa: F401
)
from autoskillit.execution._headless_result import (
    _apply_budget_guard,  # noqa: F401
    _build_session_telemetry,
    _build_skill_result,
    _capture_failure,  # noqa: F401
    _resolve_skill_session_id,  # noqa: F401
)
from autoskillit.execution._headless_scan import _scan_jsonl_write_paths  # noqa: F401
from autoskillit.execution.clone_guard import (
    check_and_revert_clone_contamination,
    is_worktree_skill,
    snapshot_clone_state,
)
from autoskillit.execution.commands import (
    build_food_truck_cmd,
    build_leaf_headless_cmd,
)
from autoskillit.execution.recording import RecordingSubprocessRunner

if TYPE_CHECKING:
    from autoskillit.config import (
        AutomationConfig,
    )
    from autoskillit.core import SubprocessResult
    from autoskillit.execution.commands import ClaudeHeadlessCmd
    from autoskillit.pipeline.context import (
        ToolContext,
    )

logger = get_logger(__name__)


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


def _resolve_skill_temp_dir(cwd: str, skill_command: str) -> Path | None:
    """Resolve the skill-specific temp directory for fs write detection."""
    name = extract_skill_name(skill_command)
    if not name:
        return None
    return Path(cwd) / ".autoskillit" / "temp" / name


@dataclasses.dataclass(frozen=True)
class PostSessionMetrics:
    loc_insertions: int
    loc_deletions: int
    effective_cwd: str


def _compute_post_session_metrics(
    cwd: str,
    pre_session_sha: str,
    skill_result: SkillResult,
) -> PostSessionMetrics:
    effective_cwd = skill_result.worktree_path or cwd
    loc_ins, loc_del = _compute_loc_changed(effective_cwd, pre_session_sha)
    return PostSessionMetrics(
        loc_insertions=loc_ins,
        loc_deletions=loc_del,
        effective_cwd=effective_cwd,
    )



async def _execute_claude_headless(
    spec: ClaudeHeadlessCmd,
    cwd: str,
    ctx: ToolContext,
    *,
    skill_command: str = "",
    step_name: str = "",
    kitchen_id: str = "",
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
    recipe_name: str = "",
    recipe_content_hash: str = "",
    recipe_composite_hash: str = "",
    recipe_version: str = "",
    on_spawn: Callable[[int, int], None] | None = None,
    skip_clone_guard: bool = False,
    readonly_skill: bool = False,
) -> SkillResult:
    """Shared subprocess execution for headless Claude sessions.

    Accepts an already-built ClaudeHeadlessCmd and handles runner invocation,
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

    runner = ctx.runner
    if runner is None:
        raise RuntimeError("No subprocess runner configured")

    linux_tracing_cfg = ctx.config.linux_tracing
    _start_ts = datetime.now(UTC).isoformat()
    _start_mono = time.monotonic()
    _versions = collect_version_snapshot()

    _readonly_skill = readonly_skill
    _clone_snapshot = None
    if (
        not skip_clone_guard
        and not is_git_worktree(Path(cwd))
        and (is_worktree_skill(skill_command) or _readonly_skill)
    ):
        _clone_snapshot = await snapshot_clone_state(cwd, runner)

    _skill_temp_dir = _resolve_skill_temp_dir(cwd, skill_command)
    _temp_snapshot_pre: set[str] = set()
    if _skill_temp_dir and _skill_temp_dir.is_dir():
        try:
            _temp_snapshot_pre = {e.name for e in os.scandir(_skill_temp_dir)}
        except OSError:
            _temp_snapshot_pre = set()

    _pre_session_sha = _capture_git_head_sha(cwd)
    _result: SubprocessResult | None = None
    try:
        _result = await runner(
            spec.cmd,
            cwd=Path(cwd),
            timeout=timeout,
            env=spec.env,
            pty_mode=True,
            session_log_dir=_session_log_dir(cwd),
            completion_marker=completion_marker,
            stale_threshold=stale_threshold,
            completion_drain_timeout=cfg.completion_drain_timeout,
            linux_tracing_config=linux_tracing_cfg,
            idle_output_timeout=effective_idle,
            max_suppression_seconds=cfg.max_suppression_seconds,
            on_pid_resolved=on_spawn,
        )
    except Exception as exc:
        logger.error("headless_runner_crashed", exc_info=True)
        _exc_text = traceback.format_exc()
        _log_dir = ctx.config.linux_tracing.log_dir
        try:
            # Deferred: autoskillit.execution.__init__ imports headless.py (L39-42);
            # a top-level import of autoskillit.execution would be circular.
            from autoskillit.execution import flush_session_log

            _api_usage = ctx.github_api_log.drain("") if ctx.github_api_log is not None else None
            flush_session_log(
                log_dir=_log_dir,
                cwd=str(cwd),
                kitchen_id=kitchen_id,
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
                recipe_name=recipe_name,
                recipe_content_hash=recipe_content_hash,
                recipe_composite_hash=recipe_composite_hash,
                recipe_version=recipe_version,
                telemetry=SessionTelemetry(
                    token_usage=None,
                    timing_seconds=None,
                    audit_record=None,
                    github_api_usage=_api_usage,
                    github_api_requests=_api_usage["total_requests"] if _api_usage else 0,
                    loc_insertions=0,
                    loc_deletions=0,
                ),
            )
        except Exception:
            logger.debug("flush_session_log during crash failed", exc_info=True)
        return SkillResult.crashed(
            exception=exc,
            skill_command=skill_command,
            order_id=order_id,
        )
    except BaseException:
        logger.warning("headless_runner_cancelled", exc_info=True)
        _exc_text = traceback.format_exc()
        _log_dir = ctx.config.linux_tracing.log_dir
        try:
            from autoskillit.execution import flush_session_log

            with anyio.CancelScope(shield=True):
                _api_usage = (
                    ctx.github_api_log.drain("") if ctx.github_api_log is not None else None
                )
                flush_session_log(
                    log_dir=_log_dir,
                    cwd=str(cwd),
                    kitchen_id=kitchen_id,
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
                    recipe_name=recipe_name,
                    recipe_content_hash=recipe_content_hash,
                    recipe_composite_hash=recipe_composite_hash,
                    recipe_version=recipe_version,
                    telemetry=SessionTelemetry(
                        token_usage=None,
                        timing_seconds=None,
                        audit_record=None,
                        github_api_usage=_api_usage,
                        github_api_requests=_api_usage["total_requests"] if _api_usage else 0,
                        loc_insertions=0,
                        loc_deletions=0,
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
    if _skill_temp_dir and _skill_temp_dir.is_dir():
        try:
            _temp_snapshot_post = {e.name for e in os.scandir(_skill_temp_dir)}
        except OSError:
            _temp_snapshot_post = set()
        _fs_writes_detected = bool(_temp_snapshot_post - _temp_snapshot_pre)

    audit_count_before = len(ctx.audit.get_report())
    skill_result = _build_skill_result(
        result,
        completion_marker=completion_marker,
        skill_command=skill_command,
        audit=ctx.audit,
        expected_output_patterns=expected_output_patterns,
        cwd=cwd,
        write_behavior=write_behavior,
        fs_writes_detected=_fs_writes_detected,
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
            completion_marker,
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
            skill_command=skill_command,
            readonly_skill=_readonly_skill,
        )

    _metrics = _compute_post_session_metrics(cwd, _pre_session_sha, skill_result)

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
                ),
                write_path_warnings=skill_result.write_path_warnings,
                write_call_count=skill_result.write_call_count,
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
                recipe_name=recipe_name,
                recipe_content_hash=recipe_content_hash,
                recipe_composite_hash=recipe_composite_hash,
                recipe_version=recipe_version,
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

    if step_name:
        try:
            ctx.token_log.record(
                step_name,
                skill_result.token_usage,
                start_ts=result.start_ts,
                end_ts=result.end_ts,
                elapsed_seconds=result.elapsed_seconds,
                order_id=order_id,
                loc_insertions=_metrics.loc_insertions,
                loc_deletions=_metrics.loc_deletions,
            )
        except Exception:
            logger.debug("token_log_record_failed", exc_info=True)
    return skill_result


async def run_headless_core(
    skill_command: str,
    cwd: str,
    ctx: ToolContext,
    *,
    model: str = "",
    step_name: str = "",
    kitchen_id: str = "",
    order_id: str = "",
    campaign_id: str = "",
    dispatch_id: str = "",
    project_dir: str = "",
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
    recipe_version: str = "",
    allowed_write_prefix: str = "",
    readonly_skill: bool = False,
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
        resolved_model = _resolve_model(model, ctx.config)
        spec = build_leaf_headless_cmd(
            skill_command,
            cwd=cwd,
            completion_marker=effective_marker,
            model=resolved_model,
            plugin_source=ctx.plugin_source,
            output_format_value=cfg.output_format.value,
            output_format_required_flags=cfg.output_format.required_cli_flags,
            add_dirs=add_dirs,
            exit_after_stop_delay_ms=cfg.exit_after_stop_delay_ms,
            scenario_step_name=step_name,
            temp_dir_relpath=temp_dir_display_str(ctx.config.workspace.temp_dir),
            allowed_write_prefix=allowed_write_prefix,
        )

        effective_timeout = timeout if timeout is not None else cfg.timeout
        effective_stale = stale_threshold if stale_threshold is not None else cfg.stale_threshold

        logger.debug(
            "run_headless_core_entry",
            cwd=cwd,
            resolved_model=resolved_model,
            timeout=effective_timeout,
            stale_threshold=effective_stale,
            plugin_source=repr(ctx.plugin_source),
            add_dirs=list(add_dirs) if add_dirs else None,
        )

        return await _execute_claude_headless(
            spec,
            cwd,
            ctx,
            skill_command=original_skill_command,
            step_name=step_name,
            kitchen_id=kitchen_id,
            order_id=order_id,
            campaign_id=campaign_id,
            dispatch_id=dispatch_id,
            project_dir=project_dir,
            timeout=float(effective_timeout),
            stale_threshold=float(effective_stale),
            idle_output_timeout=idle_output_timeout,
            expected_output_patterns=expected_output_patterns,
            write_behavior=write_behavior,
            completion_marker=effective_marker,
            recipe_name=recipe_name,
            recipe_content_hash=recipe_content_hash,
            recipe_composite_hash=recipe_composite_hash,
            recipe_version=recipe_version,
            readonly_skill=readonly_skill,
        )


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
        recipe_version: str = "",
        allowed_write_prefix: str = "",
        readonly_skill: bool = False,
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
            allowed_write_prefix=allowed_write_prefix,
            readonly_skill=readonly_skill,
        )

    async def dispatch_food_truck(
        self,
        orchestrator_prompt: str,
        cwd: str,
        *,
        completion_marker: str,
        model: str = "",
        step_name: str = "",
        kitchen_id: str = "",
        order_id: str = "",
        campaign_id: str = "",
        dispatch_id: str = "",
        project_dir: str = "",
        timeout: float | None = None,
        stale_threshold: float | None = None,
        idle_output_timeout: float | None = None,
        env_extras: Mapping[str, str] | None = None,
        requires_packs: Sequence[str] = (),
        on_spawn: Callable[[int, int], None] | None = None,
    ) -> SkillResult:
        cfg = self._ctx.config
        resolved_model = _resolve_model(model, cfg)
        fleet_cfg = cfg.fleet

        merged_extras: dict[str, str] = dict(env_extras) if env_extras else {}
        if requires_packs:
            if "AUTOSKILLIT_L2_TOOL_TAGS" in merged_extras:
                raise ValueError(
                    "dispatch_food_truck: requires_packs and env_extras both specify "
                    "AUTOSKILLIT_L2_TOOL_TAGS — use requires_packs exclusively"
                )
            merged_extras["AUTOSKILLIT_L2_TOOL_TAGS"] = ",".join(sorted(requires_packs))

        idle_cfg_val = cfg.run_skill.idle_output_timeout
        if idle_cfg_val > 0:
            merged_extras.setdefault("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", str(idle_cfg_val))

        spec = build_food_truck_cmd(
            orchestrator_prompt=orchestrator_prompt,
            plugin_source=self._ctx.plugin_source,
            cwd=cwd,
            completion_marker=completion_marker,
            model=resolved_model,
            env_extras=merged_extras or None,
            output_format_value=cfg.run_skill.output_format.value,
        )

        effective_timeout = timeout if timeout is not None else fleet_cfg.default_timeout_sec
        effective_stale = (
            stale_threshold if stale_threshold is not None else cfg.run_skill.stale_threshold
        )

        return await _execute_claude_headless(
            spec,
            cwd,
            self._ctx,
            skill_command="",
            step_name=step_name,
            kitchen_id=kitchen_id,
            order_id=order_id,
            campaign_id=campaign_id,
            dispatch_id=dispatch_id,
            project_dir=project_dir,
            timeout=float(effective_timeout),
            stale_threshold=float(effective_stale),
            idle_output_timeout=idle_output_timeout,
            completion_marker=completion_marker,
            on_spawn=on_spawn,
            skip_clone_guard=True,
        )
