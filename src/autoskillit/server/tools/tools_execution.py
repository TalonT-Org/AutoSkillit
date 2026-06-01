"""MCP tool handlers: run_cmd, run_python, run_skill."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path

import anyio
import regex as re
import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import (
    DISPATCH_ID_ENV_VAR,
    SKILL_COMMAND_DISPLAY_MAX,
    WORKTREE_SKILLS,
    SkillResult,
    ValidatedAddDir,
    extract_skill_name,
    get_logger,
    truncate_text,
    validate_project_local_skill_dir,
)
from autoskillit.core import resolve_skill_temp_dir as _resolve_skill_temp_dir
from autoskillit.server import mcp
from autoskillit.server._guards import (
    _check_dry_walkthrough,
    _check_input_contracts,
    _require_enabled,
    _require_orchestrator_or_higher,
    _validate_skill_command,
)
from autoskillit.server._misc import (
    SCENARIO_STEP_NAME_ENV,
    _hook_config_overlay_path,
    _pipeline_tracker_path,
)
from autoskillit.server._notify import _notify, track_response_size
from autoskillit.server._subprocess import _run_subprocess
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.workspace import collect_closure_write_paths

logger = get_logger(__name__)

_PURE_SLEEP_RE = re.compile(
    r'^(?:python3?\s+-c\s+["\']import time;\s*time\.sleep\((?P<py_secs>\d+(?:\.\d+)?)\)["\']'
    r"|sleep\s+(?P<sh_secs>\d+(?:\.\d+)?))$"
)

INGREDIENT_LOCK_DENY_PREFIX = "INGREDIENT LOCK ENFORCED"


def _is_absolute_path(path: str) -> bool:
    """Return True if path is an absolute filesystem path."""
    return Path(path).is_absolute()


def _is_backend_incompatible(skill_info: object, effective_backend: str) -> bool:
    """Return True if skill's backend_requirements exclude effective_backend."""
    reqs = getattr(skill_info, "backend_requirements", None)
    return bool(reqs and effective_backend not in reqs)


def _check_ingredient_locks(step_name: str, order_id: str) -> str | None:
    """Check if step_name is locked out by ingredient locks. Returns deny JSON or None."""
    from autoskillit.server import _get_ctx

    ctx = _get_ctx()
    overlay_path = _hook_config_overlay_path(ctx.project_dir)
    if not overlay_path.exists():
        return None
    try:
        overlay = json.loads(overlay_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    locked_steps = overlay.get("locked_steps", {})
    effective_oid = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")

    if effective_oid and effective_oid in locked_steps:
        if locked_steps[effective_oid].get(step_name) is False:
            ingredient_info = overlay.get("locked_ingredients", {}).get(effective_oid, {})
            return json.dumps(
                {
                    "success": False,
                    "is_error": True,
                    "error": (
                        f"{INGREDIENT_LOCK_DENY_PREFIX}: Step '{step_name}' is locked out. "
                        f"Locked ingredients for pipeline '{effective_oid}': {ingredient_info}. "
                        f"Call lock_ingredients(unlock=[...]) to release."
                    ),
                }
            )
    elif not effective_oid:
        for pid, steps in locked_steps.items():
            if steps.get(step_name) is False:
                return json.dumps(
                    {
                        "success": False,
                        "is_error": True,
                        "error": (
                            f"{INGREDIENT_LOCK_DENY_PREFIX}: Step '{step_name}' is locked out "
                            f"by pipeline '{pid}'. Pass order_id to scope the check, "
                            f"or call lock_ingredients(unlock=[...]) to release."
                        ),
                    }
                )
    return None


def _check_pipeline_deps(step_name: str, order_id: str) -> str | None:
    """Check if step_name's dependencies are satisfied. Returns deny JSON or None."""
    from autoskillit.pipeline import canonical_step_name

    effective_oid = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")
    if not effective_oid:
        return None
    from autoskillit.server import _get_ctx

    ctx = _get_ctx()
    tracker_path = _pipeline_tracker_path(ctx.project_dir, effective_oid)
    if not tracker_path.exists():
        return None
    try:
        tracker = json.loads(tracker_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    canonical = canonical_step_name(step_name)
    deps = tracker.get("dependencies", {}).get(canonical, [])
    if not deps:
        return None
    steps = tracker.get("steps", {})
    unmet = [d for d in deps if steps.get(d, {}).get("status") not in ("complete", "skipped")]
    if not unmet:
        return None
    dep_status = {d: steps.get(d, {}).get("status", "unknown") for d in unmet}
    return json.dumps(
        {
            "success": False,
            "is_error": True,
            "error": (
                f"DEPENDENCY UNMET: Step '{step_name}' requires {unmet} to complete first. "
                f"Pipeline '{effective_oid}': {dep_status}."
            ),
        }
    )


def _resolve_step_name_from_recipe(
    skill_command: str,
    active_recipe_steps: dict[str, object],
) -> tuple[str, bool]:
    """Resolve step_name from active_recipe_steps by matching skill_command prefix.

    Returns (step_name, is_ambiguous):
    - (name, False) when exactly one recipe step matches
    - ("", True) when multiple steps match (ambiguous)
    - ("", False) when no steps match
    """
    cmd_prefix = skill_command.split()[0] if skill_command.strip() else ""
    if not cmd_prefix:
        return ("", False)
    matches: list[str] = []
    for step_key, step_obj in active_recipe_steps.items():
        with_args = getattr(step_obj, "with_args", None)
        if not isinstance(with_args, dict):
            continue
        step_sc = with_args.get("skill_command", "")
        if step_sc and step_sc.split()[0] == cmd_prefix:
            matches.append(step_key)
    if len(matches) == 1:
        return (matches[0], False)
    return ("", len(matches) > 1)


def _has_active_locks(order_id: str) -> bool:
    """Return True if any ingredient locks are actively denying steps."""
    from autoskillit.server import _get_ctx

    ctx = _get_ctx()
    overlay_path = _hook_config_overlay_path(ctx.project_dir)
    if not overlay_path.exists():
        return False
    try:
        overlay = json.loads(overlay_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    locked_steps = overlay.get("locked_steps", {})
    if not locked_steps:
        return False
    effective_oid = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")
    if effective_oid:
        return any(v is False for v in locked_steps.get(effective_oid, {}).values())
    return any(v is False for steps in locked_steps.values() for v in steps.values())


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield(result_type="run_cmd")
@track_response_size("run_cmd")
async def run_cmd(
    cmd: str,
    cwd: str,
    timeout: int = 600,
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Run an arbitrary shell command in the specified directory.

    Args:
        cmd: The full command to run (e.g. "make build").
        cwd: Working directory for the command.
        timeout: Max seconds before killing the process (default 600).
        step_name: Optional YAML step key for wall-clock timing accumulation.

    Never raises.
    """
    if (tier_gate := _require_orchestrator_or_higher("run_cmd")) is not None:
        return tier_gate
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        with structlog.contextvars.bound_contextvars(tool="run_cmd", cwd=cwd):
            logger.info("run_cmd", cmd=cmd[:80], cwd=cwd)
            await _notify(
                ctx, "info", f"run_cmd: {cmd[:80]}", "autoskillit.run_cmd", extra={"cwd": cwd}
            )

            from autoskillit.server import _get_ctx

            tool_ctx = _get_ctx()
            _start = time.monotonic()
            try:
                m = _PURE_SLEEP_RE.match(cmd.strip())
                if m:
                    seconds = float(m.group("py_secs") or m.group("sh_secs"))
                    await asyncio.sleep(seconds)
                    return json.dumps(
                        {"success": True, "exit_code": 0, "stdout": "", "stderr": ""}
                    )
                _env: dict[str, str] | None = (
                    {**os.environ, SCENARIO_STEP_NAME_ENV: step_name} if step_name else None
                )
                returncode, stdout, stderr = await _run_subprocess(
                    ["bash", "-c", cmd],
                    cwd=cwd,
                    timeout=float(timeout),
                    env=_env,
                )
                result = {
                    "success": returncode == 0,
                    "exit_code": returncode,
                    "stdout": truncate_text(stdout),
                    "stderr": truncate_text(stderr),
                }
                if not result["success"]:
                    await _notify(
                        ctx,
                        "error",
                        "run_cmd failed",
                        "autoskillit.run_cmd",
                        extra={"exit_code": returncode},
                    )
                return json.dumps(result)
            finally:
                if step_name:
                    tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
    except Exception as exc:
        logger.error("run_cmd unhandled exception", exc_info=True)
        return json.dumps(
            {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
            }
        )


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield(result_type="run_python")
@track_response_size("run_python")
async def run_python(
    callable: str,
    args: dict[str, object] | None = None,
    timeout: int = 30,
    work_dir: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Call a Python function directly by dotted module path.

    Imports the module, resolves the function, and calls it with the
    provided arguments. Use for lightweight decision logic that does
    not need an LLM session (counter checks, status lookups, eligibility
    decisions).

    Both sync and async functions are supported. Async functions are
    awaited directly; sync functions run in a thread pool.

    Args:
        callable: Dotted path to the function (e.g. "mypackage.module.function").
        args: Keyword arguments to pass to the function.
        timeout: Max seconds before aborting the call (default 30).
        work_dir: When set, relative path-like args (output_dir, etc.) are
            anchored to this directory before the callable is invoked.

    Never raises.
    """
    if (tier_gate := _require_orchestrator_or_higher("run_python")) is not None:
        return tier_gate
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        with structlog.contextvars.bound_contextvars(tool="run_python"):
            logger.info("run_python", callable=callable, timeout=timeout)
            await _notify(
                ctx,
                "info",
                f"run_python: {callable}",
                "autoskillit.run_python",
                extra={"callable": callable},
            )
            from autoskillit.server.tools._execution_helpers import (
                _import_and_call,  # noqa: PLC0415
                resolve_relative_path_args,  # noqa: PLC0415
                validate_path_arg_anchoring,  # noqa: PLC0415
            )

            if work_dir and not Path(work_dir).is_absolute():
                return json.dumps(
                    {
                        "success": False,
                        "error": f"run_python: work_dir must be absolute, got {work_dir!r}",
                    }
                )
            anchor_err = validate_path_arg_anchoring(args, work_dir)
            if anchor_err:
                return json.dumps({"success": False, "error": anchor_err})
            resolved_args = args
            if work_dir:
                resolved_args = resolve_relative_path_args(args or {}, work_dir)
            result = await _import_and_call(callable, args=resolved_args, timeout=float(timeout))
            if not result.get("success"):
                await _notify(
                    ctx,
                    "error",
                    "run_python failed",
                    "autoskillit.run_python",
                    extra={"callable": callable},
                )
            return json.dumps(result)
    except Exception as exc:
        logger.error("run_python unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


def _persist_run_skill_state(skill_result: SkillResult, project_dir: Path) -> None:
    from autoskillit.server._misc import persist_run_skill_state  # noqa: PLC0415

    persist_run_skill_state(skill_result, project_dir)


def _clear_run_skill_state(project_dir: Path) -> None:
    from autoskillit.server._misc import clear_run_skill_state  # noqa: PLC0415

    clear_run_skill_state(project_dir)


def _compute_write_prefixes(
    write_watch_dirs: list[Path],
    cwd: str,
    skill_command: str,
) -> tuple[str, tuple[str, ...]]:

    worktree_write_prefixes: list[str] = []
    extracted = extract_skill_name(skill_command)
    if write_watch_dirs and extracted and extracted in WORKTREE_SKILLS:
        worktree_parent = Path(cwd).resolve().parent / "worktrees"
        worktree_write_prefixes.append(str(worktree_parent) + "/")

    base_prefixes = [str(d.resolve()) + "/" for d in write_watch_dirs]
    all_prefixes = base_prefixes + worktree_write_prefixes
    return base_prefixes[0] if base_prefixes else "", tuple(all_prefixes)


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield()
@track_response_size("run_skill")
async def run_skill(
    skill_command: str,
    cwd: str,
    model: str = "",
    step_name: str = "",
    step_provider: str = "",
    order_id: str = "",
    stale_threshold: int | None = None,
    idle_output_timeout: int | None = None,
    output_dir: str = "",
    resume_session_id: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Run a Claude Code headless session with a skill command.

    Returns JSON with: success, result, session_id, subtype, is_error, exit_code,
    needs_retry, retry_reason. When needs_retry is true, retry_reason is:
    - "resume": context/turn limit hit — partial progress on disk, route to on_context_limit.
    - "drain_race": channel confirmed completion but stdout not fully flushed — route to
      on_context_limit (same as resume).
    - "completed_no_flush": session exited with empty stdout but write evidence confirms work was
      performed — route to on_context_limit (same as drain_race/resume).
    - "empty_output": session exited cleanly with no output AND no write evidence — no partial
      progress, route to on_failure.
    - "path_contamination": session wrote files outside its working directory — route to
      on_failure.
    - "early_stop": model stopped before completion marker — route to on_failure.
    - "zero_writes": skill made no writes despite write expectation — route to on_failure.
    - "thinking_stall": model produced thinking blocks only, no text/tool output — route to
      on_context_limit if lifespan_started, else on_failure.
    - "contract_recovery": model completed and wrote artifacts but structured output failed
      pattern validation and nudge could not recover — route to on_context_limit if
      has_progress_evidence, else on_failure.

    This is the correct MCP tool to delegate work to a headless session during
    pipeline execution. NEVER use native tools (Read, Grep, Glob, Edit, Write,
    Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator.
    All code changes, investigation, and research happen through the headless
    session launched by this tool.

    Use this for all skill sessions, including long-running ones that may hit the
    context limit. The 2-hour timeout is the default. When needs_retry is true,
    route to the appropriate resume step (e.g., retry-worktree) rather than
    re-running this step from scratch.

    Args:
        skill_command: The full prompt including skill invocation (e.g. "/investigate ...").
        cwd: Working directory for the claude session.
        model: Model to use (e.g. "sonnet", "opus"). Empty string = use config default.
        step_name: Optional YAML step key (e.g. "implement"). When set, token usage is
            accumulated in the server-side token log, grouped by this name.
        order_id: Optional per-issue/order identifier for token telemetry scoping. When set,
            token and timing entries are keyed by this value, enabling per-issue isolation
            in get_token_summary/get_timing_summary and in the token_summary_appender hook.
        stale_threshold: Override the staleness kill threshold in seconds. When set on
            a RecipeStep, the recipe orchestrator passes it here. None uses the global
            config default (RunSkillConfig.stale_threshold, default 1200s).
        idle_output_timeout: Override the idle stdout kill threshold in seconds.
            0 = disabled for this step. None = use global config
            (RunSkillConfig.idle_output_timeout, default 1000s).
        resume_session_id: Session ID from a previous run_skill call that was interrupted.
            When set, the session is resumed via --resume instead of starting fresh.
            The skill_command becomes a continuation instruction (non-slash text is allowed).
            Pass the session_id from the previous run_skill result JSON.

    Never raises.
    """
    if (tier_gate := _require_orchestrator_or_higher("run_skill")) is not None:
        return tier_gate
    if (gate := _require_enabled()) is not None:
        return gate
    if not resume_session_id and (cmd_error := _validate_skill_command(skill_command)) is not None:
        return cmd_error
    if cwd and not _is_absolute_path(cwd):
        return json.dumps(
            {
                "success": False,
                "error": (
                    f"run_skill: cwd must be an absolute path, got: {cwd!r}. "
                    "Check that the skill resolved the worktree_path to absolute "
                    '(e.g. WORKTREE_PATH="$(cd "${WORKTREE_PATH}" && pwd)").'
                ),
            }
        )
    if cwd and not os.path.isdir(cwd):
        return json.dumps(
            {
                "success": False,
                "error": f"run_skill: cwd does not exist: {cwd}",
            }
        )
    if (
        step_name
        and not resume_session_id
        and (_lock_denial := _check_ingredient_locks(step_name, order_id)) is not None
    ):
        return _lock_denial
    if (
        step_name
        and not resume_session_id
        and (_dep_denial := _check_pipeline_deps(step_name, order_id)) is not None
    ):
        return _dep_denial
    try:
        _sn_token = _oid_token = None
        from autoskillit.server import _get_ctx

        _cleanup_session_id: str | None = None
        tool_ctx = _get_ctx()

        if not step_name and not resume_session_id and tool_ctx.active_recipe_steps:
            _resolved, _ambiguous = _resolve_step_name_from_recipe(
                skill_command, tool_ctx.active_recipe_steps
            )
            if _resolved:
                step_name = _resolved
                logger.warning(
                    "step_name_resolved_from_recipe",
                    step=step_name,
                    command=skill_command[:80],
                )
                if (_lock_denial := _check_ingredient_locks(step_name, order_id)) is not None:
                    return _lock_denial
                if (_dep_denial := _check_pipeline_deps(step_name, order_id)) is not None:
                    return _dep_denial
            elif not _ambiguous and _has_active_locks(order_id):
                return json.dumps(
                    {
                        "success": False,
                        "is_error": True,
                        "error": (
                            f"{INGREDIENT_LOCK_DENY_PREFIX}: step_name is empty and could "
                            "not be resolved from the recipe. Cannot verify lock "
                            "status. Pass step_name explicitly or call "
                            "lock_ingredients(unlock=[...]) to release all locks."
                        ),
                    }
                )

        with structlog.contextvars.bound_contextvars(tool="run_skill", cwd=cwd):
            logger.info("run_skill", command=skill_command[:80], cwd=cwd)
            await _notify(
                ctx,
                "info",
                f"run_skill: {skill_command[:80]}",
                "autoskillit.run_skill",
                extra={"cwd": cwd, "model": model or "default"},
            )

            from autoskillit.server import _get_config

            # Auto-enrich order_id from the fleet dispatcher's env variable when the
            # caller did not pass an explicit value. AUTOSKILLIT_DISPATCH_ID is injected
            # by fleet/_api.py into every L2 food truck session environment and inherited by all
            # sub-sessions, ensuring token log entries carry the correct order_id without
            # requiring recipe authors to thread it through every run_skill call.
            effective_order_id = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")

            if not resume_session_id:
                if (
                    input_error := _check_input_contracts(
                        skill_command, cwd, tool_ctx.input_contract_resolver
                    )
                ) is not None:
                    return input_error

            if _get_config().safety.require_dry_walkthrough:
                if (gate_error := _check_dry_walkthrough(skill_command, cwd)) is not None:
                    return gate_error

            if tool_ctx.executor is None:
                return json.dumps({"success": False, "error": "Executor not configured"})

            provider_extras: dict[str, str] | None = None
            profile_name_out: str = ""
            effective_model = model

            from autoskillit.core import (
                AGENT_BACKEND_CLAUDE_CODE,
                is_feature_enabled,
            )

            _cfg = _get_config()
            if not step_provider and step_name and tool_ctx.active_recipe_steps is not None:
                _recipe_step_pre = tool_ctx.active_recipe_steps.get(step_name)
                if _recipe_step_pre is not None and _recipe_step_pre.provider:
                    step_provider = _recipe_step_pre.provider
                    logger.warning(
                        "step_provider_resolved_from_recipe",
                        step=step_name,
                        provider=step_provider,
                    )

            if is_feature_enabled(
                "providers", _cfg.features, experimental_enabled=_cfg.experimental_enabled
            ):
                from autoskillit.server._guards import (
                    _resolve_model_as_profile,
                    _resolve_provider_profile,
                )

                _profile, _env_dict = _resolve_provider_profile(
                    step_name or "",
                    tool_ctx.recipe_name or "",
                    _cfg.providers,
                    step_provider=step_provider or "",
                )
                if _profile != "anthropic":
                    provider_extras = _env_dict
                    profile_name_out = _profile
                else:
                    effective_model, prof_name, prof_extras = _resolve_model_as_profile(
                        model, _cfg.providers
                    )
                    if prof_extras is not None:
                        provider_extras = prof_extras
                        profile_name_out = prof_name

            if _cfg.model.model_override:
                effective_model = _cfg.model.model_override
            else:
                if tool_ctx.recipe_name:
                    _mo_recipe_map = _cfg.providers.model_overrides.get(tool_ctx.recipe_name)
                    if _mo_recipe_map:
                        _step_mo = _mo_recipe_map.get(step_name) if step_name else None
                        if _step_mo is None:
                            _step_mo = _mo_recipe_map.get("*")
                        if _step_mo:
                            effective_model = _step_mo

            backend_override: str | None = (
                AGENT_BACKEND_CLAUDE_CODE
                if (
                    provider_extras
                    and "ANTHROPIC_BASE_URL" in provider_extras
                    and tool_ctx.backend is not None
                    and not tool_ctx.backend.capabilities.anthropic_provider_capable
                )
                else None
            )

            # Look up artifact validation patterns from skill contract
            expected_output_patterns: list[str] = []
            if tool_ctx.output_pattern_resolver:
                expected_output_patterns = list(tool_ctx.output_pattern_resolver(skill_command))

            # Look up write-expectation metadata from skill contract
            from autoskillit.core import WriteBehaviorSpec

            write_spec: WriteBehaviorSpec | None = None
            if tool_ctx.write_expected_resolver:
                write_spec = tool_ctx.write_expected_resolver(skill_command)

            # Build validated add_dirs via DefaultSessionSkillManager
            from pathlib import Path
            from uuid import uuid4

            from autoskillit.core import resolve_target_skill

            # Resolve correct namespace and prepare for tier2 activation
            resolved_command = skill_command
            target_name: str | None = None
            if tool_ctx.skill_resolver is not None:
                resolved_command, target_name = resolve_target_skill(
                    skill_command, tool_ctx.skill_resolver
                )

            # Backend compatibility gate — fires before both replay and live session paths.
            if target_name and tool_ctx.skill_resolver and tool_ctx.backend is not None:
                _compat_skill_info = tool_ctx.skill_resolver.resolve(target_name)
                if _compat_skill_info:
                    _effective_backend = backend_override or tool_ctx.backend.name
                    if _is_backend_incompatible(_compat_skill_info, _effective_backend):
                        return SkillResult.crashed(
                            exception=RuntimeError(
                                f"Skill {target_name!r} requires backend "
                                f"{sorted(_compat_skill_info.backend_requirements)} but session "
                                f"backend is {_effective_backend!r}."
                            ),
                            skill_command=resolved_command,
                            order_id=effective_order_id,
                        ).to_json()
            elif target_name and not tool_ctx.skill_resolver:
                logger.debug("backend_compat_check_skipped_no_resolver")

            # Server-side recipe step parameter resolution.
            # When a step_name is provided and the recipe's step definition is cached,
            # auto-fill parameters the LLM may have omitted.
            if step_name and tool_ctx.active_recipe_steps is not None:
                _recipe_step = tool_ctx.active_recipe_steps.get(step_name)
                if _recipe_step is not None:
                    if not output_dir and "output_dir" in _recipe_step.with_args:
                        _recipe_output_dir = _recipe_step.with_args["output_dir"]
                        # Skip values containing unresolved template references —
                        # load() returns raw YAML without ingredient resolution,
                        # so ${{ context.* }} placeholders may survive.
                        if "${{" not in _recipe_output_dir:
                            output_dir = _recipe_output_dir
                            logger.warning(
                                "output_dir_resolved_from_recipe",
                                step=step_name,
                                output_dir=output_dir,
                            )

                    if stale_threshold is None and _recipe_step.stale_threshold is not None:
                        stale_threshold = _recipe_step.stale_threshold
                        logger.warning(
                            "stale_threshold_resolved_from_recipe",
                            step=step_name,
                            value=stale_threshold,
                        )

                    if (
                        idle_output_timeout is None
                        and _recipe_step.idle_output_timeout is not None
                    ):
                        idle_output_timeout = _recipe_step.idle_output_timeout
                        logger.warning(
                            "idle_output_timeout_resolved_from_recipe",
                            step=step_name,
                            value=idle_output_timeout,
                        )

            write_watch_dirs: list[Path] = []
            if output_dir:
                resolved_dir = Path(output_dir)
                if not resolved_dir.is_absolute():
                    resolved_dir = Path(cwd) / output_dir
                write_watch_dirs.append(resolved_dir)

            if not write_watch_dirs:
                _default_temp = _resolve_skill_temp_dir(cwd, skill_command)
                if _default_temp:
                    write_watch_dirs.append(_default_temp)

            is_read_only = bool(
                tool_ctx.read_only_resolver and tool_ctx.read_only_resolver(skill_command)
            )
            completion_required = bool(
                tool_ctx.completion_required_resolver
                and tool_ctx.completion_required_resolver(skill_command)
            )
            invocation_marker = f"%%ORDER_UP::{uuid4().hex[:8]}%%"

            skill_add_dirs: list[ValidatedAddDir] = []
            replay_snapshot_used = False
            _runner = tool_ctx.runner
            if (
                step_name
                and _runner is not None
                and getattr(_runner, "skill_snapshots", None)
                and hasattr(_runner, "restore_skill_snapshot")
                and tool_ctx.ephemeral_root is not None
            ):
                _ephemeral_root = tool_ctx.ephemeral_root
                session_id = f"headless-{uuid4().hex[:12]}"
                _cleanup_session_id = session_id
                _restored = _runner.restore_skill_snapshot(  # type: ignore[attr-defined]
                    step_name, _ephemeral_root, session_id
                )
                if _restored is not None:
                    skill_add_dirs.append(_restored)
                    replay_snapshot_used = True
                    logger.debug(
                        "replay_skill_snapshot_restored",
                        step=step_name,
                        session_id=session_id,
                    )

            if not replay_snapshot_used and tool_ctx.session_skill_manager is not None:
                allow_only: frozenset[str] | None = None
                closure: frozenset[str] = frozenset()
                if target_name:
                    closure = tool_ctx.session_skill_manager.compute_skill_closure(target_name)
                    allow_only = closure if closure else None

                session_id = f"headless-{uuid4().hex[:12]}"
                _cleanup_session_id = session_id
                session_root = tool_ctx.session_skill_manager.init_session(
                    session_id,
                    cook_session=False,
                    config=tool_ctx.config,
                    project_dir=Path(cwd),
                    recipe_packs=tool_ctx.active_recipe_packs,
                    recipe_features=tool_ctx.active_recipe_features,
                    allow_only=allow_only,
                    backend=tool_ctx.backend,
                )
                skill_add_dirs.append(session_root)

                if target_name:
                    tool_ctx.session_skill_manager.activate_skill_deps(session_id, target_name)
                    _is_known_skill = (
                        tool_ctx.skill_resolver is not None
                        and tool_ctx.skill_resolver.resolve(target_name) is not None
                    )
                    if _is_known_skill:
                        _skill_md = (
                            Path(session_root.path)
                            / ".claude"
                            / "skills"
                            / target_name
                            / "SKILL.md"
                        )
                        if not _skill_md.exists():
                            logger.error(
                                "target_skill_not_in_session",
                                target=target_name,
                                session_id=session_id,
                                session_root=str(session_root.path),
                            )
                            return SkillResult.crashed(
                                exception=RuntimeError(
                                    f"Target skill {target_name!r} not available in session "
                                    f"{session_id!r}: SKILL.md not found after init_session + "
                                    f"activate_skill_deps. Check tier/feature/pack gating."
                                ),
                                skill_command=resolved_command,
                                session_id=session_id,
                                order_id=effective_order_id,
                            ).to_json()
                    # Extend write_watch_dirs with write_paths from the closure.
                    # Replay-path sessions inherit write scope from their original
                    # snapshot — they don't need re-augmentation because the snapshot
                    # was built from a live session that already had the full prefix set.
                    if closure and tool_ctx.skill_resolver is not None:
                        _raw_write_paths = collect_closure_write_paths(
                            closure, tool_ctx.skill_resolver
                        )
                        if _raw_write_paths:
                            _temp_prefix = os.path.join(cwd, ".autoskillit", "temp")
                            for _rwp in _raw_write_paths:
                                _resolved_wp = Path(
                                    _rwp.replace("{{AUTOSKILLIT_TEMP}}", _temp_prefix)
                                )
                                if _resolved_wp not in write_watch_dirs:
                                    write_watch_dirs.append(_resolved_wp)

            allowed_write_prefix = ""
            allowed_write_prefixes: tuple[str, ...] = ()
            if write_watch_dirs:
                allowed_write_prefix, allowed_write_prefixes = _compute_write_prefixes(
                    write_watch_dirs, cwd, skill_command
                )
            elif is_read_only:
                _skill_temp_name = target_name or ""
                if _skill_temp_name:
                    allowed_write_prefix = os.path.join(
                        cwd, ".autoskillit", "temp", _skill_temp_name, ""
                    )
                else:
                    logger.warning(
                        "read_only_skill_no_target_name",
                        skill_command=skill_command[:SKILL_COMMAND_DISPLAY_MAX],
                    )

            _local_dir = validate_project_local_skill_dir(Path(cwd), tool_ctx.backend)
            if _local_dir is not None:
                skill_add_dirs.append(_local_dir)

            from autoskillit.pipeline.context import (  # noqa: PLC0415
                current_order_id as _current_order_id,
            )
            from autoskillit.pipeline.context import (  # noqa: PLC0415
                current_step_name as _current_step_name,
            )
            from autoskillit.pipeline.tokens import (  # noqa: PLC0415
                canonical_step_name as _canonical_step_name,
            )

            _sn_token = _current_step_name.set(_canonical_step_name(step_name))
            _oid_token = _current_order_id.set(effective_order_id)

            from autoskillit.core import (  # noqa: PLC0415
                claude_code_project_dir,
                execution_marker,
                find_caller_session_id,
            )

            _marker_dir: Path | None = None
            try:
                _marker_dir = claude_code_project_dir(str(tool_ctx.project_dir))
            except OSError:
                pass
            _orchestrator_sid = find_caller_session_id(project_dir=tool_ctx.project_dir)

            _start = time.monotonic()
            try:
                async with execution_marker(
                    _marker_dir,
                    _orchestrator_sid,
                    "run-skill",
                ):
                    skill_result = await tool_ctx.executor.run(
                        resolved_command,
                        cwd,
                        model=effective_model,
                        add_dirs=skill_add_dirs,
                        step_name=step_name,
                        kitchen_id=tool_ctx.kitchen_id,
                        order_id=effective_order_id,
                        expected_output_patterns=expected_output_patterns,
                        write_behavior=write_spec,
                        stale_threshold=float(stale_threshold)
                        if stale_threshold is not None
                        else None,
                        idle_output_timeout=float(idle_output_timeout)
                        if idle_output_timeout is not None
                        else None,
                        completion_marker=invocation_marker,
                        recipe_name=tool_ctx.recipe_name,
                        recipe_content_hash=tool_ctx.recipe_content_hash,
                        recipe_composite_hash=tool_ctx.recipe_composite_hash,
                        recipe_version=tool_ctx.recipe_version,
                        allowed_write_prefix=allowed_write_prefix,
                        allowed_write_prefixes=allowed_write_prefixes,
                        readonly_skill=is_read_only,
                        completion_required=completion_required,
                        write_watch_dirs=write_watch_dirs,
                        provider_extras=provider_extras,
                        profile_name=profile_name_out,
                        provider_name=profile_name_out,
                        backend_override=backend_override,
                        resume_session_id=resume_session_id,
                        marker_dir=_marker_dir,
                        caller_session_id=_orchestrator_sid,
                    )
                if skill_result.success:
                    tool_ctx.audit.record_success(skill_command)
                    _clear_run_skill_state(tool_ctx.project_dir)
                else:
                    await _notify(
                        ctx,
                        "error",
                        "run_skill failed",
                        "autoskillit.run_skill",
                        extra={
                            "exit_code": skill_result.exit_code,
                            "subtype": skill_result.subtype,
                        },
                    )
                    _persist_run_skill_state(skill_result, tool_ctx.project_dir)
                if effective_order_id:
                    skill_result.order_id = effective_order_id
                from autoskillit.server._misc import (  # noqa: PLC0415
                    _refresh_quota_cache,
                )

                if tool_ctx.background is not None:
                    tool_ctx.background.submit(
                        _refresh_quota_cache(tool_ctx.config.quota_guard),
                        label="quota_post_run_refresh",
                    )
                return skill_result.to_json()
            except Exception as exc:
                logger.error("run_skill executor raised unexpectedly", exc_info=True)
                return SkillResult.crashed(
                    exception=exc,
                    skill_command=resolved_command,
                    order_id=effective_order_id,
                ).to_json()
            finally:
                if step_name:
                    tool_ctx.timing_log.record(
                        step_name, time.monotonic() - _start, order_id=effective_order_id
                    )
    except Exception as exc:
        logger.error("run_skill unhandled exception", exc_info=True)
        return SkillResult.crashed(
            exception=exc,
            skill_command=skill_command,
            order_id=order_id,
        ).to_json()
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            logger.warning("run_skill cancelled", exc_info=True)
        _cmd = locals().get("resolved_command", skill_command)
        _oid = locals().get("effective_order_id", order_id)
        return SkillResult.cancelled(
            skill_command=_cmd,  # type: ignore[arg-type]
            order_id=_oid,  # type: ignore[arg-type]
        ).to_json()
    finally:
        if _sn_token is not None:
            _current_step_name.reset(_sn_token)  # type: ignore[possibly-undefined]
        if _oid_token is not None:
            _current_order_id.reset(_oid_token)  # type: ignore[possibly-undefined]
        _sid: str | None = locals().get("_cleanup_session_id")  # type: ignore[assignment]
        if _sid is not None:
            _ssm = tool_ctx.session_skill_manager  # type: ignore[possibly-undefined]
            if _ssm is not None:
                try:
                    _ssm.cleanup_session(_sid)
                except Exception:
                    logger.warning(
                        "session_skill_cleanup_failed",
                        session_id=_sid,
                        exc_info=True,
                    )
            elif tool_ctx.ephemeral_root is not None:  # type: ignore[possibly-undefined]
                _cleanup_dir = tool_ctx.ephemeral_root / _sid  # type: ignore[possibly-undefined]
                if _cleanup_dir.is_dir():
                    try:
                        shutil.rmtree(_cleanup_dir)
                    except Exception:
                        logger.warning(
                            "session_dir_rmtree_failed",
                            path=str(_cleanup_dir),
                            exc_info=True,
                        )
