"""MCP tool handlers: merge_worktree, classify_fix."""

from __future__ import annotations

import json
import time

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import RestartScope, get_logger
from autoskillit.server import mcp
from autoskillit.server.helpers import _notify, _require_enabled, _run_subprocess

logger = get_logger(__name__)


@mcp.tool(tags={"automation", "kitchen"})
async def merge_worktree(
    worktree_path: str,
    base_branch: str,
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Merge a worktree branch into the base branch after verifying tests pass.

    Programmatic gate: runs the configured test command in the worktree before allowing merge.
    If tests fail, returns error without merging.
    On failure, consider using /autoskillit:resolve-failures via run_skill
    for automated diagnosis and remediation.

    Args:
        worktree_path: Absolute path to the git worktree.
        base_branch: Branch to merge into (e.g. "main").
        step_name: Optional YAML step key for wall-clock timing accumulation.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="merge_worktree", cwd=worktree_path)
    logger.info("merge_worktree", path=worktree_path, base=base_branch)
    await _notify(
        ctx,
        "info",
        f"merge_worktree: {worktree_path} -> {base_branch}",
        "autoskillit.merge_worktree",
        extra={"worktree": worktree_path, "base": base_branch},
    )

    from autoskillit.server import _get_config, _get_ctx
    from autoskillit.server.git import perform_merge

    tool_ctx = _get_ctx()
    runner = tool_ctx.runner
    assert runner is not None, "No subprocess runner configured"
    _start = time.monotonic()
    try:
        result = await perform_merge(
            worktree_path,
            base_branch,
            config=_get_config(),
            runner=runner,
            tester=tool_ctx.tester,
        )

        if "error" in result:
            await _notify(
                ctx,
                "error",
                "merge_worktree failed",
                "autoskillit.merge_worktree",
                extra={"reason": result["error"]},
            )

        return json.dumps(result)
    finally:
        if step_name:
            tool_ctx.timing_log.record(step_name, time.monotonic() - _start)


@mcp.tool(tags={"automation", "kitchen"})
async def classify_fix(
    worktree_path: str,
    base_branch: str,
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Analyze a worktree's changes to determine if the fix requires restarting
    from plan creation or just re-running the implementation.

    Inspects git diff between the worktree HEAD and the base branch merge-base.
    If any changed files are in critical paths, returns full_restart.
    Otherwise returns partial_restart.

    Routing guidance:
    - full_restart: The fix touches critical paths. Re-run investigation and
      plan creation (e.g. call /autoskillit:investigate via run_skill).
    - partial_restart: The fix is localized. Re-run implementation only
      (e.g. call /autoskillit:implement-worktree-no-merge via run_skill).

    Args:
        worktree_path: Path to the git worktree with the implemented fix.
        base_branch: The branch the worktree was created from (for merge-base).
        step_name: Optional YAML step key for wall-clock timing accumulation.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="classify_fix", cwd=worktree_path)
    logger.info("classify_fix", worktree=worktree_path, base=base_branch)
    await _notify(
        ctx,
        "info",
        f"classify_fix: {worktree_path}",
        "autoskillit.classify_fix",
        extra={"worktree": worktree_path, "base": base_branch},
    )

    from autoskillit.server import _get_config, _get_ctx
    from autoskillit.server.git import _filter_changed_files

    _timing_ctx = _get_ctx()
    _start = time.monotonic()
    try:
        returncode, stdout, stderr = await _run_subprocess(
            ["git", "diff", "--name-only", f"origin/{base_branch}...HEAD"],
            cwd=worktree_path,
            timeout=30,
        )

        if returncode != 0:
            await _notify(
                ctx,
                "error",
                "classify_fix: git diff failed (falling back to full_restart)",
                "autoskillit.classify_fix",
                extra={"worktree": worktree_path},
            )
            # A missing origin/<base_branch> ref (rc=128, "ambiguous argument" or
            # "unknown revision") is treated as FULL_RESTART — conservative safe default.
            # Any other git error also falls back to FULL_RESTART for the same reason:
            # if we can't determine what changed, assume the worst.
            return json.dumps(
                {
                    "restart_scope": RestartScope.FULL_RESTART,
                    "reason": (
                        f"Cannot diff against origin/{base_branch} — ref may not exist locally. "
                        f"git error: {stderr.strip()[:200]}"
                    ),
                    "critical_files": [],
                    "all_changed_files": [],
                }
            )

        prefixes = _get_config().classify_fix.path_prefixes
        changed_files, critical_files = _filter_changed_files(stdout, prefixes)

        if critical_files:
            return json.dumps(
                {
                    "restart_scope": RestartScope.FULL_RESTART,
                    "reason": f"Fix touches critical paths: {', '.join(critical_files[:5])}",
                    "critical_files": critical_files,
                    "all_changed_files": changed_files,
                }
            )

        return json.dumps(
            {
                "restart_scope": RestartScope.PARTIAL_RESTART,
                "reason": "Fix does not touch critical paths — partial restart is sufficient",
                "critical_files": [],
                "all_changed_files": changed_files,
            }
        )
    finally:
        if step_name:
            _timing_ctx.timing_log.record(step_name, time.monotonic() - _start)
