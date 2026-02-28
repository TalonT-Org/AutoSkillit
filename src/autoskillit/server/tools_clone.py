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
    source_dir: str, run_name: str, ctx: Context = CurrentContext()
) -> str:
    """Clone a source repository for isolated pipeline execution.

    Clones source_dir into ../autoskillit-runs/<run_name>-<timestamp>/.
    When source_dir is empty, auto-detects the git root via git rev-parse.

    Returns {"clone_path": str, "source_dir": str} on success,
    or {"error": str} on failure.

    Args:
        source_dir: Path to the source repository. Empty string auto-detects via git.
        run_name: Name prefix for the clone directory (e.g. "impl", "audit-fix").
    """
    if (gate := _require_enabled()) is not None:
        return gate
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="clone_repo", source_dir=source_dir)
    logger.info("clone_repo", source_dir=source_dir, run_name=run_name)
    await _notify(
        ctx,
        "info",
        f"clone_repo: {source_dir!r} run_name={run_name!r}",
        "autoskillit.clone_repo",
        extra={"source_dir": source_dir, "run_name": run_name},
    )

    from autoskillit.workspace import clone as _clone

    try:
        result = await asyncio.to_thread(_clone.clone_repo, source_dir, run_name)
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

    from autoskillit.workspace import clone as _clone

    result = await asyncio.to_thread(_clone.remove_clone, clone_path, keep)
    return json.dumps(result)


@mcp.tool(tags={"automation"})
async def push_to_remote(
    clone_path: str, source_dir: str, branch: str, ctx: Context = CurrentContext()
) -> str:
    """Push the merged branch from a pipeline clone back to the upstream remote.

    Reads the remote URL from source_dir (read-only via git remote get-url)
    and pushes from clone_path. source_dir is never modified — clone isolation
    is preserved.

    Returns {"success": "true", "stderr": str} on success.
    Returns {"error": str, "stderr": str} on failure (triggers on_failure routing).

    Args:
        clone_path: Absolute path to the clone directory to push from.
        source_dir: Absolute path to the source repo (used only to read remote URL).
        branch: Branch name to push (e.g. "main").
    """
    if (gate := _require_enabled()) is not None:
        return gate
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="push_to_remote", clone_path=clone_path)
    logger.info("push_to_remote", clone_path=clone_path, source_dir=source_dir, branch=branch)
    await _notify(
        ctx,
        "info",
        f"push_to_remote: {clone_path!r} → branch={branch!r}",
        "autoskillit.push_to_remote",
        extra={"clone_path": clone_path, "source_dir": source_dir, "branch": branch},
    )

    from autoskillit.workspace import clone as _clone

    result = await asyncio.to_thread(_clone.push_to_remote, clone_path, source_dir, branch)

    if result.get("success") == "false":
        await _notify(
            ctx,
            "error",
            "push_to_remote failed",
            "autoskillit.push_to_remote",
            extra={"stderr": result.get("stderr", "")},
        )
        return json.dumps({"error": "push failed", "stderr": result.get("stderr", "")})

    return json.dumps(result)
