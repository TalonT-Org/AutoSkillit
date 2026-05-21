"""Infrastructure-level label cleanup for crashed or cancelled dispatches.

Wires the LABEL_LIFECYCLE_REGISTRY state machine to the fleet finally block and
startup sweep — the two infrastructure paths that run outside the recipe layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    LABEL_LIFECYCLE_REGISTRY,
    IssueLabelState,
    _parse_issue_ref,
    get_logger,
)
from autoskillit.fleet._liveness import is_dispatch_session_alive
from autoskillit.fleet.sidecar import read_sidecar_from_path
from autoskillit.fleet.state import CampaignStateMutator, DispatchStatus

if TYPE_CHECKING:
    from autoskillit.core import GitHubFetcher

logger = get_logger(__name__)

_REMOVE_LABELS: list[str] = sorted(
    s.value
    for s in LABEL_LIFECYCLE_REGISTRY[IssueLabelState.FAIL].removes_on_entry
    | {IssueLabelState.IN_PROGRESS}
)
_ADD_LABELS: list[str] = [IssueLabelState.FAIL.value]


async def cleanup_orphaned_labels(
    sidecar_path: str | None,
    github_client: GitHubFetcher | None,
) -> None:
    """Remove in-progress labels for all issues in a dispatch sidecar.

    Safe to call unconditionally from a finally block — returns immediately when
    sidecar_path or github_client is None, and swallows GitHub API errors so the
    original exception is never suppressed.
    """
    if sidecar_path is None or github_client is None:
        logger.debug(
            "infra_label_cleanup_skipped",
            reason="no_sidecar_or_no_client",
            has_sidecar=sidecar_path is not None,
        )
        return

    entries = read_sidecar_from_path(Path(sidecar_path))
    if not entries:
        return

    for entry in entries:
        try:
            owner, repo, number = _parse_issue_ref(entry.issue_url)
        except ValueError:
            logger.warning(
                "infra_label_cleanup_skip_bad_url",
                issue_url=entry.issue_url,
            )
            continue
        try:
            result = await github_client.swap_labels(
                owner,
                repo,
                number,
                remove_labels=_REMOVE_LABELS,
                add_labels=_ADD_LABELS,
            )
            logger.info(
                "infra_label_cleanup",
                issue_url=entry.issue_url,
                success=result.get("success"),
            )
        except Exception:
            logger.warning(
                "infra_label_cleanup_swap_failed",
                issue_url=entry.issue_url,
                exc_info=True,
            )


async def sweep_stale_dispatch_labels(
    campaign_state_paths: list[Path],
    github_client: GitHubFetcher | None,
) -> None:
    """Startup sweep: clean up labels for dead dispatches across all campaigns.

    Pass 1: dead RUNNING dispatches (process died mid-run).
    Pass 2: terminal dispatches with labels_cleaned=False (cleanup was missed).

    Called as a background task from _fleet_auto_gate_boot. Errors on individual
    state files are logged and skipped so one corrupt file cannot block recovery.
    """
    _TERMINAL_UNCLEANED = frozenset({DispatchStatus.FAILURE, DispatchStatus.INTERRUPTED})
    for state_path in campaign_state_paths:
        stale_sidecar_paths: list[str | None] = []
        terminal_uncleaned: list[tuple[str, str | None]] = []
        try:
            with CampaignStateMutator(state_path) as m:
                if m.state is None:
                    continue
                for d in m.state.dispatches:
                    if d.status == DispatchStatus.RUNNING:
                        if is_dispatch_session_alive(d):
                            continue
                        stale_sidecar_paths.append(d.sidecar_path)
                        d.status = DispatchStatus.INTERRUPTED
                        m.mark_dirty()
                    elif (
                        d.status in _TERMINAL_UNCLEANED
                        and not d.labels_cleaned
                        and d.sidecar_path is not None
                    ):
                        terminal_uncleaned.append((d.name, d.sidecar_path))
        except Exception:
            logger.warning(
                "startup_label_sweep_failed",
                state_path=str(state_path),
                exc_info=True,
            )
            continue
        for sp in stale_sidecar_paths:
            await cleanup_orphaned_labels(sp, github_client)
        for _name, sp in terminal_uncleaned:
            await cleanup_orphaned_labels(sp, github_client)
        if terminal_uncleaned:
            try:
                with CampaignStateMutator(state_path) as m:
                    if m.state is not None:
                        cleaned_names = {name for name, _ in terminal_uncleaned}
                        for d in m.state.dispatches:
                            if d.name in cleaned_names:
                                d.labels_cleaned = True
                                m.mark_dirty()
            except Exception:
                logger.warning(
                    "startup_label_sweep_mark_cleaned_failed",
                    state_path=str(state_path),
                    exc_info=True,
                )


def discover_campaign_state_files(project_dir: Path) -> list[Path]:
    """Return all campaign state JSON files in the dispatches directory."""
    dispatches_dir = project_dir / ".autoskillit" / "temp" / "dispatches"
    if not dispatches_dir.exists():
        return []
    return list(dispatches_dir.glob("*.json"))
