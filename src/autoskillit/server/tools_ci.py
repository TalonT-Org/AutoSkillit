"""MCP tool handlers: set_commit_status (gated), check_repo_merge_state (gated)."""

from __future__ import annotations

import json
import time
from typing import Literal

import httpx
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import DirectInstall, MarketplaceInstall, get_logger
from autoskillit.server import mcp
from autoskillit.server.helpers import (
    _require_enabled,
    _run_subprocess,
    fetch_repo_merge_state,
    resolve_repo_from_remote,
    track_response_size,
)

logger = get_logger(__name__)


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
        if cwd:
            effective_cwd = cwd
        else:
            match tool_ctx.plugin_source:
                case DirectInstall(plugin_dir=p):
                    effective_cwd = str(p)
                case MarketplaceInstall(cache_path=cp):
                    effective_cwd = str(cp)

        # Resolve owner/repo if not provided
        owner_repo = repo
        if not owner_repo:
            owner_repo = await resolve_repo_from_remote(effective_cwd)
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
            resolved_repo = await resolve_repo_from_remote(cwd, hint=remote_url or None)
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
            logger.error("autoskillit.check_repo_merge_state failed", exc_info=True)
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
