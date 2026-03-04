"""MCP tool handlers: run_cmd, run_python, run_skill, run_skill_retry."""

from __future__ import annotations

import json

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import PIPELINE_FORBIDDEN_TOOLS, get_logger, truncate_text
from autoskillit.server import mcp
from autoskillit.server.helpers import (
    _check_dry_walkthrough,
    _import_and_call,
    _notify,
    _require_enabled,
    _run_subprocess,
)

logger = get_logger(__name__)


@mcp.tool(tags={"automation"})
async def run_cmd(cmd: str, cwd: str, timeout: int = 600, ctx: Context = CurrentContext()) -> str:
    """Run an arbitrary shell command in the specified directory.

    Args:
        cmd: The full command to run (e.g. "make build").
        cwd: Working directory for the command.
        timeout: Max seconds before killing the process (default 600).
    """
    if (gate := _require_enabled()) is not None:
        return gate
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="run_cmd", cwd=cwd)
    logger.info("run_cmd", cmd=cmd[:80], cwd=cwd)
    await _notify(ctx, "info", f"run_cmd: {cmd[:80]}", "autoskillit.run_cmd", extra={"cwd": cwd})
    returncode, stdout, stderr = await _run_subprocess(
        ["bash", "-c", cmd],
        cwd=cwd,
        timeout=float(timeout),
    )
    result = {
        "success": returncode == 0,
        "exit_code": returncode,
        "stdout": truncate_text(stdout),
        "stderr": truncate_text(stderr),
    }
    if not result["success"]:
        await _notify(
            ctx, "error", "run_cmd failed", "autoskillit.run_cmd", extra={"exit_code": returncode}
        )
    return json.dumps(result)


@mcp.tool(tags={"automation"})
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
    """
    if (gate := _require_enabled()) is not None:
        return gate
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


@mcp.tool(tags={"automation"})
async def run_skill(
    skill_command: str,
    cwd: str,
    add_dir: str = "",
    model: str = "",
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Run a Claude Code headless session with a skill command.

    Returns JSON with: success, result, session_id, subtype, is_error, exit_code,
    needs_retry, retry_reason. When needs_retry is true, retry_reason is
    "resume" — the session should be retried to continue from where it left off.

    This is the correct MCP tool to delegate work to a headless session during
    pipeline execution. NEVER use native tools (Read, Grep, Glob, Edit, Write,
    Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator.
    All code changes, investigation, and research happen through the headless
    session launched by this tool.

    Args:
        skill_command: The full prompt including skill invocation (e.g. "/investigate ...").
        cwd: Working directory for the claude session.
        add_dir: Optional additional directory to add to the session context.
        model: Model to use (e.g. "sonnet", "opus"). Empty string = use config default.
        step_name: Optional YAML step key (e.g. "implement"). When set, token usage is
            accumulated in the server-side token log, grouped by this name.
    """
    if (gate := _require_enabled()) is not None:
        return gate
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
    skill_result = await tool_ctx.executor.run(
        skill_command, cwd, model=model, add_dir=add_dir, step_name=step_name
    )
    if not skill_result.success:
        await _notify(
            ctx,
            "error",
            "run_skill failed",
            "autoskillit.run_skill",
            extra={"exit_code": skill_result.exit_code, "subtype": skill_result.subtype},
        )
    return skill_result.to_json()


@mcp.tool(tags={"automation"})
async def run_skill_retry(
    skill_command: str,
    cwd: str,
    add_dir: str = "",
    model: str = "",
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Run a Claude Code headless session with retry detection.

    Use this for long-running skill sessions that may hit the context limit.
    Returns JSON with: success, result, session_id, subtype, is_error, exit_code,
    needs_retry, retry_reason. The needs_retry field indicates whether the session
    didn't finish. When needs_retry is true, retry_reason is "resume" — the session
    should be retried to continue from where it left off.

    IMPORTANT: When needs_retry is true, the result field contains an actionable
    summary, not the raw CLI error. Do NOT interpret the result text as indicating
    the input was too large — it means the session's context window filled during
    execution. The correct action is always to resume the session.

    This is the correct MCP tool for long-running delegated work during pipeline
    execution. NEVER use native tools (Read, Grep, Glob, Edit, Write, Bash, Agent,
    WebFetch, WebSearch, NotebookEdit) from the orchestrator. All code
    changes, investigation, and research happen through the headless session
    launched by this tool.

    Args:
        skill_command: The full prompt including skill invocation.
        cwd: Working directory for the claude session.
        add_dir: Optional additional directory to add to the session context.
        model: Model to use (e.g. "sonnet", "opus"). Empty string = use config default.
        step_name: Optional YAML step key (e.g. "implement"). When set, token usage is
            accumulated in the server-side token log, grouped by this name.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="run_skill_retry", cwd=cwd)
    logger.info("run_skill_retry", command=skill_command[:80], cwd=cwd)
    await _notify(
        ctx,
        "info",
        f"run_skill_retry: {skill_command[:80]}",
        "autoskillit.run_skill_retry",
        extra={"cwd": cwd, "model": model or "default"},
    )

    from autoskillit.server import _get_config, _get_ctx

    if _get_config().safety.require_dry_walkthrough:
        if (gate_error := _check_dry_walkthrough(skill_command, cwd)) is not None:
            return gate_error

    tool_ctx = _get_ctx()
    if tool_ctx.executor is None:
        return json.dumps({"success": False, "error": "Executor not configured"})
    cfg = _get_config().run_skill_retry
    skill_result = await tool_ctx.executor.run(
        skill_command,
        cwd,
        model=model,
        add_dir=add_dir,
        step_name=step_name,
        timeout=cfg.timeout,
        stale_threshold=cfg.stale_threshold,
    )
    if not skill_result.success:
        await _notify(
            ctx,
            "error",
            "run_skill_retry failed",
            "autoskillit.run_skill_retry",
            extra={"exit_code": skill_result.exit_code, "subtype": skill_result.subtype},
        )
    return skill_result.to_json()


__all__ = [
    "PIPELINE_FORBIDDEN_TOOLS",
    "run_cmd",
    "run_python",
    "run_skill",
    "run_skill_retry",
]
