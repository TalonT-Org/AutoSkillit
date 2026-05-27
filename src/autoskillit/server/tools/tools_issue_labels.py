"""MCP tool handlers: claim_issue, release_issue (GitHub label management)."""

from __future__ import annotations

import json
from typing import Any

import structlog

from autoskillit.core import REVIEW_APPROACH_MARKER, _parse_issue_ref, get_logger
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server._notify import track_response_size
from autoskillit.server.tools._claim_helpers import (
    _get_campaign_state_paths,
    _try_claim_with_liveness,
)

logger = get_logger(__name__)


def _extract_label_names(raw_labels: list[Any]) -> list[str]:
    """Extract label name strings from a mixed list of dicts or strings."""
    return [lbl["name"] if isinstance(lbl, dict) else str(lbl) for lbl in raw_labels]


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@track_response_size("claim_issue")
async def claim_issue(
    issue_url: str,
    label: str | None = None,
    allow_reentry: bool = False,
) -> str:
    """Apply an in-progress label to a GitHub issue to claim it for processing.

    Checks if the issue already has the label (another session may be processing it),
    ensures the label exists in the repo, then applies it atomically.

    Returns JSON with: success, claimed (bool), issue_number, label, review_approach_recommended.
    When claimed=false, the issue is already being processed by another session.
    When allow_reentry=True and label already present, returns claimed=True with reentry=True.
    On gate closed or no token: {success: false, error: "..."}.

    Args:
        issue_url: Full GitHub issue URL (https://github.com/owner/repo/issues/42)
                   or shorthand (owner/repo#42).
        label: Label name to apply. Defaults to github.in_progress_label from config.
        allow_reentry: When True and the in-progress label is already present, returns
                       claimed=True with reentry=True instead of claimed=False. Used by
                       process-issues to re-enter recipes for upfront-claimed issues.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        with structlog.contextvars.bound_contextvars(tool="claim_issue", issue_url=issue_url):
            logger.info("claim_issue", issue_url=issue_url)

            from autoskillit.server import _get_ctx

            tool_ctx = _get_ctx()
            if tool_ctx.github_client is None:
                return json.dumps(
                    {"success": False, "error": "GitHub token required for label management"}
                )

            effective_label = label or tool_ctx.config.github.in_progress_label

            try:
                owner, repo, issue_number = _parse_issue_ref(issue_url)
            except ValueError as exc:
                return json.dumps({"success": False, "error": str(exc)})

            if err := tool_ctx.config.github.check_label_allowed(effective_label):
                return json.dumps({"success": False, "error": err})

            result = await tool_ctx.github_client.fetch_issue(issue_url, include_comments=False)
            if not result.get("success"):
                return json.dumps({"success": False, "error": result.get("error", "fetch failed")})

            review_approach_recommended = REVIEW_APPROACH_MARKER in (result.get("body") or "")
            issue_state = result.get("state", "open").lower()
            if issue_state == "closed":
                return json.dumps(
                    {
                        "success": True,
                        "claimed": False,
                        "reason": "issue is closed",
                    }
                )

            current_labels = _extract_label_names(result.get("labels", []))
            decision = await _try_claim_with_liveness(
                issue_url=issue_url,
                issue_number=issue_number,
                effective_label=effective_label,
                current_labels=current_labels,
                allow_reentry=allow_reentry,
                github_client=tool_ctx.github_client,
                campaign_state_paths=_get_campaign_state_paths(tool_ctx),
            )
            if not decision.claimed:
                return json.dumps(
                    {
                        "success": True,
                        "claimed": False,
                        "reason": decision.reason,
                    }
                )
            if decision.reentry:
                return json.dumps(
                    {
                        "success": True,
                        "claimed": True,
                        "reentry": True,
                        "issue_number": issue_number,
                        "label": effective_label,
                        "review_approach_recommended": review_approach_recommended,
                    }
                )

            ensure_color, ensure_description, remove_labels = (
                tool_ctx.config.github.resolve_label_metadata(effective_label)
            )

            await tool_ctx.github_client.ensure_label(
                owner,
                repo,
                effective_label,
                color=ensure_color,
                description=ensure_description,
            )

            swap_result = await tool_ctx.github_client.swap_labels(
                owner,
                repo,
                issue_number,
                remove_labels=remove_labels,
                add_labels=[effective_label],
            )
            if not swap_result.get("success"):
                return json.dumps(
                    {"success": False, "error": swap_result.get("error", "swap_labels failed")}
                )

            return json.dumps(
                {
                    "success": True,
                    "claimed": True,
                    "issue_number": issue_number,
                    "label": effective_label,
                    "review_approach_recommended": review_approach_recommended,
                }
            )
    except Exception as exc:
        logger.error("claim_issue unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@track_response_size("release_issue")
async def release_issue(
    issue_url: str,
    label: str | None = None,
    target_branch: str | None = None,
    staged_label: str | None = None,
    fail_label: str | None = None,
    close_issue: str | None = None,
) -> str:
    """Remove the in-progress label from a GitHub issue to release it.

    Call this in cleanup paths (both success and failure) to allow the issue
    to be picked up by future pipeline runs.

    When target_branch is provided and differs from the configured default base branch,
    also applies a staged label to indicate the work is merged and awaiting promotion.

    When fail_label is provided (and target_branch is NOT), swaps in-progress for the
    fail label to mark the issue as failed without releasing it back to the queue.

    Returns JSON with: success, issue_number, label, staged, staged_label.
    On gate closed or no token: {success: false, error: "..."}.

    Args:
        issue_url: Full GitHub issue URL or shorthand (owner/repo#42).
        label: Label name to remove. Defaults to github.in_progress_label from config.
        target_branch: Branch the PR was merged into. When non-default, applies staged label.
        staged_label: Label name for staged state. Defaults to github.staged_label from config.
        fail_label: Label name for failure state. When provided, swaps in-progress for this label.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        with structlog.contextvars.bound_contextvars(tool="release_issue", issue_url=issue_url):
            logger.info("release_issue", issue_url=issue_url)

            from autoskillit.server import _get_ctx

            tool_ctx = _get_ctx()
            if tool_ctx.github_client is None:
                return json.dumps(
                    {"success": False, "error": "GitHub token required for label management"}
                )

            effective_label = label or tool_ctx.config.github.in_progress_label

        try:
            owner, repo, issue_number = _parse_issue_ref(issue_url)
        except ValueError as exc:
            return json.dumps({"success": False, "error": str(exc)})

        # Determine if staging is needed
        promotion_target = tool_ctx.config.branching.promotion_target
        should_stage = target_branch is not None and target_branch != promotion_target

        staged = False
        config_fail_label = tool_ctx.config.github.fail_label
        effective_staged_label = staged_label or tool_ctx.config.github.staged_label

        if should_stage:
            if err := tool_ctx.config.github.check_label_allowed(effective_staged_label):
                return json.dumps(
                    {
                        "success": False,
                        "issue_number": issue_number,
                        "label": effective_staged_label,
                        "error": err,
                    }
                )

            if tool_ctx.config.github.state_for_label(effective_staged_label) is not None:
                staged_color, staged_description, remove_labels = (
                    tool_ctx.config.github.resolve_label_metadata(effective_staged_label)
                )
            else:
                staged_color = "0075ca"
                staged_description = (
                    f"Implementation staged and waiting for promotion to {promotion_target}"
                )
                remove_labels = [
                    effective_label,
                    config_fail_label,
                    tool_ctx.config.github.queued_label,
                ]

            ensure_result = await tool_ctx.github_client.ensure_label(
                owner,
                repo,
                effective_staged_label,
                color=staged_color,
                description=staged_description,
            )
            if not ensure_result.get("success"):
                return json.dumps(
                    {
                        "success": False,
                        "issue_number": issue_number,
                        "label": effective_label,
                        "error": (
                            f"Failed to ensure staged label: {ensure_result.get('error', '?')}"
                        ),
                    }
                )

            swap_result = await tool_ctx.github_client.swap_labels(
                owner,
                repo,
                issue_number,
                remove_labels=remove_labels,
                add_labels=[effective_staged_label],
            )
            if not swap_result.get("success"):
                return json.dumps(
                    {
                        "success": False,
                        "issue_number": issue_number,
                        "label": effective_label,
                        "error": f"Failed to apply staged label: {swap_result.get('error', '?')}",
                    }
                )
            staged = True
        elif fail_label is not None:
            if err := tool_ctx.config.github.check_label_allowed(fail_label):
                return json.dumps(
                    {
                        "success": False,
                        "issue_number": issue_number,
                        "label": fail_label,
                        "error": err,
                    }
                )

            if tool_ctx.config.github.state_for_label(fail_label) is not None:
                fail_color, fail_description, remove_labels = (
                    tool_ctx.config.github.resolve_label_metadata(fail_label)
                )
            else:
                fail_color = "d73a4a"
                fail_description = "Recipe execution failed"
                remove_labels = [effective_label, tool_ctx.config.github.queued_label]

            ensure_result = await tool_ctx.github_client.ensure_label(
                owner,
                repo,
                fail_label,
                color=fail_color,
                description=fail_description,
            )
            if not ensure_result.get("success"):
                return json.dumps(
                    {
                        "success": False,
                        "issue_number": issue_number,
                        "label": effective_label,
                        "error": (
                            f"Failed to ensure fail label: {ensure_result.get('error', '?')}"
                        ),
                    }
                )

            swap_result = await tool_ctx.github_client.swap_labels(
                owner,
                repo,
                issue_number,
                remove_labels=remove_labels,
                add_labels=[fail_label],
            )
            if not swap_result.get("success"):
                return json.dumps(
                    {
                        "success": False,
                        "issue_number": issue_number,
                        "label": effective_label,
                        "error": f"Failed to apply fail label: {swap_result.get('error', '?')}",
                    }
                )

            return json.dumps(
                {
                    "success": True,
                    "issue_number": issue_number,
                    "label": effective_label,
                    "failed": True,
                    "fail_label": fail_label,
                }
            )
        else:
            logger.warning(
                "release_issue bare removal (no fail_label or target_branch) for %s",
                issue_url,
            )
            swap_result = await tool_ctx.github_client.swap_labels(
                owner,
                repo,
                issue_number,
                remove_labels=tool_ctx.config.github.all_lifecycle_labels(),
                add_labels=[],
            )
            if not swap_result.get("success"):
                return json.dumps(
                    {
                        "success": False,
                        "issue_number": issue_number,
                        "label": effective_label,
                        "error": f"Failed to remove label: {swap_result.get('error', '?')}",
                    }
                )
            if close_issue == "true":
                await tool_ctx.github_client.close_issue(owner, repo, issue_number)

        return json.dumps(
            {
                "success": True,
                "issue_number": issue_number,
                "label": effective_label,
                "staged": staged,
                "staged_label": effective_staged_label if staged else None,
            }
        )
    except Exception as exc:
        logger.error("release_issue unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
