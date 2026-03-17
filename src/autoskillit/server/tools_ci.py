"""MCP tool handlers: wait_for_ci (gated), get_ci_status (ungated), set_commit_status (gated),
wait_for_merge_queue (gated).
"""

from __future__ import annotations

import asyncio
import json
from typing import Literal

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import CIRunScope, get_logger
from autoskillit.server import mcp
from autoskillit.server.helpers import (
    _notify,
    _require_enabled,
    _run_subprocess,
    infer_repo_from_remote,
    track_response_size,
)

logger = get_logger(__name__)


@mcp.tool(tags={"autoskillit", "kitchen", "ci"}, annotations={"readOnlyHint": True})
@track_response_size("wait_for_ci")
async def wait_for_ci(
    branch: str,
    repo: str | None = None,
    remote_url: str = "",
    head_sha: str | None = None,
    workflow: str | None = None,
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
        remote_url: Full GitHub remote URL (e.g. "https://github.com/owner/repo.git").
                    Parsed to owner/repo before inference. Takes priority over repo
                    when both are provided.
        head_sha: Specific commit SHA to match. If omitted, inferred from
                  HEAD in cwd.
        workflow: Workflow filename to filter runs (e.g. "tests.yml"). If
                  omitted, falls back to the project-level ci.workflow config.
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

    scope = CIRunScope(
        workflow=workflow or tool_ctx.default_ci_scope.workflow,
        head_sha=head_sha,
    )

    resolved_repo = await infer_repo_from_remote(cwd, hint=remote_url or repo or None)

    await _notify(
        ctx,
        "info",
        f"Watching CI for branch {branch}",
        "autoskillit.wait_for_ci",
        extra={
            "repo": resolved_repo or "(infer)",
            "head_sha": scope.head_sha or "(any)",
            "workflow": scope.workflow or "(any)",
        },
    )

    result = await tool_ctx.ci_watcher.wait(
        branch,
        repo=resolved_repo or None,
        scope=scope,
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


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@track_response_size("set_commit_status")
async def set_commit_status(
    sha: str,
    state: Literal["pending", "success", "failure", "error"],
    context: str,
    description: str = "",
    target_url: str = "",
    repo: str = "",
    cwd: str = "",
) -> dict[str, object]:
    """Post a GitHub Commit Status to a commit SHA.

    Use to implement review-first gating: post `pending` when review starts,
    then `success` or `failure` when it completes. Combine with a required
    status check in branch protection to block merge until the review resolves.

    Args:
        sha: Full commit SHA to attach the status to.
        state: One of: pending, success, failure, error.
        context: Status context label (e.g. "autoskillit/ai-review").
        description: Short human-readable status description (max 140 chars).
        target_url: Optional URL linking to review details.
        repo: owner/repo format. Inferred from `cwd` git remote if absent.
        cwd: Working directory for repo inference. Defaults to plugin_dir.
    """
    if (gate := _require_enabled()) is not None:
        return json.loads(gate)  # type: ignore[return-value]

    if not sha:
        return {"success": False, "error": "sha must not be empty"}
    if not context:
        return {"success": False, "error": "context must not be empty"}
    if len(description) > 140:
        return {
            "success": False,
            "error": f"description exceeds 140 chars ({len(description)} chars)",
        }

    from autoskillit.server import _get_ctx

    tool_ctx = _get_ctx()
    effective_cwd = cwd or tool_ctx.plugin_dir or "."

    # Resolve owner/repo if not provided
    owner_repo = repo
    if not owner_repo:
        rc, stdout, stderr = await _run_subprocess(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            cwd=effective_cwd,
            timeout=30.0,
        )
        if rc != 0:
            return {"success": False, "error": f"Could not infer owner/repo: {stderr}"}
        owner_repo = stdout.strip()

    cmd = [
        "gh",
        "api",
        "--method",
        "POST",
        f"/repos/{owner_repo}/statuses/{sha}",
        "-f",
        f"state={state}",
        "-f",
        f"context={context}",
        "-f",
        f"description={description}",
    ]
    if target_url:
        cmd += ["-f", f"target_url={target_url}"]

    rc, _stdout, stderr = await _run_subprocess(cmd, cwd=effective_cwd, timeout=30.0)
    if rc != 0:
        return {"success": False, "error": stderr}

    return {"success": True, "sha": sha, "state": state, "context": context}


@mcp.tool(tags={"autoskillit", "kitchen", "ci"}, annotations={"readOnlyHint": True})
@track_response_size("get_ci_status")
async def get_ci_status(
    branch: str | None = None,
    run_id: int | None = None,
    repo: str | None = None,
    workflow: str | None = None,
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
        workflow: Workflow filename to filter runs (e.g. "tests.yml"). If
                  omitted, falls back to the project-level ci.workflow config.
        cwd: Working directory for git operations.

    Returns:
        JSON with runs list, each containing id, status, conclusion, failed_jobs.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    from autoskillit.server import _get_ctx

    tool_ctx = _get_ctx()
    if tool_ctx.ci_watcher is None:
        return json.dumps({"runs": [], "error": "CI watcher not configured"})

    if branch is None and run_id is None:
        return json.dumps({"runs": [], "error": "Provide branch or run_id"})

    scope = CIRunScope(workflow=workflow or tool_ctx.default_ci_scope.workflow)

    result = await tool_ctx.ci_watcher.status(
        branch or "",
        repo=repo,
        run_id=run_id,
        scope=scope,
        cwd=cwd,
    )
    return json.dumps(result)


@mcp.tool(tags={"autoskillit", "kitchen", "ci"}, annotations={"readOnlyHint": False})
@track_response_size("toggle_auto_merge")
async def toggle_auto_merge(
    pr_number: int,
    target_branch: str,
    cwd: str,
    repo: str = "",
    remote_url: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Disable then re-enable auto-merge for a PR to re-enroll it in the merge queue.

    Uses the same GraphQL mutation path as wait_for_merge_queue's stall recovery.
    Call this when wait_for_merge_queue returns pr_state="stalled" and you want
    to attempt one additional re-enrollment cycle.

    Args:
        pr_number: PR number to re-enroll.
        target_branch: Branch the merge queue targets (e.g. "integration").
        cwd: Working directory for git remote resolution when repo is not provided.
        repo: Optional "owner/name" string. Inferred from git remote if empty.
        remote_url: Full GitHub remote URL. Parsed to owner/repo before inference.

    Returns:
        JSON: {"success": bool, "pr_number": int} on success,
              {"success": false, "error": str} on failure.
    """
    if (gate := _require_enabled()) is not None:
        return gate

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        tool="toggle_auto_merge", pr_number=pr_number, target_branch=target_branch
    )
    await _notify(
        ctx,
        "info",
        f"Toggling auto-merge for PR #{pr_number} on {target_branch!r}",
        "autoskillit.toggle_auto_merge",
        extra={"pr_number": pr_number, "target_branch": target_branch},
    )

    from autoskillit.server import _get_ctx

    tool_ctx = _get_ctx()

    if tool_ctx.merge_queue_watcher is None:
        return json.dumps(
            {
                "success": False,
                "error": "merge_queue_watcher not configured (missing GITHUB_TOKEN?)",
            }
        )

    resolved_repo = await infer_repo_from_remote(cwd, hint=remote_url or repo or None)

    result = await tool_ctx.merge_queue_watcher.toggle(
        pr_number=pr_number,
        target_branch=target_branch,
        repo=resolved_repo or None,
        cwd=cwd,
    )
    return json.dumps(result)


@mcp.tool(tags={"autoskillit", "kitchen", "ci"}, annotations={"readOnlyHint": False})
@track_response_size("wait_for_merge_queue")
async def wait_for_merge_queue(
    pr_number: int,
    target_branch: str,
    cwd: str,
    repo: str = "",
    remote_url: str = "",
    timeout_seconds: int = 600,
    poll_interval: int = 15,
    stall_grace_period: int = 60,
    max_stall_retries: int = 3,
    not_in_queue_confirmation_cycles: int = 2,
    ctx: Context = CurrentContext(),
) -> str:
    """Poll a PR's progress through GitHub's merge queue until merged, ejected, or timed out.

    Args:
        pr_number: PR number to monitor.
        target_branch: Branch the merge queue targets (e.g. "integration").
        cwd: Working directory for git remote resolution when repo is not provided.
        repo: Optional "owner/name" string. Inferred from git remote if empty.
        remote_url: Full GitHub remote URL (e.g. "https://github.com/owner/repo.git").
                    Parsed to owner/repo before inference. Takes priority over repo
                    when both are provided.
        timeout_seconds: Total polling budget (default 600s).
        poll_interval: Seconds between polls (default 15s).
        stall_grace_period: Seconds after auto-merge is enabled before stall recovery
                    may trigger. Prevents intervention during normal queue processing
                    (default 60s).
        max_stall_retries: Maximum disable/re-enable toggle attempts before declaring
                    the PR stalled and returning pr_state="stalled" (default 3).
        not_in_queue_confirmation_cycles: Consecutive "not in queue" cycles required
                    before treating absence as definitive. Guards against race between
                    queue exit and merged=true propagation (default 2).

    Returns:
        JSON: {
            "success": bool,
            "pr_state": "merged"|"ejected"|"stalled"|"timeout"|"error",
            "reason": str,
            "stall_retries_attempted": int,
        }
    """
    if (gate := _require_enabled()) is not None:
        return gate

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        tool="wait_for_merge_queue", pr_number=pr_number, target_branch=target_branch
    )
    await _notify(
        ctx,
        "info",
        f"Waiting for PR #{pr_number} in merge queue on {target_branch!r}",
        "autoskillit.wait_for_merge_queue",
        extra={"pr_number": pr_number, "target_branch": target_branch},
    )

    from autoskillit.server import _get_ctx

    tool_ctx = _get_ctx()

    if tool_ctx.merge_queue_watcher is None:
        return json.dumps(
            {
                "success": False,
                "pr_state": "error",
                "reason": "merge_queue_watcher not configured (missing GITHUB_TOKEN?)",
            }
        )

    resolved_repo = await infer_repo_from_remote(cwd, hint=remote_url or repo or None)

    result = await tool_ctx.merge_queue_watcher.wait(
        pr_number=pr_number,
        target_branch=target_branch,
        repo=resolved_repo or None,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
        stall_grace_period=stall_grace_period,
        max_stall_retries=max_stall_retries,
        not_in_queue_confirmation_cycles=not_in_queue_confirmation_cycles,
    )
    return json.dumps(result)
