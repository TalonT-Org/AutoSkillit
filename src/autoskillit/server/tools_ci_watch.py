"""CI watching MCP tools: wait_for_ci and get_ci_status."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Literal

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import CIRunScope, get_logger
from autoskillit.pipeline import ToolContext
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server._misc import resolve_repo_from_remote
from autoskillit.server._notify import _notify, track_response_size
from autoskillit.server._subprocess import _run_subprocess

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
    lookback_seconds: int = 3600,
    cwd: str = ".",
    step_name: str = "",
    auto_trigger: bool = False,
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
        auto_trigger: When True and ci_watcher returns "no_runs", performs an active
                      self-healing sequence: checks PR mergeability, creates an empty
                      commit, and force-pushes the branch to re-trigger webhook delivery,
                      then re-polls CI with a fresh timeout. Result includes
                      "triggered": true when the sequence fires. Default False.

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
            if step_name:
                tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
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

        resolved_repo = await resolve_repo_from_remote(cwd, hint=remote_url or repo or None)

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
                lookback_seconds=lookback_seconds,
                cwd=cwd,
            )

            # Include head_sha used for this CI check so orchestrators can verify
            # CI results correspond to the current HEAD after a force-push.
            if scope.head_sha:
                result = {**result, "head_sha": scope.head_sha}

            if auto_trigger and result.get("conclusion") == "no_runs":
                result = await _auto_trigger_ci(
                    branch=branch,
                    cwd=cwd,
                    result=result,
                    scope=scope,
                    resolved_repo=resolved_repo,
                    tool_ctx=tool_ctx,
                    timeout_seconds=timeout_seconds,
                    lookback_seconds=lookback_seconds,
                )

            conclusion = result.get("conclusion", "unknown")
            level: Literal["info", "error"] = "info" if conclusion == "success" else "error"
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
            return json.dumps(
                {
                    "run_id": None,
                    "conclusion": "error",
                    "failed_jobs": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        finally:
            if step_name:
                tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
    except Exception as exc:
        logger.error("wait_for_ci unhandled exception", exc_info=True)
        if step_name and _timing_ctx is not None:
            _timing_ctx.timing_log.record(step_name, time.monotonic() - _start)
        return json.dumps(
            {
                "run_id": None,
                "conclusion": "error",
                "failed_jobs": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        )


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


async def _auto_trigger_ci(
    *,
    branch: str,
    cwd: str,
    result: dict[str, Any],
    scope: CIRunScope,
    resolved_repo: str | None,
    tool_ctx: ToolContext,
    timeout_seconds: int,
    lookback_seconds: int = 3600,
) -> dict[str, Any]:
    """Active CI trigger recovery: empty commit + force-push + re-poll.

    Called when wait_for_ci returns no_runs and auto_trigger=True.
    Returns result dict augmented with "triggered" key.
    On any failure (merge conflict, push rejected, etc.) returns original
    no_runs result so the recipe routes to handle_no_ci_runs as fallback.
    """
    rc_m, out_m, _ = await _run_subprocess(
        ["gh", "pr", "view", branch, "--json", "mergeable"],
        cwd=cwd,
        timeout=15.0,
    )
    if rc_m != 0:
        logger.warning("auto_trigger: gh pr view failed, cannot check mergeability", rc=rc_m)
        return {**result, "conclusion": "gh_view_failed", "triggered": False}
    try:
        mergeable = json.loads(out_m).get("mergeable", "UNKNOWN")
    except json.JSONDecodeError:
        logger.warning("auto_trigger: failed to parse gh pr view JSON", exc_info=True)
        mergeable = "UNKNOWN"
    if mergeable == "CONFLICTING":
        return {**result, "conclusion": "merge_conflict", "triggered": False}

    rc_c, _, err_c = await _run_subprocess(
        ["git", "commit", "--allow-empty", "-m", "ci: trigger"],
        cwd=cwd,
        timeout=30.0,
    )
    if rc_c != 0:
        logger.warning("auto_trigger: empty commit failed", stderr=err_c)
        return result

    rc_sha, sha_out, _ = await _run_subprocess(["git", "rev-parse", "HEAD"], cwd=cwd, timeout=5.0)
    new_head_sha = (sha_out.strip() or None) if rc_sha == 0 else None

    remote_name = "origin"
    for _candidate in ("upstream", "origin"):
        rc_r, url_r, _ = await _run_subprocess(
            ["git", "remote", "get-url", _candidate], cwd=cwd, timeout=5.0
        )
        if rc_r == 0 and not url_r.strip().startswith("file://"):
            remote_name = _candidate
            break
    rc_p, _, err_p = await _run_subprocess(
        ["git", "push", "--force-with-lease", remote_name, branch],
        cwd=cwd,
        timeout=60.0,
    )
    if rc_p != 0:
        logger.warning("auto_trigger: push failed", stderr=err_p)
        rc_reset, _, _ = await _run_subprocess(
            ["git", "reset", "--soft", "HEAD~1"], cwd=cwd, timeout=10.0
        )
        if rc_reset != 0:
            logger.warning("auto_trigger: cleanup reset failed; branch may be diverged")
        return result

    new_scope = CIRunScope(
        workflow=scope.workflow,
        head_sha=new_head_sha,
        event=scope.event,
    )
    if tool_ctx.ci_watcher is None:
        raise RuntimeError("auto_trigger: ci_watcher not configured on tool_ctx")
    try:
        triggered_result = await tool_ctx.ci_watcher.wait(
            branch,
            repo=resolved_repo,
            scope=new_scope,
            timeout_seconds=timeout_seconds,
            lookback_seconds=lookback_seconds,
            cwd=cwd,
        )
        if new_head_sha:
            triggered_result = {**triggered_result, "head_sha": new_head_sha}
        return {**triggered_result, "triggered": True}
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.error("auto_trigger: second CI poll failed", exc_info=True)
        return {**result, "conclusion": "auto_trigger_failed", "triggered": False}
