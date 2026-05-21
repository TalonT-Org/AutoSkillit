"""Shared claiming logic for claim_issue and claim_and_resolve_issue."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.core import GitHubFetcher
    from autoskillit.pipeline.context import ToolContext


@dataclass(frozen=True, slots=True)
class ClaimDecision:
    claimed: bool
    reentry: bool = False
    stale_label_cleaned: bool = False
    reason: str = ""


def _get_campaign_state_paths(tool_ctx: ToolContext) -> list[Path]:
    from autoskillit.fleet import discover_campaign_state_files  # noqa: PLC0415

    return discover_campaign_state_files(tool_ctx.project_dir)


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
    from autoskillit.fleet import (  # noqa: PLC0415
        DispatchStatus,
        cleanup_orphaned_labels,
        find_dispatch_for_issue,
        is_dispatch_session_alive,
    )

    _TERMINAL_STATUSES = frozenset({DispatchStatus.FAILURE, DispatchStatus.INTERRUPTED})

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
    if dispatch.status in _TERMINAL_STATUSES:
        await cleanup_orphaned_labels(dispatch.sidecar_path, github_client)
        return ClaimDecision(claimed=True, stale_label_cleaned=True)
    if is_dispatch_session_alive(dispatch):
        return ClaimDecision(
            claimed=False,
            reason=(
                f"Issue #{issue_number} already has '{effective_label}' label"
                " — owning dispatch session is still alive"
            ),
        )
    await cleanup_orphaned_labels(dispatch.sidecar_path, github_client)
    return ClaimDecision(claimed=True, stale_label_cleaned=True)
