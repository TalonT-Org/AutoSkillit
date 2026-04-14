"""MCP tool handlers: wait_for_ci (gated), get_ci_status (ungated), set_commit_status (gated),
wait_for_merge_queue (gated).
"""

from __future__ import annotations

import json
import time
from typing import Literal

import httpx
import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import CIRunScope, get_logger
from autoskillit.server import mcp
from autoskillit.server.helpers import (
    _notify,
    _require_enabled,
    _run_subprocess,
    fetch_repo_merge_state,
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
    event: str | None = None,
    timeout_seconds: int = 300,
    cwd: str = ".",
    step_name: str = "",
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
        event: GitHub trigger event to filter runs (e.g. "push", "pull_request").
               If omitted, falls back to the project-level ci.event config.
        timeout_seconds: Maximum time to wait (default 300s).
        cwd: Working directory for git operations.
        step_name: Optional YAML step key for wall-clock timing accumulation.

    Returns:
        JSON with run_id, conclusion ("success", "failure", "cancelled",
        "action_required", "timed_out", "no_runs", "error", "unknown"),
        and failed_jobs list. Billing limit errors surface as
        conclusion="action_required" with failed_jobs=[].

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        _start = time.monotonic()
        _timing_ctx = None
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(tool="wait_for_ci")
        logger.info("wait_for_ci", branch=branch, repo=repo or "(infer)")

        from autoskillit.server import _get_ctx

        tool_ctx = _get_ctx()
        _timing_ctx = tool_ctx
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
                rc, stdout, _ = await _run_subprocess(
                    ["git", "rev-parse", "HEAD"], cwd=cwd, timeout=5.0
                )
                if rc == 0:
                    head_sha = stdout.strip()
            except Exception:
                logger.warning("git rev-parse HEAD failed", exc_info=True)

        scope = CIRunScope(
            workflow=workflow or tool_ctx.default_ci_scope.workflow,
            head_sha=head_sha,
            event=event or tool_ctx.default_ci_scope.event,
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

        try:
            result = await tool_ctx.ci_watcher.wait(
                branch,
                repo=resolved_repo or None,
                scope=scope,
                timeout_seconds=timeout_seconds,
                cwd=cwd,
            )

            # Include head_sha used for this CI check so orchestrators can verify
            # CI results correspond to the current HEAD after a force-push.
            if scope.head_sha:
                result = {**result, "head_sha": scope.head_sha}

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
        except Exception as exc:
            logger.error("wait_for_ci failed", exc_info=True)
            return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            if step_name:
                tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
    except Exception as exc:
        logger.error("wait_for_ci unhandled exception", exc_info=True)
        if step_name and _timing_ctx is not None:
            _timing_ctx.timing_log.record(step_name, time.monotonic() - _start)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


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
) -> str:
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

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate

    if not sha:
        return json.dumps({"success": False, "error": "sha must not be empty"})
    if not context:
        return json.dumps({"success": False, "error": "context must not be empty"})
    if len(description) > 140:
        return json.dumps(
            {
                "success": False,
                "error": f"description exceeds 140 chars ({len(description)} chars)",
            }
        )

    try:
        from autoskillit.server import _get_ctx

        tool_ctx = _get_ctx()
        effective_cwd = cwd or tool_ctx.plugin_dir or "."

        # Resolve owner/repo if not provided
        owner_repo = repo
        if not owner_repo:
            owner_repo = await infer_repo_from_remote(effective_cwd)
            if not owner_repo:
                return json.dumps(
                    {"success": False, "error": "Could not infer owner/repo from git remote"}
                )

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
            return json.dumps({"success": False, "error": stderr})

        return json.dumps({"success": True, "sha": sha, "state": state, "context": context})
    except Exception as exc:
        logger.error("set_commit_status unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(tags={"autoskillit", "kitchen", "ci"}, annotations={"readOnlyHint": True})
@track_response_size("get_ci_status")
async def get_ci_status(
    branch: str | None = None,
    run_id: int | None = None,
    repo: str | None = None,
    workflow: str | None = None,
    event: str | None = None,
    cwd: str = ".",
) -> str:
    """Return current CI status for a branch or specific run without waiting.

    Args:
        branch: Git branch name. Required if run_id is not provided.
        run_id: Specific run ID to check. If provided, branch is ignored.
        repo: GitHub owner/repo. If omitted, inferred from git remote in cwd.
        workflow: Workflow filename to filter runs (e.g. "tests.yml"). If
                  omitted, falls back to the project-level ci.workflow config.
        event: GitHub trigger event to filter runs (e.g. "push", "pull_request").
               If omitted, falls back to the project-level ci.event config.
        cwd: Working directory for git operations.

    Returns:
        JSON with runs list, each containing id, status, conclusion, failed_jobs.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        from autoskillit.server import _get_ctx

        tool_ctx = _get_ctx()
        if tool_ctx.ci_watcher is None:
            return json.dumps({"runs": [], "error": "CI watcher not configured"})

        if branch is None and run_id is None:
            return json.dumps({"runs": [], "error": "Provide branch or run_id"})

        scope = CIRunScope(
            workflow=workflow or tool_ctx.default_ci_scope.workflow,
            event=event or tool_ctx.default_ci_scope.event,
        )

        result = await tool_ctx.ci_watcher.status(
            branch or "",
            repo=repo,
            run_id=run_id,
            scope=scope,
            cwd=cwd,
        )
        return json.dumps(result)
    except Exception as exc:
        logger.error("get_ci_status unhandled exception", exc_info=True)
        return json.dumps({"runs": [], "error": f"{type(exc).__name__}: {exc}"})


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

        resolved_repo = await infer_repo_from_remote(cwd, hint=remote_url or repo or None)

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
    max_inconclusive_retries: int = 5,
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
        step_name: Optional YAML step key for wall-clock timing accumulation.

    Returns:
        JSON: {
            "success": bool,
            "pr_state": "merged"|"ejected"|"ejected_ci_failure"|"stalled"|
                        "dropped_healthy"|"timeout"|"error",
            "reason": str,
            "stall_retries_attempted": int,
        }

        pr_state values:
          merged           — PR successfully merged through the queue.
          ejected          — PR removed from queue (conflict or other non-CI reason).
          ejected_ci_failure — PR removed from queue because CI checks failed.
          stalled          — PR stuck in queue; max stall retries exhausted.
          dropped_healthy  — auto-merge disabled on a PR with no CI/conflict issues.
          timeout          — polling budget exhausted before a terminal state was reached.
          error            — watcher raised an unexpected exception.

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
            return json.dumps(
                {
                    "success": False,
                    "pr_state": "error",
                    "reason": "merge_queue_watcher not configured (missing GITHUB_TOKEN?)",
                }
            )

        resolved_repo = await infer_repo_from_remote(cwd, hint=remote_url or repo or None)

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


@mcp.tool(tags={"autoskillit", "kitchen", "ci"}, annotations={"readOnlyHint": True})
@track_response_size("check_repo_merge_state")
async def check_repo_merge_state(
    branch: str,
    cwd: str = ".",
    remote_url: str = "",
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Single GraphQL round-trip returning queue_available, merge_group_trigger,
    auto_merge_available, and ci_event for the given repository branch.

    Consolidates the three former run_cmd shell steps in the pre_queue_gate block
    into a single MCP tool call, eliminating N+2 REST/GraphQL round-trips and
    making the call budget auditable by block-level semantic rules.

    Args:
        branch: Target branch name to check merge queue state for.
        cwd: Working directory for git remote resolution.
        remote_url: Full GitHub remote URL; parsed to owner/repo if provided.
        step_name: Step name for timing telemetry.

    Returns a JSON object with keys:
    - ``queue_available``: branch has an active GitHub merge queue.
    - ``merge_group_trigger``: a CI workflow declares the merge_group event.
    - ``auto_merge_available``: repository has auto-merge enabled.
    - ``ci_event``: ``"push"`` | ``"merge_group"`` | ``null`` — recommended
      event to use when polling CI via wait_for_ci.
    On any error, returns an error field alongside the four boolean/null defaults.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        from autoskillit.server import _get_ctx

        tool_ctx = _get_ctx()
        _start = time.monotonic()
        try:
            resolved_repo = await infer_repo_from_remote(cwd, hint=remote_url or None)
            if not resolved_repo or "/" not in resolved_repo:
                return json.dumps(
                    {
                        "error": f"Could not resolve owner/repo from cwd={cwd!r}",
                        "queue_available": False,
                        "merge_group_trigger": False,
                        "auto_merge_available": False,
                        "ci_event": None,
                    }
                )
            owner, repo = resolved_repo.split("/", 1)
            resolved_token = (
                tool_ctx.token_factory()
                if tool_ctx.token_factory is not None
                else tool_ctx.config.github.token
            )
            state = await fetch_repo_merge_state(
                owner=owner,
                repo=repo,
                branch=branch,
                token=resolved_token,
            )
            return json.dumps(state)
        except Exception as exc:
            logger.error("autoskillit.check_repo_merge_state failed", exc_info=exc)
            envelope: dict[str, object] = {
                "error": f"{type(exc).__name__}: {exc}",
                "queue_available": False,
                "merge_group_trigger": False,
                "auto_merge_available": False,
                "ci_event": None,
            }
            if isinstance(exc, httpx.HTTPStatusError):
                envelope["http_status"] = exc.response.status_code
            return json.dumps(envelope)
        finally:
            if step_name:
                tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
    except Exception as exc:
        logger.error("check_repo_merge_state unhandled exception", exc_info=True)
        return json.dumps(
            {
                "error": f"{type(exc).__name__}: {exc}",
                "queue_available": False,
                "merge_group_trigger": False,
                "auto_merge_available": False,
                "ci_event": None,
            }
        )
