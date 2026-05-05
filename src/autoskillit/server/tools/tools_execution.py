"""MCP tool handlers: run_cmd, run_python, run_skill."""

from __future__ import annotations

import asyncio
import functools
import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import (
    LayoutError,
    SkillResult,
    ValidatedAddDir,
    get_logger,
    truncate_text,
    validate_add_dir,
)
from autoskillit.server import mcp
from autoskillit.server._guards import (
    _check_dry_walkthrough,
    _require_enabled,
    _require_orchestrator_or_higher,
    _validate_skill_command,
)
from autoskillit.server._misc import SCENARIO_STEP_NAME_ENV
from autoskillit.server._notify import _notify, track_response_size
from autoskillit.server._subprocess import _run_subprocess

logger = get_logger(__name__)


async def _import_and_call(
    dotted_path: str,
    args: dict[str, object] | None = None,
    timeout: float = 30,
) -> dict[str, object]:
    """Import a Python callable by dotted path and invoke it.

    Returns dict with 'success', 'result' (or 'error').
    Handles sync and async callables, with timeout protection.
    """
    import importlib
    import inspect

    if args is None:
        args = {}

    if "." not in dotted_path:
        return {"success": False, "error": f"Invalid dotted path: {dotted_path!r}"}

    module_path, attr_name = dotted_path.rsplit(".", 1)

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        return {"success": False, "error": f"Import failed for {module_path!r}: {exc}"}

    try:
        func = getattr(module, attr_name)
    except AttributeError:
        return {
            "success": False,
            "error": f"Module {module_path!r} has no attribute {attr_name!r}",
        }

    if not callable(func):
        return {"success": False, "error": f"{dotted_path!r} is not callable"}

    sig = inspect.signature(func)
    coerced: dict[str, object] = {}
    for key, val in args.items():
        if val is None and key in sig.parameters:
            param = sig.parameters[key]
            if param.default is not inspect.Parameter.empty and param.default is not None:
                logger.warning(
                    "run_python null-arg coerced to default",
                    callable=dotted_path,
                    arg=key,
                    default=repr(param.default),
                )
                coerced[key] = param.default
                continue
        coerced[key] = val
    args = coerced

    try:
        if inspect.iscoroutinefunction(func):
            result = await asyncio.wait_for(func(**args), timeout=timeout)
        else:
            result = await asyncio.wait_for(asyncio.to_thread(func, **args), timeout=timeout)
    except TimeoutError:
        logger.warning(
            "run_python timed out; sync thread may continue running",
            dotted_path=dotted_path,
            timeout=timeout,
        )
        return {"success": False, "error": f"Timeout after {timeout}s calling {dotted_path}"}
    except Exception as exc:
        logger.warning(
            "run_python execution failed",
            dotted_path=dotted_path,
            error=type(exc).__name__,
        )
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        json.dumps(result)
        return {"success": True, "result": result}
    except (TypeError, ValueError):
        return {"success": True, "result": str(result)}


def _get_food_truck_prompt_builder() -> Callable[..., str]:
    """Return the food truck prompt builder with mcp_prefix pre-bound."""
    from autoskillit.core import detect_autoskillit_mcp_prefix
    from autoskillit.fleet import _build_food_truck_prompt

    mcp_prefix = detect_autoskillit_mcp_prefix()
    return functools.partial(_build_food_truck_prompt, mcp_prefix=mcp_prefix)


_PURE_SLEEP_RE = re.compile(
    r'^(?:python3?\s+-c\s+["\']import time;\s*time\.sleep\((?P<py_secs>\d+(?:\.\d+)?)\)["\']'
    r"|sleep\s+(?P<sh_secs>\d+(?:\.\d+)?))$"
)


def _is_absolute_path(path: str) -> bool:
    """Return True if path is an absolute filesystem path."""
    return Path(path).is_absolute()


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
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
    if (headless := _require_orchestrator_or_higher("run_cmd")) is not None:
        return headless
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(tool="run_cmd", cwd=cwd)
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
                return json.dumps({"success": True, "exit_code": 0, "stdout": "", "stderr": ""})
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
@track_response_size("run_python")
async def run_python(
    callable: str,
    args: dict[str, object] | None = None,
    timeout: int = 30,
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

    Never raises.
    """
    if (headless := _require_orchestrator_or_higher("run_python")) is not None:
        return headless
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(tool="run_python")
        logger.info("run_python", callable=callable, timeout=timeout)
        await _notify(
            ctx,
            "info",
            f"run_python: {callable}",
            "autoskillit.run_python",
            extra={"callable": callable},
        )
        result = await _import_and_call(callable, args=args, timeout=float(timeout))
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


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@track_response_size("run_skill")
async def run_skill(
    skill_command: str,
    cwd: str,
    model: str = "",
    step_name: str = "",
    order_id: str = "",
    stale_threshold: int | None = None,
    idle_output_timeout: int | None = None,
    output_dir: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Run a Claude Code headless session with a skill command.

    Returns JSON with: success, result, session_id, subtype, is_error, exit_code,
    needs_retry, retry_reason. When needs_retry is true, retry_reason is:
    - "resume": context/turn limit hit — partial progress on disk, route to on_context_limit.
    - "drain_race": channel confirmed completion but stdout not fully flushed — route to
      on_context_limit (same as resume).
    - "empty_output": session exited cleanly but produced no output — no partial progress,
      route to on_failure.
    - "path_contamination": session wrote files outside its working directory — route to
      on_failure.
    - "early_stop": model stopped before completion marker — route to on_failure.
    - "zero_writes": skill made no writes despite write expectation — route to on_failure.

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
            (RunSkillConfig.idle_output_timeout, default 600s).

    Never raises.
    """
    if (headless := _require_orchestrator_or_higher("run_skill")) is not None:
        return headless
    if (gate := _require_enabled()) is not None:
        return gate
    if (cmd_error := _validate_skill_command(skill_command)) is not None:
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
    try:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(tool="run_skill", cwd=cwd)
        logger.info("run_skill", command=skill_command[:80], cwd=cwd)
        await _notify(
            ctx,
            "info",
            f"run_skill: {skill_command[:80]}",
            "autoskillit.run_skill",
            extra={"cwd": cwd, "model": model or "default"},
        )

        from autoskillit.server import _get_config, _get_ctx

        # Auto-enrich order_id from the fleet dispatcher's env variable when the
        # caller did not pass an explicit value. AUTOSKILLIT_DISPATCH_ID is injected
        # by fleet/_api.py into every L3 session environment and inherited by all
        # sub-sessions, ensuring token log entries carry the correct order_id without
        # requiring recipe authors to thread it through every run_skill call.
        effective_order_id = order_id or os.environ.get("AUTOSKILLIT_DISPATCH_ID", "")

        if _get_config().safety.require_dry_walkthrough:
            if (gate_error := _check_dry_walkthrough(skill_command, cwd)) is not None:
                return gate_error

        tool_ctx = _get_ctx()
        if tool_ctx.executor is None:
            return json.dumps({"success": False, "error": "Executor not configured"})

        provider_extras: dict[str, str] | None = None
        profile_name_out: str = ""

        from autoskillit.core import is_feature_enabled

        _cfg = _get_config()
        if is_feature_enabled(
            "providers", _cfg.features, experimental_enabled=_cfg.experimental_enabled
        ):
            from autoskillit.server._guards import _resolve_provider_profile

            _profile, _env_dict = _resolve_provider_profile(
                step_name or "", tool_ctx.recipe_name or "", _cfg.providers
            )
            if _profile != "anthropic":
                provider_extras = _env_dict
                profile_name_out = _profile

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

        write_watch_dirs: list[Path] = []
        if output_dir:
            resolved_dir = Path(output_dir)
            if not resolved_dir.is_absolute():
                resolved_dir = Path(cwd) / output_dir
            write_watch_dirs.append(resolved_dir)

        is_read_only = bool(
            tool_ctx.read_only_resolver and tool_ctx.read_only_resolver(skill_command)
        )
        allowed_write_prefix = ""
        if is_read_only:
            if write_watch_dirs:
                allowed_write_prefix = str(write_watch_dirs[0]) + "/"
            else:
                _skill_temp_name = target_name or ""
                if _skill_temp_name:
                    allowed_write_prefix = os.path.join(
                        cwd, ".autoskillit", "temp", _skill_temp_name, ""
                    )
                else:
                    logger.warning(
                        "read_only_skill_no_target_name",
                        skill_command=skill_command[:100],
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
            if target_name:
                closure = tool_ctx.session_skill_manager.compute_skill_closure(target_name)
                allow_only = closure if closure else None

            session_id = f"headless-{uuid4().hex[:12]}"
            session_root = tool_ctx.session_skill_manager.init_session(
                session_id,
                cook_session=False,
                config=tool_ctx.config,
                project_dir=Path(cwd),
                recipe_packs=tool_ctx.active_recipe_packs,
                recipe_features=tool_ctx.active_recipe_features,
                allow_only=allow_only,
            )
            skill_add_dirs.append(session_root)

            if target_name:
                tool_ctx.session_skill_manager.activate_skill_deps(session_id, target_name)
        try:
            skill_add_dirs.append(validate_add_dir(Path(cwd)))
        except LayoutError:
            pass  # cwd has no project-local skills — already accessible as working dir

        _start = time.monotonic()
        try:
            skill_result = await tool_ctx.executor.run(
                resolved_command,
                cwd,
                model=model,
                add_dirs=skill_add_dirs,
                step_name=step_name,
                kitchen_id=tool_ctx.kitchen_id,
                order_id=effective_order_id,
                expected_output_patterns=expected_output_patterns,
                write_behavior=write_spec,
                stale_threshold=float(stale_threshold) if stale_threshold is not None else None,
                idle_output_timeout=float(idle_output_timeout)
                if idle_output_timeout is not None
                else None,
                completion_marker=invocation_marker,
                recipe_name=tool_ctx.recipe_name,
                recipe_content_hash=tool_ctx.recipe_content_hash,
                recipe_composite_hash=tool_ctx.recipe_composite_hash,
                recipe_version=tool_ctx.recipe_version,
                allowed_write_prefix=allowed_write_prefix,
                readonly_skill=is_read_only,
                write_watch_dirs=write_watch_dirs,
                provider_extras=provider_extras,
                profile_name=profile_name_out,
            )
            if skill_result.success:
                tool_ctx.audit.record_success(skill_command)
            else:
                await _notify(
                    ctx,
                    "error",
                    "run_skill failed",
                    "autoskillit.run_skill",
                    extra={"exit_code": skill_result.exit_code, "subtype": skill_result.subtype},
                )
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
    except BaseException:
        logger.warning("run_skill cancelled", exc_info=True)
        raise


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "fleet"},
    annotations={"readOnlyHint": True},
)
@track_response_size("dispatch_food_truck")
async def dispatch_food_truck(
    recipe: str,
    task: str,
    ingredients: dict[str, str] | None = None,
    dispatch_name: str | None = None,
    timeout_sec: int | None = None,
    capture: dict[str, str] | None = None,
    resume_session_id: str | None = None,
    ctx: Context = CurrentContext(),
) -> str:
    """Dispatch a single food truck L3 session for one recipe.

    Spawns a headless subprocess that executes the given recipe with the
    provided task and ingredient overrides. Returns a JSON envelope with
    dispatch_id, l3_session_id, l3_payload, and token_usage.

    Args:
        recipe: Recipe name to dispatch (must be kind=standard).
        task: Task description for the L3 session.
        ingredients: Optional ingredient overrides (all values must be strings).
        dispatch_name: Optional display name for the dispatch record.
        timeout_sec: Optional L3 session timeout override in seconds.
        capture: Optional dict mapping capture keys to "${{ result.field }}" templates.
            Extracted values are persisted in the campaign context for downstream
            dispatches to reference via "${{ campaign.key }}" in their ingredients.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate

    try:
        # Feature guard: config authority check independent of MCP visibility state.
        # Fleet sessions open the gate unconditionally at boot; this catch-all ensures
        # dispatch_food_truck never executes when features.fleet is disabled in config.
        from autoskillit.core import FleetErrorCode, fleet_error, is_feature_enabled
        from autoskillit.server import _get_ctx as _get_ctx_for_feature_check

        _feature_ctx = _get_ctx_for_feature_check()
        if not is_feature_enabled(
            "fleet",
            _feature_ctx.config.features,
            experimental_enabled=_feature_ctx.config.experimental_enabled,
        ):
            return fleet_error(
                FleetErrorCode.FLEET_FEATURE_DISABLED,
                "Fleet feature is disabled. Set features.experimental_enabled: true to enable.",
            )

        if os.environ.get("AUTOSKILLIT_HEADLESS") == "1":
            return fleet_error(
                FleetErrorCode.FLEET_HARD_REFUSAL_HEADLESS,
                "dispatch_food_truck cannot be called from headless sessions.",
            )

        campaign_state_path_str = os.environ.get("AUTOSKILLIT_CAMPAIGN_STATE_PATH")
        continue_on_failure_str = os.environ.get("AUTOSKILLIT_CONTINUE_ON_FAILURE", "false")
        if campaign_state_path_str and continue_on_failure_str.lower() != "true":
            from autoskillit.fleet import has_failed_dispatch  # noqa: PLC0415

            if has_failed_dispatch(Path(campaign_state_path_str)):
                return fleet_error(
                    FleetErrorCode.FLEET_CAMPAIGN_HALTED,
                    "Campaign halted: a prior dispatch failed and "
                    "continue_on_failure is false. "
                    "No further dispatches permitted.",
                )

        from autoskillit.fleet import execute_dispatch
        from autoskillit.server import _get_ctx
        from autoskillit.server._misc import (  # noqa: PLC0415
            _refresh_quota_cache,
            check_and_sleep_if_needed,
            invalidate_cache,
        )

        tool_ctx = _get_ctx()
        result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe=recipe,
            task=task,
            ingredients=ingredients,
            dispatch_name=dispatch_name,
            timeout_sec=timeout_sec,
            prompt_builder=_get_food_truck_prompt_builder(),
            quota_checker=check_and_sleep_if_needed,
            quota_refresher=_refresh_quota_cache,
            cache_invalidator=invalidate_cache,
            capture=capture,
            resume_session_id=resume_session_id,
        )

        campaign_state_path_str = os.environ.get("AUTOSKILLIT_CAMPAIGN_STATE_PATH")
        if campaign_state_path_str and dispatch_name:
            try:
                envelope = json.loads(result)
                campaign_state_path = Path(campaign_state_path_str)
                if campaign_state_path.exists():
                    from autoskillit.fleet import (
                        DispatchRecord,
                        DispatchStatus,
                        append_dispatch_record,
                    )

                    status = DispatchStatus(envelope["dispatch_status"])
                    append_dispatch_record(
                        campaign_state_path,
                        DispatchRecord(
                            name=dispatch_name,
                            status=status,
                            dispatch_id=envelope.get("dispatch_id", ""),
                            l3_session_id=envelope.get("l3_session_id", ""),
                            reason=envelope.get("reason", ""),
                            token_usage=envelope.get("token_usage") or {},
                        ),
                    )
            except Exception:
                logger.warning("campaign state update failed", exc_info=True)

        return result
    except Exception as exc:
        logger.error("dispatch_food_truck unhandled exception", exc_info=True)
        from autoskillit.core import FleetErrorCode, fleet_error

        return fleet_error(
            FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
            f"{type(exc).__name__}: {exc}",
        )


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "fleet"},
    annotations={"readOnlyHint": True},
)
@track_response_size("record_gate_dispatch")
async def record_gate_dispatch(
    dispatch_name: str,
    approved: bool,
    ctx: Context = CurrentContext(),
) -> str:
    """Record the outcome of a gate dispatch to the campaign state file.

    Gate dispatches are handled by AskUserQuestion (no L3 session). This tool
    persists the user's approval/rejection so that campaign resume can skip
    completed gates.

    Args:
        dispatch_name: Name of the gate dispatch in the campaign manifest.
        approved: True if the user approved the gate, False if rejected.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate

    try:
        from autoskillit.core import FleetErrorCode, fleet_error, is_feature_enabled
        from autoskillit.fleet import record_gate_outcome
        from autoskillit.server import _get_ctx as _get_ctx_for_feature_check

        _feature_ctx = _get_ctx_for_feature_check()
        if not is_feature_enabled(
            "fleet",
            _feature_ctx.config.features,
            experimental_enabled=_feature_ctx.config.experimental_enabled,
        ):
            return fleet_error(
                FleetErrorCode.FLEET_FEATURE_DISABLED,
                "Fleet feature is disabled. Set features.experimental_enabled: true to enable.",
            )

        campaign_state_path_str = os.environ.get("AUTOSKILLIT_CAMPAIGN_STATE_PATH")
        if not campaign_state_path_str:
            return fleet_error(
                FleetErrorCode.FLEET_GATE_NO_CAMPAIGN,
                "No AUTOSKILLIT_CAMPAIGN_STATE_PATH set — not running in campaign mode.",
            )

        result = record_gate_outcome(Path(campaign_state_path_str), dispatch_name, approved)
        if not result.success:
            return fleet_error(FleetErrorCode(result.error_code), result.error_message)

        return json.dumps(
            {"success": True, "dispatch_name": result.dispatch_name, "status": result.status}
        )
    except Exception as exc:
        logger.error("record_gate_dispatch unhandled exception", exc_info=True)
        from autoskillit.core import FleetErrorCode, fleet_error

        return fleet_error(
            FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
            f"{type(exc).__name__}: {exc}",
        )
