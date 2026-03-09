"""MCP tool handlers: wait_for_ci (gated), get_ci_status (ungated)."""

from __future__ import annotations

import asyncio
import json

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import get_logger
from autoskillit.server import mcp
from autoskillit.server.helpers import _notify, _require_enabled

logger = get_logger(__name__)


@mcp.tool(tags={"automation", "kitchen"})
async def wait_for_ci(
    branch: str,
    repo: str | None = None,
    head_sha: str | None = None,
    timeout_seconds: int = 300,
    cwd: str = ".",
    ctx: Context = CurrentContext(),
) -> str:
    """Wait for a GitHub Actions CI run to complete on the given branch.

    Uses a three-phase algorithm (look-back, poll, wait) that eliminates
    the race condition where CI completes before polling starts.

    Args:
        branch: Git branch name to watch CI for.
        repo: GitHub owner/repo (e.g. "owner/repo"). If omitted, inferred
              from git remote in cwd.
        head_sha: Specific commit SHA to match. If omitted, inferred from
                  HEAD in cwd.
        timeout_seconds: Maximum time to wait (default 300s).
        cwd: Working directory for git operations.

    Returns:
        JSON with run_id, conclusion ("success", "failure", "cancelled",
        "action_required", "timed_out", "no_runs", "error", "unknown"),
        and failed_jobs list. Billing limit errors surface as
        conclusion="action_required" with failed_jobs=[].
    """
    if (gate := _require_enabled()) is not None:
        return gate
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="wait_for_ci")
    logger.info("wait_for_ci", branch=branch, repo=repo or "(infer)")

    from autoskillit.server import _get_ctx

    tool_ctx = _get_ctx()
    if tool_ctx.ci_watcher is None:
        return json.dumps(
            {
                "run_id": None,
                "conclusion": "error",
                "failed_jobs": [],
                "error": "CI watcher not configured",
            }
        )

    # Infer head_sha from cwd if not provided
    if head_sha is None and cwd:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "rev-parse",
                "HEAD",
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                head_sha = stdout.decode().strip()
        except OSError:
            pass

    await _notify(
        ctx,
        "info",
        f"Watching CI for branch {branch}",
        "autoskillit.wait_for_ci",
        extra={"repo": repo or "(infer)", "head_sha": head_sha or "(any)"},
    )

    result = await tool_ctx.ci_watcher.wait(
        branch,
        repo=repo,
        head_sha=head_sha,
        timeout_seconds=timeout_seconds,
        cwd=cwd,
    )

    conclusion = result.get("conclusion", "unknown")
    level = "info" if conclusion == "success" else "error"
    await _notify(
        ctx,
        level,
        f"CI result: {conclusion}",
        "autoskillit.wait_for_ci",
        extra={"run_id": result.get("run_id")},
    )

    return json.dumps(result)


@mcp.tool(tags={"automation"})
async def get_ci_status(
    branch: str | None = None,
    run_id: int | None = None,
    repo: str | None = None,
    cwd: str = ".",
) -> str:
    """Return current CI status for a branch or specific run without waiting.

    This tool is always available (not gated by open_kitchen).
    This tool sends no MCP progress notifications by design (ungated tools are
    notification-free — see CLAUDE.md).

    Args:
        branch: Git branch name. Required if run_id is not provided.
        run_id: Specific run ID to check. If provided, branch is ignored.
        repo: GitHub owner/repo. If omitted, inferred from git remote in cwd.
        cwd: Working directory for git operations.

    Returns:
        JSON with runs list, each containing id, status, conclusion, failed_jobs.
    """
    from autoskillit.server import _get_ctx

    tool_ctx = _get_ctx()
    if tool_ctx.ci_watcher is None:
        return json.dumps({"runs": [], "error": "CI watcher not configured"})

    if branch is None and run_id is None:
        return json.dumps({"runs": [], "error": "Provide branch or run_id"})

    result = await tool_ctx.ci_watcher.status(
        branch or "",
        repo=repo,
        run_id=run_id,
        cwd=cwd,
    )
    return json.dumps(result)
