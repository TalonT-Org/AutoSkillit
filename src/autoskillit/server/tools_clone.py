"""MCP tool handlers: clone_repo, remove_clone, push_to_remote,
register_clone_status, batch_cleanup_clones."""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Literal

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import CAMPAIGN_ID_ENV_VAR, get_logger
from autoskillit.server import mcp
from autoskillit.server.helpers import (
    _notify,
    _require_enabled,
    clone_registry,
    track_response_size,
)

logger = get_logger(__name__)


@mcp.tool(tags={"autoskillit", "kitchen", "clone"}, annotations={"readOnlyHint": True})
@track_response_size("clone_repo")
async def clone_repo(
    source_dir: str,
    run_name: str,
    branch: str = "",
    strategy: str = "",
    remote_url: str = "",
    step_name: str = "",
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
        remote_url: Override remote URL for clone's origin. When provided, applied
                    via 'git remote set-url origin' after cloning, absorbing the need
                    for a separate git remote set-url step. Empty string (default)
                    uses the source repo's detected origin.
        step_name: Optional YAML step key for wall-clock timing accumulation.

    SOURCE ISOLATION: Once this tool returns, source_dir must not be touched for
    any purpose except reading its remote URL in push_to_remote. Never run git
    operations (including checkout, fetch, reset, pull), run_cmd, or any other
    command in source_dir. All pipeline work — skill invocations, git operations,
    file reads — runs exclusively in clone_path (captured as work_dir in recipes).

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
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
                "remote_url": remote_url,
            },
        )

        from autoskillit.server import _get_ctx

        tool_ctx = _get_ctx()
        if tool_ctx.clone_mgr is None:
            return json.dumps({"error": "Clone manager not configured"})

        _start = time.monotonic()
        try:
            result = await asyncio.to_thread(
                tool_ctx.clone_mgr.clone_repo, source_dir, run_name, branch, strategy, remote_url
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
        finally:
            if step_name:
                tool_ctx.timing_log.record(step_name, time.monotonic() - _start)

        return json.dumps(result)
    except Exception as exc:
        logger.error("clone_repo unhandled exception", exc_info=True)
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(tags={"autoskillit", "kitchen", "clone"}, annotations={"readOnlyHint": True})
@track_response_size("remove_clone")
async def remove_clone(
    clone_path: str,
    keep: str = "false",
    step_name: str = "",
    ctx: Context = CurrentContext(),
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
        step_name: Optional YAML step key for wall-clock timing accumulation.
    """
    try:
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

        _start = time.monotonic()
        try:
            result = await asyncio.to_thread(tool_ctx.clone_mgr.remove_clone, clone_path, keep)
            return json.dumps(result)
        finally:
            if step_name:
                tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
    except Exception as exc:
        logger.error("remove_clone unhandled exception", exc_info=True)
        return json.dumps({"removed": "false", "reason": f"unexpected error: {exc}"})


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@track_response_size("push_to_remote")
async def push_to_remote(
    clone_path: str,
    branch: str,
    source_dir: str = "",
    remote_url: str = "",
    force: str = "",
    step_name: str = "",
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
        branch: Branch name to push (e.g. "integration").
        source_dir: Source repo path (read-only URL lookup when remote_url is empty).
        remote_url: Pre-resolved upstream remote URL. When provided, source_dir is skipped.
        step_name: Optional YAML step key for wall-clock timing accumulation.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
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

        _start = time.monotonic()
        try:
            result = await asyncio.to_thread(
                lambda: clone_mgr.push_to_remote(
                    clone_path,
                    source_dir,
                    branch,
                    remote_url=remote_url,
                    protected_branches=tool_ctx.config.safety.protected_branches,
                    force=force.strip().lower() == "true",
                )
            )

            if not result.get("success"):
                await _notify(
                    ctx,
                    "error",
                    "push_to_remote failed",
                    "autoskillit.push_to_remote",
                    extra={
                        "stderr": result.get("stderr", ""),
                        "error_type": result.get("error_type", ""),
                    },
                )
                return json.dumps(
                    {
                        "success": False,
                        "error": "push failed",
                        "stderr": result.get("stderr", ""),
                        "error_type": result.get("error_type", ""),
                    }
                )

            return json.dumps(result)
        finally:
            if step_name:
                tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
    except Exception as exc:
        logger.error("push_to_remote unhandled exception", exc_info=True)
        return json.dumps(
            {"success": False, "error": f"{type(exc).__name__}: {exc}", "stderr": ""}
        )


@mcp.tool(tags={"autoskillit", "kitchen", "clone"}, annotations={"readOnlyHint": True})
@track_response_size("register_clone_status")
async def register_clone_status(
    clone_path: str,
    status: str,
    registry_path: str = "",
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Register a clone path in the shared cleanup registry.

    Called per-pipeline when defer_cleanup=true. Safe for parallel callers —
    clone_registry uses atomic writes.

    Returns {"registered": "true", "registry_path": str} on success,
    or {"registered": "false", "reason": str} on error.

    Args:
        clone_path: Absolute path to the clone directory to register.
        status: Completion status — "success" (safe to delete), "error" (preserve for
                investigation), or "unconfirmed" (preserve; merge was attempted but not
                yet confirmed — in-progress label is retained on the issue).
        registry_path: Absolute path to the shared registry file. Defaults to
                       .autoskillit/temp/clone-cleanup-registry.json in cwd.
        step_name: Optional YAML step key for wall-clock timing accumulation.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(tool="register_clone_status", clone_path=clone_path)
        logger.info("register_clone_status", clone_path=clone_path, status=status)

        if status not in ("success", "error", "unconfirmed"):
            return json.dumps(
                {
                    "registered": "false",
                    "reason": (
                        f"Invalid status '{status}'. Must be 'success', 'error', or 'unconfirmed'."
                    ),
                }
            )

        from autoskillit.server import _get_ctx

        tool_ctx = _get_ctx()
        owner = os.environ.get(CAMPAIGN_ID_ENV_VAR, "") or tool_ctx.kitchen_id
        if owner == "":
            return json.dumps(
                {
                    "registered": "false",
                    "reason": "no active kitchen (kitchen_id empty) — cannot register clone",
                }
            )
        _start = time.monotonic()
        typed_status: Literal["success", "error", "unconfirmed"] = (
            "success"
            if status == "success"
            else "unconfirmed"
            if status == "unconfirmed"
            else "error"
        )
        try:
            result = await asyncio.to_thread(
                clone_registry.register_clone,
                clone_path,
                typed_status,
                owner,
                registry_path,
                tool_ctx.temp_dir,
            )
            return json.dumps(result)
        except Exception as exc:
            logger.warning("register_clone_status failed", exc_info=True)
            return json.dumps({"registered": "false", "reason": str(exc)})
        finally:
            if step_name:
                tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
    except Exception as exc:
        logger.error("register_clone_status unhandled exception", exc_info=True)
        return json.dumps({"registered": "false", "reason": f"{type(exc).__name__}: {exc}"})


@mcp.tool(tags={"autoskillit", "kitchen", "clone", "fleet"}, annotations={"readOnlyHint": True})
@track_response_size("batch_cleanup_clones")
async def batch_cleanup_clones(
    registry_path: str = "",
    all_owners: str = "false",
    owner_filter: str = "",
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Delete all success-status clones from the registry. Preserve error-status clones.

    Called ONCE after all parallel pipelines have completed (after batch_confirm_cleanup
    action: confirm). Never raises — failures are reported in the result dict.

    By default, deletion is scoped to the current kitchen's entries (owner = kitchen_id).
    Pass all_owners="true" as an operator escape hatch to delete all success-status entries
    regardless of owner, including legacy orphan entries from pre-owner-schema registries.

    Returns {"deleted": [...], "delete_failures": [...], "preserved": [...]}.

    Args:
        registry_path: Absolute path to the shared registry file. Defaults to
                       .autoskillit/temp/clone-cleanup-registry.json in cwd.
        all_owners: "false" (default) scopes deletion to the current kitchen's entries;
                    "true" is the operator escape hatch that ignores owner and deletes
                    all success-status entries including legacy orphans.
        owner_filter: Explicit owner string for campaign-scoped cleanup. When non-empty,
                      overrides kitchen_id scoping. Ignored when all_owners="true".
        step_name: Optional YAML step key for wall-clock timing accumulation.
    """
    try:
        if (gate := _require_enabled()) is not None:
            return gate
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(tool="batch_cleanup_clones")
        logger.info("batch_cleanup_clones", registry_path=registry_path, all_owners=all_owners)

        from autoskillit.server import _get_ctx

        tool_ctx = _get_ctx()
        if tool_ctx.clone_mgr is None:
            return json.dumps(
                {
                    "deleted": [],
                    "delete_failures": [],
                    "preserved": [],
                    "error": "Clone manager not configured",
                }
            )

        is_escape_hatch = all_owners.strip().lower() == "true"
        owner_filter_resolved: str | None
        if is_escape_hatch:
            owner_filter_resolved = None
        elif owner_filter:
            owner_filter_resolved = owner_filter
        else:
            owner_filter_resolved = tool_ctx.kitchen_id
            if owner_filter_resolved == "":
                return json.dumps(
                    {
                        "deleted": [],
                        "delete_failures": [],
                        "preserved": [],
                        "error": "no active kitchen (kitchen_id empty) — "
                        "pass all_owners='true' or owner_filter to force-cleanup",
                    }
                )

        _start = time.monotonic()
        try:
            result = await asyncio.to_thread(
                clone_registry.batch_delete,
                registry_path,
                tool_ctx.clone_mgr.remove_clone,
                tool_ctx.temp_dir,
                owner_filter_resolved,
            )
            return json.dumps(result)
        finally:
            if step_name:
                tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
    except Exception as exc:
        logger.warning("batch_cleanup_clones failed", exc_info=True)
        return json.dumps({"deleted": [], "preserved": [], "error": str(exc)})
