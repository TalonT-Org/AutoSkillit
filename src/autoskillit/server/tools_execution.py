"""MCP tool handlers: run_cmd, run_python, run_skill."""

from __future__ import annotations

import json
import os
import time
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
from autoskillit.server.helpers import (
    SCENARIO_STEP_NAME_ENV,
    _check_dry_walkthrough,
    _import_and_call,
    _notify,
    _require_enabled,
    _require_not_headless,
    _run_subprocess,
    _validate_skill_command,
    track_response_size,
)

logger = get_logger(__name__)


def _is_absolute_path(path: str) -> bool:
    """Return True if path is an absolute filesystem path."""
    return Path(path).is_absolute()


@mcp.tool(tags={"autoskillit", "kitchen"}, annotations={"readOnlyHint": True})
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
    if (headless := _require_not_headless("run_cmd")) is not None:
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


@mcp.tool(tags={"autoskillit", "kitchen"}, annotations={"readOnlyHint": True})
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
    if (headless := _require_not_headless("run_python")) is not None:
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


@mcp.tool(tags={"autoskillit", "kitchen"}, annotations={"readOnlyHint": True})
@track_response_size("run_skill")
async def run_skill(
    skill_command: str,
    cwd: str,
    model: str = "",
    step_name: str = "",
    order_id: str = "",
    stale_threshold: int | None = None,
    idle_output_timeout: int | None = None,
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
    if (headless := _require_not_headless("run_skill")) is not None:
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

        if _get_config().safety.require_dry_walkthrough:
            if (gate_error := _check_dry_walkthrough(skill_command, cwd)) is not None:
                return gate_error

        tool_ctx = _get_ctx()
        if tool_ctx.executor is None:
            return json.dumps({"success": False, "error": "Executor not configured"})

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

        invocation_marker = f"%%ORDER_UP::{uuid4().hex[:8]}%%"

        skill_add_dirs: list[ValidatedAddDir] = []
        if tool_ctx.session_skill_manager is not None:
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
                allow_only=allow_only,
            )
            skill_add_dirs.append(session_root)

            # Activate the target skill and its declared dependencies
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
                order_id=order_id,
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
            if order_id:
                skill_result.order_id = order_id
            from autoskillit.server.helpers import _refresh_quota_cache  # noqa: PLC0415

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
                order_id=order_id,
            ).to_json()
        finally:
            if step_name:
                tool_ctx.timing_log.record(step_name, time.monotonic() - _start, order_id=order_id)
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
