"""MCP tool handlers: clone_repo, remove_clone, push_to_remote."""

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


@mcp.tool(tags={"automation"})
async def clone_repo(
    source_dir: str,
    run_name: str,
    branch: str = "",
    strategy: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Clone a source repository for isolated pipeline execution.

    Clones source_dir into ../autoskillit-runs/<run_name>-<timestamp>/.
    When source_dir is empty, auto-detects the git root via git rev-parse.

    Returns {"clone_path": str, "source_dir": str, "remote_url": str} on success,
    or {"error": str} on failure.

    When uncommitted changes are detected and strategy is "" (default), returns
    {"uncommitted_changes": "true", "changed_files": str, ...} without cloning.

    Args:
        source_dir: Path to the source repository. Empty string auto-detects via git.
        run_name: Name prefix for the clone directory (e.g. "impl", "audit-fix").
        branch: Branch to check out in the clone. Empty = auto-detect from HEAD.
        strategy: On uncommitted changes: "" = return warning (default),
                  "proceed" = clone remote committed state only,
                  "clone_local" = copytree (includes working-tree changes).

    SOURCE ISOLATION: Once this tool returns, source_dir must not be touched for
    any purpose except reading its remote URL in push_to_remote. Never run git
    operations (including checkout, fetch, reset, pull), run_cmd, or any other
    command in source_dir. All pipeline work — skill invocations, git operations,
    file reads — runs exclusively in clone_path (captured as work_dir in recipes).
    """
    if (gate := _require_enabled()) is not None:
        return gate
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="clone_repo", source_dir=source_dir)
    logger.info("clone_repo", source_dir=source_dir, run_name=run_name, branch=branch)
    await _notify(
        ctx,
        "info",
        f"clone_repo: {source_dir!r} run_name={run_name!r} branch={branch!r}",
        "autoskillit.clone_repo",
        extra={
            "source_dir": source_dir,
            "run_name": run_name,
            "branch": branch,
            "strategy": strategy,
        },
    )

    from autoskillit.server import _get_ctx

    tool_ctx = _get_ctx()
    if tool_ctx.clone_mgr is None:
        return json.dumps({"error": "Clone manager not configured"})

    try:
        result = await asyncio.to_thread(
            tool_ctx.clone_mgr.clone_repo, source_dir, run_name, branch, strategy
        )
    except (ValueError, RuntimeError) as exc:
        await _notify(
            ctx,
            "error",
            "clone_repo failed",
            "autoskillit.clone_repo",
            extra={"reason": str(exc)},
        )
        return json.dumps({"error": str(exc)})

    return json.dumps(result)


@mcp.tool(tags={"automation"})
async def remove_clone(
    clone_path: str, keep: str = "false", ctx: Context = CurrentContext()
) -> str:
    """Remove a pipeline clone directory.

    Best-effort teardown — never raises. When keep="true", the directory
    is preserved for debugging. Routes to on_success regardless of whether
    removal succeeded, because a cleanup failure should not mask the
    pipeline outcome.

    Returns {"removed": "true"} or {"removed": "false", "reason": str}.

    Args:
        clone_path: Absolute path to the clone directory to remove.
        keep: Pass "true" to preserve the directory (debugging). Default "false".
    """
    if (gate := _require_enabled()) is not None:
        return gate
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="remove_clone", clone_path=clone_path)
    logger.info("remove_clone", clone_path=clone_path, keep=keep)
    await _notify(
        ctx,
        "info",
        f"remove_clone: {clone_path!r} keep={keep}",
        "autoskillit.remove_clone",
        extra={"clone_path": clone_path, "keep": keep},
    )

    from autoskillit.server import _get_ctx

    tool_ctx = _get_ctx()
    if tool_ctx.clone_mgr is None:
        return json.dumps({"removed": "false", "reason": "Clone manager not configured"})

    result = await asyncio.to_thread(tool_ctx.clone_mgr.remove_clone, clone_path, keep)
    return json.dumps(result)


@mcp.tool(tags={"automation"})
async def push_to_remote(
    clone_path: str,
    branch: str,
    source_dir: str = "",
    remote_url: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Push the merged branch from a pipeline clone back to the upstream remote.

    When remote_url is provided, it is used directly and source_dir is not
    accessed for URL lookup. This is the preferred calling convention — capture
    remote_url from the clone step and pass it explicitly here.

    When remote_url is empty, falls back to reading the upstream URL from
    source_dir via git remote get-url (read-only). source_dir is never modified
    — clone isolation is preserved.

    Returns {"success": "true", "stderr": str} on success.
    Returns {"error": str, "stderr": str, "error_type": str} on failure
    (triggers on_failure routing).

    Args:
        clone_path: Absolute path to the clone directory to push from.
        branch: Branch name to push (e.g. "main").
        source_dir: Source repo path (read-only URL lookup when remote_url is empty).
        remote_url: Pre-resolved upstream remote URL. When provided, source_dir is skipped.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="push_to_remote", clone_path=clone_path)
    logger.info(
        "push_to_remote",
        clone_path=clone_path,
        source_dir=source_dir,
        remote_url=remote_url,
        branch=branch,
    )
    await _notify(
        ctx,
        "info",
        f"push_to_remote: {clone_path!r} → branch={branch!r}",
        "autoskillit.push_to_remote",
        extra={
            "clone_path": clone_path,
            "source_dir": source_dir,
            "remote_url": remote_url,
            "branch": branch,
        },
    )

    from autoskillit.server import _get_ctx

    tool_ctx = _get_ctx()
    clone_mgr = tool_ctx.clone_mgr
    if clone_mgr is None:
        return json.dumps({"error": "Clone manager not configured", "stderr": ""})

    result = await asyncio.to_thread(
        lambda: clone_mgr.push_to_remote(clone_path, source_dir, branch, remote_url=remote_url)
    )

    if not result.get("success"):
        await _notify(
            ctx,
            "error",
            "push_to_remote failed",
            "autoskillit.push_to_remote",
            extra={"stderr": result.get("stderr", ""), "error_type": result.get("error_type", "")},
        )
        return json.dumps(
            {
                "error": "push failed",
                "stderr": result.get("stderr", ""),
                "error_type": result.get("error_type", ""),
            }
        )

    return json.dumps(result)
