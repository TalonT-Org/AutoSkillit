"""Merge queue MCP tools: toggle_auto_merge, enqueue_pr, wait_for_merge_queue."""

from __future__ import annotations

import json
import time

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import get_logger
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server._misc import resolve_repo_from_remote
from autoskillit.server._notify import _notify, track_response_size

logger = get_logger(__name__)


@mcp.tool(tags={"autoskillit", "kitchen", "ci"}, annotations={"readOnlyHint": True})
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

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
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

        resolved_repo = await resolve_repo_from_remote(cwd, hint=remote_url or repo or None)

        result = await tool_ctx.merge_queue_watcher.toggle(
            pr_number=pr_number,
            target_branch=target_branch,
            repo=resolved_repo or None,
            cwd=cwd,
        )
        return json.dumps(result)
    except Exception as exc:
        logger.error("toggle_auto_merge unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(tags={"autoskillit", "kitchen", "ci"}, annotations={"readOnlyHint": True})
@track_response_size("enqueue_pr")
async def enqueue_pr(
    pr_number: int,
    target_branch: str,
    cwd: str,
    auto_merge_available: bool,
    repo: str = "",
    remote_url: str = "",
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Enqueue a PR into the merge queue using the correct enrollment strategy.

    Uses enablePullRequestAutoMerge when auto_merge_available=true,
    enqueuePullRequest GraphQL mutation when auto_merge_available=false.

    Args:
        pr_number: PR number to enqueue.
        target_branch: Branch the merge queue targets (e.g. "integration").
        cwd: Working directory for git remote resolution when repo is not provided.
        auto_merge_available: Whether the repository allows auto-merge.
        repo: Optional "owner/name" string. Inferred from git remote if empty.
        remote_url: Full GitHub remote URL. Parsed to owner/repo before inference.
        step_name: Optional YAML step key for wall-clock timing accumulation.

    Returns:
        JSON: {"success": bool, "pr_number": int, "enrollment_method": str} on success,
              {"success": false, "error": str} on failure.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            tool="enqueue_pr", pr_number=pr_number, target_branch=target_branch
        )
        await _notify(
            ctx,
            "info",
            f"Enrolling PR #{pr_number} in merge queue on {target_branch!r}",
            "autoskillit.enqueue_pr",
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

        resolved_repo = await resolve_repo_from_remote(cwd, hint=remote_url or repo or None)

        _start = time.monotonic()
        try:
            result = await tool_ctx.merge_queue_watcher.enqueue(
                pr_number=pr_number,
                target_branch=target_branch,
                repo=resolved_repo or None,
                cwd=cwd,
                auto_merge_available=auto_merge_available,
            )
            return json.dumps(result)
        except Exception as exc:
            logger.error("enqueue_pr watcher error", exc_info=True)
            return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            if step_name:
                tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
    except Exception as exc:
        logger.error("enqueue_pr unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(tags={"autoskillit", "kitchen", "ci"}, annotations={"readOnlyHint": True})
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
    max_inconclusive_retries: int = 5,
    auto_merge_available: bool = True,
    step_name: str = "",
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
        max_inconclusive_retries: Maximum NoPositiveSignal cycles (beyond the
                    confirmation window) before returning pr_state="timeout" (default 5).
        auto_merge_available: Whether the repository allows auto-merge. When False,
                    stall recovery uses enqueuePullRequest instead of toggle (default True).
        step_name: Optional YAML step key for wall-clock timing accumulation.

    Returns:
        JSON: {
            "success": bool,
            "pr_state": "merged"|"ejected"|"ejected_ci_failure"|"stalled"|
                        "dropped_healthy"|"dropped_merge_group_ci"|
                        "not_enrolled"|"timeout"|"error",
            "reason": str,
            "stall_retries_attempted": int,
        }

        pr_state values:
          merged                — PR successfully merged through the queue.
          ejected               — PR removed from queue (conflict or other non-CI reason).
          ejected_ci_failure    — PR removed from queue because CI checks failed.
          stalled               — PR stuck in queue; max stall retries exhausted.
          dropped_healthy       — auto-merge disabled on a PR with no CI/conflict issues.
          dropped_merge_group_ci — PR ejected due to merge-group CI failure (PR-branch CI clean).
          not_enrolled          — PR was never enrolled in the merge queue.
          timeout               — polling budget exhausted before a terminal state was reached.
          error                 — watcher raised an unexpected exception.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
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
            if step_name:
                tool_ctx.timing_log.record(step_name, 0.0)
            return json.dumps(
                {
                    "success": False,
                    "pr_state": "error",
                    "reason": "merge_queue_watcher not configured (missing GITHUB_TOKEN?)",
                }
            )

        resolved_repo = await resolve_repo_from_remote(cwd, hint=remote_url or repo or None)

        _start = time.monotonic()
        try:
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
                max_inconclusive_retries=max_inconclusive_retries,
                auto_merge_available=auto_merge_available,
            )
            return json.dumps(result)
        except Exception as exc:
            logger.error("wait_for_merge_queue ci_watcher error", exc_info=True)
            return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            if step_name:
                tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
    except Exception as exc:
        logger.error("wait_for_merge_queue unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
