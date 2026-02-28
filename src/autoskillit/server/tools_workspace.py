"""MCP tool handlers: test_check, reset_test_dir, reset_workspace."""

from __future__ import annotations

import json
import os
from pathlib import Path

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import get_logger, truncate_text
from autoskillit.server import mcp
from autoskillit.server.helpers import _require_enabled, _run_subprocess

logger = get_logger(__name__)


@mcp.tool(tags={"automation"})
async def test_check(worktree_path: str, ctx: Context = CurrentContext()) -> str:
    """Run the configured test command in a worktree directory. Returns unambiguous PASS/FAIL.

    CRITICAL: This tool is a pipeline gate, not a diagnostic tool. When it
    returns {"passed": false}, follow the pipeline script's on_failure routing
    (e.g. call assess-and-merge via run_skill). Do NOT:
    - Run tests yourself (pytest, make test, etc.) to investigate
    - Read test output or try to diagnose failures
    - Attempt to fix code directly
    The on_failure step handles all diagnosis and remediation.

    Args:
        worktree_path: Path to the git worktree to run tests in.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="test_check", cwd=worktree_path)
    logger.info("test_check", worktree=worktree_path)
    try:
        await ctx.info(
            f"test_check: {worktree_path}",
            logger_name="autoskillit.test_check",
            extra={"worktree": worktree_path},
        )
    except (RuntimeError, AttributeError):
        pass

    from autoskillit.server import _get_ctx

    tool_ctx = _get_ctx()
    if tool_ctx.tester is None:
        return json.dumps({"passed": False, "error": "Test runner not configured"})

    passed, output = await tool_ctx.tester.run(Path(worktree_path))

    if not passed:
        try:
            await ctx.error(
                "test_check: tests failed",
                logger_name="autoskillit.test_check",
                extra={"worktree": worktree_path},
            )
        except (RuntimeError, AttributeError):
            pass

    return json.dumps({"passed": passed, "output": truncate_text(output)})


@mcp.tool(tags={"automation"})
async def reset_test_dir(
    test_dir: str, force: bool = False, ctx: Context = CurrentContext()
) -> str:
    """Remove all files from a test directory. Only works on directories with a reset guard marker.

    The directory must contain the configured marker file (default: .autoskillit-workspace)
    unless force=True is set. Use ``autoskillit workspace init <dir>`` to create the marker.

    Args:
        test_dir: Path to the test directory to clear. Must contain the reset guard marker.
        force: Override the marker check. When True, all contents are deleted
               including the marker file itself.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    resolved = os.path.realpath(test_dir)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="reset_test_dir", cwd=resolved)
    logger.info("reset_test_dir", resolved=str(resolved), force=force)
    try:
        await ctx.info(
            f"reset_test_dir: {resolved}",
            logger_name="autoskillit.reset_test_dir",
            extra={"resolved": resolved, "force": force},
        )
    except (RuntimeError, AttributeError):
        pass

    if not os.path.isdir(resolved):
        try:
            await ctx.error(
                "reset_test_dir failed",
                logger_name="autoskillit.reset_test_dir",
                extra={"reason": "directory does not exist"},
            )
        except (RuntimeError, AttributeError):
            pass
        return json.dumps({"error": f"Directory does not exist: {resolved}"})

    from autoskillit.server import _get_config

    marker_name = _get_config().safety.reset_guard_marker
    marker_path = Path(resolved) / marker_name
    if not force and not marker_path.is_file():
        try:
            await ctx.error(
                "reset_test_dir failed",
                logger_name="autoskillit.reset_test_dir",
                extra={"reason": "marker missing"},
            )
        except (RuntimeError, AttributeError):
            pass
        return json.dumps(
            {
                "error": f"Safety: directory missing reset guard marker ({marker_name})",
                "hint": f"Create the marker with: autoskillit workspace init {resolved}",
            }
        )

    from autoskillit.server import _get_ctx

    tool_ctx = _get_ctx()
    if tool_ctx.workspace_mgr is None:
        return json.dumps({"error": "Workspace manager not configured"})

    preserve = None if force else {marker_name}
    cleanup = tool_ctx.workspace_mgr.delete_contents(Path(resolved), preserve=preserve)
    return json.dumps({**cleanup.to_dict(), "forced": force})


@mcp.tool(tags={"automation"})
async def reset_workspace(test_dir: str, ctx: Context = CurrentContext()) -> str:
    """Runs a configured reset command then deletes directory contents,
    preserving configured directories and the reset guard marker.

    Args:
        test_dir: Path to the test project directory. Must contain the reset guard marker.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    resolved = os.path.realpath(test_dir)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="reset_workspace", cwd=resolved)
    logger.info("reset_workspace", resolved=str(resolved))
    try:
        await ctx.info(
            f"reset_workspace: {resolved}",
            logger_name="autoskillit.reset_workspace",
            extra={"resolved": resolved},
        )
    except (RuntimeError, AttributeError):
        pass

    if not os.path.isdir(resolved):
        try:
            await ctx.error(
                "reset_workspace failed",
                logger_name="autoskillit.reset_workspace",
                extra={"reason": "directory does not exist"},
            )
        except (RuntimeError, AttributeError):
            pass
        return json.dumps({"error": f"Directory does not exist: {resolved}"})

    from autoskillit.server import _get_config

    marker_name = _get_config().safety.reset_guard_marker
    marker_path = Path(resolved) / marker_name
    if not marker_path.is_file():
        try:
            await ctx.error(
                "reset_workspace failed",
                logger_name="autoskillit.reset_workspace",
                extra={"reason": "marker missing"},
            )
        except (RuntimeError, AttributeError):
            pass
        return json.dumps(
            {
                "error": f"Safety: directory missing reset guard marker ({marker_name})",
                "hint": f"Create the marker with: autoskillit workspace init {resolved}",
            }
        )

    reset_cmd = _get_config().reset_workspace.command
    if reset_cmd is None:
        try:
            await ctx.error(
                "reset_workspace failed",
                logger_name="autoskillit.reset_workspace",
                extra={"reason": "not configured"},
            )
        except (RuntimeError, AttributeError):
            pass
        return json.dumps({"error": "reset_workspace not configured for this project"})

    returncode, stdout, stderr = await _run_subprocess(
        reset_cmd,
        cwd=resolved,
        timeout=60,
    )

    if returncode != 0:
        try:
            await ctx.error(
                "reset_workspace failed",
                logger_name="autoskillit.reset_workspace",
                extra={"reason": "reset command failed", "exit_code": returncode},
            )
        except (RuntimeError, AttributeError):
            pass
        return json.dumps(
            {
                "error": "reset command failed",
                "exit_code": returncode,
                "stderr": truncate_text(stderr),
            }
        )

    from autoskillit.server import _get_ctx

    tool_ctx = _get_ctx()
    if tool_ctx.workspace_mgr is None:
        return json.dumps({"error": "Workspace manager not configured"})

    preserve = set(_get_config().reset_workspace.preserve_dirs) | {marker_name}
    cleanup = tool_ctx.workspace_mgr.delete_contents(Path(resolved), preserve=preserve)
    return json.dumps(cleanup.to_dict())
