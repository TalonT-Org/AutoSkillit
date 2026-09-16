"""Shared claiming logic for claim_issue and claim_and_resolve_issue."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Required, TypedDict

from autoskillit.core import (
    INVESTIGATION_COMPLETE_MARKER,
    REVIEW_APPROACH_MARKER,
    detect_body_marker,
    get_logger,
)
from autoskillit.fleet import (
    TERMINAL_UNCLEANED_STATUSES,
    CampaignStateMutator,
    DispatchStatus,
    cleanup_orphaned_labels,
    discover_campaign_state_files,
    find_dispatch_for_issue,
    is_dispatch_session_alive,
)

if TYPE_CHECKING:
    from autoskillit.config import GitHubConfig
    from autoskillit.core import GitHubFetcher
    from autoskillit.pipeline.context import ToolContext

logger = get_logger(__name__)


class ClaimResult(TypedDict, total=False):
    """Structured result shared by the issue-claim tool handlers."""

    success: Required[bool]
    claimed: bool
    reason: str
    reentry: bool
    label: str
    error: str
    review_approach_recommended: bool
    investigation_complete: bool
    issue_number: int
    issue_title: str
    issue_slug: str
    timings: dict[str, int]


class _ClaimMarkers(TypedDict):
    review_approach_recommended: bool
    investigation_complete: bool


@dataclass(frozen=True, slots=True)
class ClaimDecision:
    claimed: bool
    reentry: bool = False
    stale_label_cleaned: bool = False
    reason: str = ""


def _get_campaign_state_paths(tool_ctx: ToolContext) -> list[Path]:

    return discover_campaign_state_files(tool_ctx.project_dir)


def _mark_dispatch_labels_cleaned(dispatch_name: str, campaign_state_paths: list[Path]) -> None:
    """Persist labels_cleaned=True for a dispatch after claim-time label cleanup."""

    for state_path in campaign_state_paths:
        try:
            with CampaignStateMutator(state_path) as m:
                if m.state is None:
                    continue
                for d in m.state.dispatches:
                    if d.name == dispatch_name:
                        d.labels_cleaned = True
                        m.mark_dirty()
                        return
        except Exception:
            logger.warning(
                "claim_mark_labels_cleaned_failed",
                state_path=str(state_path),
                dispatch_name=dispatch_name,
                exc_info=True,
            )
            continue


async def _try_claim_with_liveness(
    issue_url: str,
    issue_number: int,
    effective_label: str,
    current_labels: list[str],
    allow_reentry: bool,
    github_client: GitHubFetcher | None,
    campaign_state_paths: list[Path],
) -> ClaimDecision:
    """Core label-presence check with liveness fallback.

    Called by both claim_issue and claim_and_resolve_issue. Returns a ClaimDecision
    indicating whether to proceed with the claim. When the owning dispatch session is
    dead, cleans up the stale label inline and returns claimed=True.
    """

    if effective_label not in current_labels:
        return ClaimDecision(claimed=True)
    if allow_reentry:
        return ClaimDecision(claimed=True, reentry=True)
    dispatch = find_dispatch_for_issue(issue_url, campaign_state_paths)
    if dispatch is None:
        return ClaimDecision(
            claimed=False,
            reason=(
                f"Issue #{issue_number} already has '{effective_label}' label"
                " — another session may be processing it"
            ),
        )
    if dispatch.status in TERMINAL_UNCLEANED_STATUSES:
        cleaned = await cleanup_orphaned_labels(
            dispatch.sidecar_path, github_client, issue_url=dispatch.issue_url
        )
        if cleaned:
            _mark_dispatch_labels_cleaned(dispatch.name, campaign_state_paths)
        return ClaimDecision(claimed=True, stale_label_cleaned=cleaned)
    if dispatch.status == DispatchStatus.PENDING:
        return ClaimDecision(
            claimed=False,
            reason=(
                f"Issue #{issue_number} already has '{effective_label}' label"
                " — a reset dispatch is pending retry"
            ),
        )
    if is_dispatch_session_alive(dispatch):
        return ClaimDecision(
            claimed=False,
            reason=(
                f"Issue #{issue_number} already has '{effective_label}' label"
                " — owning dispatch session is still alive"
            ),
        )
    cleaned = await cleanup_orphaned_labels(
        dispatch.sidecar_path, github_client, issue_url=dispatch.issue_url
    )
    return ClaimDecision(claimed=True, stale_label_cleaned=cleaned)


async def _claim_fetched_issue(
    *,
    issue_url: str,
    owner: str,
    repo: str,
    issue_number: int,
    issue_body: str,
    issue_state: str,
    get_current_labels: Callable[[], list[str]],
    effective_label: str,
    allow_reentry: bool,
    github_client: GitHubFetcher,
    get_campaign_state_paths: Callable[[], list[Path]],
    github_config: GitHubConfig,
) -> ClaimResult:
    """Claim an already-fetched issue and return its common result fragment."""

    markers: _ClaimMarkers = {
        "review_approach_recommended": detect_body_marker(issue_body, REVIEW_APPROACH_MARKER),
        "investigation_complete": detect_body_marker(issue_body, INVESTIGATION_COMPLETE_MARKER),
    }
    if issue_state.lower() == "closed":
        return {"success": True, "claimed": False, "reason": "issue is closed", **markers}

    decision = await _try_claim_with_liveness(
        issue_url=issue_url,
        issue_number=issue_number,
        effective_label=effective_label,
        current_labels=get_current_labels(),
        allow_reentry=allow_reentry,
        github_client=github_client,
        campaign_state_paths=get_campaign_state_paths(),
    )
    if not decision.claimed:
        return {"success": True, "claimed": False, "reason": decision.reason, **markers}
    if decision.reentry:
        return {
            "success": True,
            "claimed": True,
            "reentry": True,
            "label": effective_label,
            **markers,
        }

    ensure_color, ensure_description, remove_labels = github_config.resolve_label_metadata(
        effective_label
    )
    await github_client.ensure_label(
        owner,
        repo,
        effective_label,
        color=ensure_color,
        description=ensure_description,
    )
    swap_result = await github_client.swap_labels(
        owner,
        repo,
        issue_number,
        remove_labels=remove_labels,
        add_labels=[effective_label],
    )
    if not swap_result.get("success"):
        return {
            "success": False,
            "error": swap_result.get("error", "swap_labels failed"),
            **markers,
        }
    return {"success": True, "claimed": True, "label": effective_label, **markers}
