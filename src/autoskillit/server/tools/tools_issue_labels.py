"""MCP tool handlers: claim_issue, release_issue (GitHub label management)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from autoskillit.core import (
    _parse_issue_ref,
    get_logger,
)
from autoskillit.server import mcp
from autoskillit.server._notify import track_response_size
from autoskillit.server.lifecycle._guards import _require_enabled, _require_no_infrastructure_fault
from autoskillit.server.lifecycle._session_scope import SCOPE_ANY, session_scoped
from autoskillit.server.recipe._recipe_segment_delivery import (
    PreparedRecipeSegmentDelivery,
    attach_recipe_segment,
    prepare_recipe_segment_delivery,
)
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._claim_helpers import (
    _claim_fetched_issue,
    _get_campaign_state_paths,
)

if TYPE_CHECKING:
    from autoskillit.config import GitHubConfig
    from autoskillit.core import GitHubFetcher

logger = get_logger(__name__)


def _extract_label_names(raw_labels: list[Any]) -> list[str]:
    """Extract label name strings from a mixed list of dicts or strings."""
    return [lbl["name"] if isinstance(lbl, dict) else str(lbl) for lbl in raw_labels]


async def _apply_release_label(
    *,
    github_client: GitHubFetcher,
    github_config: GitHubConfig,
    owner: str,
    repo: str,
    issue_number: int,
    effective_label: str,
    replacement_label: str,
    fallback_color: str,
    fallback_description: str,
    fallback_remove_labels: list[str],
    ensure_error_prefix: str,
    swap_error_prefix: str,
) -> dict[str, Any] | None:
    """Ensure and apply a release-state label, returning an error result when needed."""

    if err := github_config.check_label_allowed(replacement_label):
        return {
            "success": False,
            "issue_number": issue_number,
            "label": replacement_label,
            "error": err,
        }
    if github_config.state_for_label(replacement_label) is not None:
        color, description, remove_labels = github_config.resolve_label_metadata(replacement_label)
    else:
        color = fallback_color
        description = fallback_description
        remove_labels = fallback_remove_labels

    ensure_result = await github_client.ensure_label(
        owner,
        repo,
        replacement_label,
        color=color,
        description=description,
    )
    if not ensure_result.get("success"):
        return {
            "success": False,
            "issue_number": issue_number,
            "label": effective_label,
            "error": f"{ensure_error_prefix}: {ensure_result.get('error', '?')}",
        }

    swap_result = await github_client.swap_labels(
        owner,
        repo,
        issue_number,
        remove_labels=remove_labels,
        add_labels=[replacement_label],
    )
    if not swap_result.get("success"):
        return {
            "success": False,
            "issue_number": issue_number,
            "label": effective_label,
            "error": f"{swap_error_prefix}: {swap_result.get('error', '?')}",
        }
    return None


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@session_scoped(SCOPE_ANY)
@_cancellation_shield()
@track_response_size("claim_issue")
async def claim_issue(
    issue_url: str,
    label: str | None = None,
    allow_reentry: bool = False,
) -> str:
    """Apply an in-progress label to a GitHub issue to claim it for processing.

    Checks if the issue already has the label (another session may be processing it),
    ensures the label exists in the repo, then applies it atomically.

    Returns JSON with: success, claimed (bool), issue_number, label,
    review_approach_recommended, investigation_complete.
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

            from autoskillit.server import (  # circular-break
                _get_ctx,
            )  # circular-break: server-internal circular dependency

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

            claim_result = await _claim_fetched_issue(
                issue_url=issue_url,
                owner=owner,
                repo=repo,
                issue_number=issue_number,
                issue_body=result.get("body") or "",
                issue_state=result.get("state", "open"),
                get_current_labels=lambda: _extract_label_names(result.get("labels", [])),
                effective_label=effective_label,
                allow_reentry=allow_reentry,
                github_client=tool_ctx.github_client,
                get_campaign_state_paths=lambda: _get_campaign_state_paths(tool_ctx),
                github_config=tool_ctx.config.github,
            )
            if claim_result.get("claimed") is True:
                claim_result["issue_number"] = issue_number
            return json.dumps(claim_result)
    except Exception as exc:
        logger.error("claim_issue unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(tags={"autoskillit", "kitchen", "github"}, annotations={"readOnlyHint": True})
@session_scoped(SCOPE_ANY)
@_cancellation_shield()
@track_response_size("release_issue")
async def release_issue(
    issue_url: str,
    label: str | None = None,
    target_branch: str | None = None,
    staged_label: str | None = None,
    fail_label: str | None = None,
    close_issue: str | None = None,
    step_name: str = "",
    infrastructure_fault_override_reason: str | None = None,
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
        step_name: Exact YAML step key used for recovery segment delivery.
        infrastructure_fault_override_reason: Required to proceed when the most
            recently completed step ended in an infrastructure fault. Absent by
            default so refusal is the default path, not an opt-in.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    if (
        fault_gate := _require_no_infrastructure_fault(
            "release_issue", override_reason=infrastructure_fault_override_reason
        )
    ) is not None:
        return fault_gate
    try:
        prepared_segment: PreparedRecipeSegmentDelivery | None = None

        def _render(result: dict[str, Any]) -> str:
            return json.dumps(attach_recipe_segment(result, prepared_segment, success=False))

        with structlog.contextvars.bound_contextvars(tool="release_issue", issue_url=issue_url):
            logger.info("release_issue", issue_url=issue_url)

            from autoskillit.server import (  # circular-break
                _get_ctx,
            )  # circular-break: server-internal circular dependency

            tool_ctx = _get_ctx()
            prepared_segment = prepare_recipe_segment_delivery(tool_ctx, step_name)
            if tool_ctx.github_client is None:
                return _render(
                    {"success": False, "error": "GitHub token required for label management"}
                )

            effective_label = label or tool_ctx.config.github.in_progress_label

            try:
                owner, repo, issue_number = _parse_issue_ref(issue_url)
            except ValueError as exc:
                return _render({"success": False, "error": str(exc)})

            # Determine if staging is needed
            promotion_target = tool_ctx.config.branching.promotion_target
            should_stage = bool(target_branch) and target_branch != promotion_target

            staged = False
            config_fail_label = tool_ctx.config.github.fail_label
            effective_staged_label = staged_label or tool_ctx.config.github.staged_label

            # close_issue is intentionally not checked here — staging takes precedence over closing
            if should_stage:
                release_error = await _apply_release_label(
                    github_client=tool_ctx.github_client,
                    github_config=tool_ctx.config.github,
                    owner=owner,
                    repo=repo,
                    issue_number=issue_number,
                    effective_label=effective_label,
                    replacement_label=effective_staged_label,
                    fallback_color="0075ca",
                    fallback_description=(
                        f"Implementation staged and waiting for promotion to {promotion_target}"
                    ),
                    fallback_remove_labels=[
                        effective_label,
                        config_fail_label,
                        tool_ctx.config.github.queued_label,
                    ],
                    ensure_error_prefix="Failed to ensure staged label",
                    swap_error_prefix="Failed to apply staged label",
                )
                if release_error is not None:
                    return _render(release_error)
                staged = True
            elif fail_label is not None:
                release_error = await _apply_release_label(
                    github_client=tool_ctx.github_client,
                    github_config=tool_ctx.config.github,
                    owner=owner,
                    repo=repo,
                    issue_number=issue_number,
                    effective_label=effective_label,
                    replacement_label=fail_label,
                    fallback_color="d73a4a",
                    fallback_description="Recipe execution failed",
                    fallback_remove_labels=[
                        effective_label,
                        tool_ctx.config.github.queued_label,
                    ],
                    ensure_error_prefix="Failed to ensure fail label",
                    swap_error_prefix="Failed to apply fail label",
                )
                if release_error is not None:
                    return _render(release_error)

                return _render(
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
                    return _render(
                        {
                            "success": False,
                            "issue_number": issue_number,
                            "label": effective_label,
                            "error": f"Failed to remove label: {swap_result.get('error', '?')}",
                        }
                    )
                if close_issue == "true":
                    await tool_ctx.github_client.close_issue(owner, repo, issue_number)

            return _render(
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
        return _render({"success": False, "error": f"{type(exc).__name__}: {exc}"})
