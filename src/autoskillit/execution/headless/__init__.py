"""Headless Claude Code session orchestration.

IL-1 module (execution/). Owns the full lifecycle of a headless claude CLI session:
command preparation, subprocess invocation via the injected runner, and
SkillResult construction.

Public API:
    run_headless_core(skill_command, cwd, ctx, *, ...) -> SkillResult
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from autoskillit.core import (
    FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
    SKILL_COMMAND_DISPLAY_MAX,
    CodingAgentBackend,
    SessionCheckpoint,  # noqa: F401, TC001
    SkillResult,
    SkillSessionConfig,
    ValidatedAddDir,
    WriteBehaviorSpec,
    get_logger,
    temp_dir_display_str,
)
from autoskillit.execution.backends import get_backend  # noqa: F401
from autoskillit.execution.headless._headless_evidence import (
    _adapt_agent_result,  # noqa: F401
    _apply_budget_guard,  # noqa: F401
    _build_error_path_telemetry,  # noqa: F401
    _build_session_telemetry,  # noqa: F401
    _capture_failure,  # noqa: F401
)
from autoskillit.execution.headless._headless_execute import (
    _execute_claude_headless,
)
from autoskillit.execution.headless._headless_git import (
    _capture_git_head_sha,  # noqa: F401
    _compute_loc_changed,  # noqa: F401
    _detect_branch_divergence,  # noqa: F401
)
from autoskillit.execution.headless._headless_helpers import (
    PostSessionMetrics,
    _compute_post_session_metrics,  # noqa: F401
    _derive_step_name_from_skill_command,
    _recursive_snapshot,  # noqa: F401
    _resolve_model,
    _resolve_pty_mode,  # noqa: F401
    _resolve_session_log_dir,
    _session_log_dir,  # noqa: F401
)
from autoskillit.execution.headless._headless_path_tokens import (  # noqa: F401
    _INTENTIONALLY_EXCLUDED_PATH_TOKENS,
    _OUTPUT_PATH_PATTERN,
    _OUTPUT_PATH_TOKENS,
    _RECOVERABLE_PATH_TOKENS,
    _WORKTREE_PATH_PATTERN,
    _build_path_token_set,
    _extract_output_paths,
    _extract_worktree_path,
    _validate_output_paths,
)
from autoskillit.execution.headless._headless_recovery import (
    _CHANNEL_B_RECOVERABLE_SUBTYPES,  # noqa: F401
    _NUDGE_TIMEOUT,  # noqa: F401
    _TOKEN_NAME_RE,  # noqa: F401
    _attempt_contract_nudge,  # noqa: F401
    _extract_missing_token_hints,  # noqa: F401
    _is_path_capture_pattern,  # noqa: F401
    _merge_token_usage,  # noqa: F401
    _recover_block_from_assistant_messages,  # noqa: F401
    _recover_from_separate_marker,  # noqa: F401
    _synthesize_from_write_artifacts,  # noqa: F401
)
from autoskillit.execution.headless._headless_result import (
    _build_skill_result,  # noqa: F401
    _parse_stdout,  # noqa: F401
    _resolve_skill_session_id,  # noqa: F401
)
from autoskillit.execution.headless._headless_scan import _scan_jsonl_write_paths  # noqa: F401
from autoskillit.execution.recording import RecordingSubprocessRunner

if TYPE_CHECKING:
    from autoskillit.pipeline.context import ToolContext

__all__ = [
    "DefaultHeadlessExecutor",
    "PostSessionMetrics",
    "run_headless_core",
]

logger = get_logger(__name__)


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
    allowed_write_prefixes: tuple[str, ...] = (),
    readonly_skill: bool = False,
    write_watch_dirs: Sequence[Path] = (),
    provider_extras: Mapping[str, str] | None = None,
    profile_name: str = "",
    provider_name: str = "",
    provider_fallback_env: dict[str, str] | None = None,
    provider_fallback_name: str = "",
    resume_session_id: str = "",
    resume_checkpoint: SessionCheckpoint | None = None,
    resume_message: str | None = None,
    backend_override: str | None = None,
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
        skill_command=original_skill_command[:SKILL_COMMAND_DISPLAY_MAX],
        step_name=step_name or None,
    ):
        resolved_model = _resolve_model(
            model, ctx.config, step_name=step_name, recipe_name=recipe_name
        )
        add_dirs_tuple = tuple(add_dirs)
        assert ctx.backend is not None, (
            "ctx.backend must be set before run_headless_core is called"
        )
        config = SkillSessionConfig(
            completion_marker=effective_marker,
            model=resolved_model,
            plugin_source=ctx.plugin_source,
            output_format=cfg.output_format,
            add_dirs=add_dirs_tuple,
            exit_after_stop_delay_ms=cfg.exit_after_stop_delay_ms,
            stream_idle_timeout_ms=cfg.stream_idle_timeout_ms,
            scenario_step_name=step_name,
            temp_dir_relpath=temp_dir_display_str(ctx.config.workspace.temp_dir),
            allowed_write_prefix=allowed_write_prefix,
            allowed_write_prefixes=allowed_write_prefixes,
            provider_extras=provider_extras,
            profile_name=profile_name,
            resume_session_id=resume_session_id,
            resume_checkpoint=resume_checkpoint,
            resume_message=resume_message,
        )
        step_backend: CodingAgentBackend | None = None
        if backend_override is not None:
            step_backend = get_backend(backend_override)
            logger.info(
                "step_backend_override_resolved",
                override=backend_override,
                step_backend=step_backend.name,
                ctx_backend=ctx.backend.name,
            )

        _cmd_backend = step_backend if step_backend is not None else ctx.backend
        spec = _cmd_backend.build_skill_session_cmd(skill_command, cwd, config)
        logger.debug("run_headless_core_backend_dispatch", backend=_cmd_backend.name)

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
            write_watch_dirs=write_watch_dirs,
            provider_name=provider_name,
            provider_fallback_env=provider_fallback_env,
            provider_fallback_name=provider_fallback_name,
            provider_extras=provider_extras,
            step_backend=step_backend,
            model_identifier=resolved_model or "",
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
        allowed_write_prefixes: tuple[str, ...] = (),
        readonly_skill: bool = False,
        write_watch_dirs: Sequence[Path] = (),
        provider_extras: Mapping[str, str] | None = None,
        profile_name: str = "",
        provider_name: str = "",
        provider_fallback_env: dict[str, str] | None = None,
        provider_fallback_name: str = "",
        resume_session_id: str = "",
        resume_checkpoint: SessionCheckpoint | None = None,
        resume_message: str | None = None,
        backend_override: str | None = None,
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
            allowed_write_prefixes=allowed_write_prefixes,
            readonly_skill=readonly_skill,
            write_watch_dirs=write_watch_dirs,
            provider_extras=provider_extras,
            profile_name=profile_name,
            provider_name=provider_name,
            provider_fallback_env=provider_fallback_env,
            provider_fallback_name=provider_fallback_name,
            resume_session_id=resume_session_id,
            resume_checkpoint=resume_checkpoint,
            resume_message=resume_message,
            backend_override=backend_override,
        )

    async def dispatch_food_truck(
        self,
        orchestrator_prompt: str,
        cwd: str,
        *,
        completion_marker: str,
        prior_completion_markers: Sequence[str] | None = None,
        resume_session_id: str | None = None,
        resume_checkpoint: SessionCheckpoint | None = None,
        model: str = "",
        step_name: str = "",
        kitchen_id: str = "",
        order_id: str = "",
        campaign_id: str = "",
        dispatch_id: str = "",
        caller_session_id: str = "",
        project_dir: str = "",
        timeout: float | None = None,
        stale_threshold: float | None = None,
        idle_output_timeout: float | None = None,
        env_extras: Mapping[str, str] | None = None,
        requires_packs: Sequence[str] = (),
        on_spawn: Callable[[int, int], None] | None = None,
        allowed_write_prefix: str = "",
        allowed_write_prefixes: tuple[str, ...] = (),
        provider_name: str = "",
        provider_fallback_env: dict[str, str] | None = None,
        provider_fallback_name: str = "",
        sentinel_contract: str = "",
        marker_dir: Path | None = None,
        session_id: str | None = None,
        resume_message: str | None = None,
        backend_override: str | None = None,
    ) -> SkillResult:
        if self._ctx.backend is not None and not self._ctx.backend.capabilities.food_truck_capable:
            raise RuntimeError(
                f"backend does not support food truck dispatch "
                f"(food_truck_capable=False); got {self._ctx.backend.name!r}"
            )
        cfg = self._ctx.config
        resolved_model = _resolve_model(model, cfg, step_name=step_name)
        fleet_cfg = cfg.fleet

        merged_extras: dict[str, str] = dict(env_extras) if env_extras else {}
        if requires_packs:
            if FOOD_TRUCK_TOOL_TAGS_ENV_VAR in merged_extras:
                raise ValueError(
                    f"dispatch_food_truck: requires_packs and env_extras both specify "
                    f"{FOOD_TRUCK_TOOL_TAGS_ENV_VAR} — use requires_packs exclusively"
                )
            merged_extras[FOOD_TRUCK_TOOL_TAGS_ENV_VAR] = ",".join(sorted(requires_packs))

        fleet_idle = fleet_cfg.idle_output_timeout
        if idle_output_timeout is not None:
            merged_extras["AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT"] = str(idle_output_timeout)
        elif fleet_idle > 0:
            merged_extras.setdefault("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", str(fleet_idle))
        else:
            idle_cfg_val = cfg.run_skill.idle_output_timeout
            if idle_cfg_val > 0:
                merged_extras.setdefault("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", str(idle_cfg_val))

        assert self._ctx.backend is not None, "ctx.backend must be set before dispatch_food_truck"
        backend = self._ctx.backend
        cmd_spec = backend.build_food_truck_cmd(
            orchestrator_prompt=orchestrator_prompt,
            plugin_source=self._ctx.plugin_source,
            cwd=cwd,
            completion_marker=completion_marker,
            resume_session_id=resume_session_id,
            resume_checkpoint=resume_checkpoint,
            model=resolved_model,
            env_extras=merged_extras or None,
            output_format=cfg.run_skill.output_format,
            exit_after_stop_delay_ms=cfg.run_skill.exit_after_stop_delay_ms,
            stream_idle_timeout_ms=cfg.run_skill.stream_idle_timeout_ms,
            scenario_step_name=step_name,
            temp_dir_relpath=temp_dir_display_str(cfg.workspace.temp_dir),
            allowed_write_prefix=allowed_write_prefix,
            allowed_write_prefixes=allowed_write_prefixes,
            sentinel_contract=sentinel_contract,
            resume_message=resume_message,
        )
        spec = cmd_spec

        effective_timeout = timeout if timeout is not None else fleet_cfg.default_timeout_sec
        effective_stale = (
            stale_threshold if stale_threshold is not None else cfg.run_skill.stale_threshold
        )
        effective_deadline_ext = fleet_cfg.enable_deadline_extension
        effective_max_ext = float(fleet_cfg.max_extension_seconds)

        effective_idle_out: float | None = (
            idle_output_timeout
            if idle_output_timeout is not None
            else float(fleet_idle)
            if fleet_idle > 0
            else None
        )

        effective_marker_dir: Path | None = marker_dir or (
            _resolve_session_log_dir(cwd, cast(CodingAgentBackend, self._ctx.backend))
            if cwd
            else None
        )

        return await _execute_claude_headless(
            spec,
            cwd,
            self._ctx,
            skill_command="",
            step_name=step_name,
            kitchen_id=kitchen_id,
            caller_session_id=caller_session_id,
            order_id=order_id,
            campaign_id=campaign_id,
            dispatch_id=dispatch_id,
            project_dir=project_dir,
            timeout=float(effective_timeout),
            stale_threshold=float(effective_stale),
            idle_output_timeout=effective_idle_out,
            completion_marker=completion_marker,
            prior_completion_markers=prior_completion_markers,
            on_spawn=on_spawn,
            skip_clone_guard=True,
            pty_override=False,
            provider_name=provider_name,
            provider_fallback_env=provider_fallback_env,
            provider_fallback_name=provider_fallback_name,
            enable_deadline_extension=effective_deadline_ext,
            max_extension_seconds=effective_max_ext,
            marker_dir=effective_marker_dir,
            session_id=session_id,
            model_identifier=resolved_model or "",
        )
