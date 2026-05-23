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
from autoskillit.fleet.sidecar import SidecarReadStatus, read_sidecar_from_path
from autoskillit.fleet.state import (
    TERMINAL_UNCLEANED_STATUSES,
    CampaignStateMutator,
    DispatchStatus,
)

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
) -> bool:
    """Remove in-progress labels for all issues in a dispatch sidecar.

    Safe to call unconditionally from a finally block — returns immediately when
    sidecar_path or github_client is None, and swallows all errors so the
    original exception is never suppressed.

    Returns True when all swap_labels calls succeeded (or there was nothing to
    clean). Returns False when any call failed or raised.
    """
    if sidecar_path is None or github_client is None:
        logger.debug(
            "infra_label_cleanup_skipped",
            reason="no_sidecar_or_no_client",
            has_sidecar=sidecar_path is not None,
        )
        return True

    try:
        sidecar_result = read_sidecar_from_path(Path(sidecar_path))
    except Exception:
        logger.warning(
            "infra_label_cleanup_sidecar_read_failed",
            sidecar_path=sidecar_path,
            exc_info=True,
        )
        return False

    if sidecar_result.source != SidecarReadStatus.FOUND:
        logger.warning(
            "infra_label_cleanup_sidecar_unavailable",
            sidecar_path=sidecar_path,
            source=sidecar_result.source,
        )
        return False

    if not sidecar_result.entries:
        return True

    all_succeeded = True
    for entry in sidecar_result.entries:
        try:
            owner, repo, number = _parse_issue_ref(entry.issue_url)
        except ValueError:
            logger.warning(
                "infra_label_cleanup_skip_bad_url",
                issue_url=entry.issue_url,
            )
            all_succeeded = False
            continue
        try:
            result = await github_client.swap_labels(
                owner,
                repo,
                number,
                remove_labels=_REMOVE_LABELS,
                add_labels=_ADD_LABELS,
            )
            if not result.get("success"):
                all_succeeded = False
            logger.info(
                "infra_label_cleanup",
                issue_url=entry.issue_url,
                success=result.get("success"),
            )
        except Exception:
            all_succeeded = False
            logger.warning(
                "infra_label_cleanup_swap_failed",
                issue_url=entry.issue_url,
                exc_info=True,
            )
    return all_succeeded


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
    for state_path in campaign_state_paths:
        stale_dispatches: list[tuple[str, str | None]] = []
        terminal_uncleaned: list[tuple[str, str | None]] = []
        try:
            with CampaignStateMutator(state_path) as m:
                if m.state is None:
                    continue
                for d in m.state.dispatches:
                    if d.status == DispatchStatus.RUNNING:
                        if is_dispatch_session_alive(d):
                            continue
                        stale_dispatches.append((d.name, d.sidecar_path))
                        d.status = DispatchStatus.INTERRUPTED
                        m.mark_dirty()
                    elif (
                        d.status in TERMINAL_UNCLEANED_STATUSES
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

        cleanup_results: dict[str, bool] = {}
        for name, sp in stale_dispatches:
            cleanup_results[name] = await cleanup_orphaned_labels(sp, github_client)
        for name, sp in terminal_uncleaned:
            cleanup_results[name] = await cleanup_orphaned_labels(sp, github_client)

        if cleanup_results:
            try:
                with CampaignStateMutator(state_path) as m:
                    if m.state is not None:
                        for d in m.state.dispatches:
                            if d.name in cleanup_results and cleanup_results[d.name]:
                                d.labels_cleaned = True
                                m.mark_dirty()
            except Exception:
                logger.warning(
                    "startup_label_sweep_mark_cleaned_failed",
                    state_path=str(state_path),
                    cleaned_names=sorted(n for n, s in cleanup_results.items() if s),
                    exc_info=True,
                )


def discover_campaign_state_files(project_dir: Path) -> list[Path]:
    """Return all campaign state JSON files in the dispatches directory."""
    dispatches_dir = project_dir / ".autoskillit" / "temp" / "dispatches"
    if not dispatches_dir.exists():
        return []
    return list(dispatches_dir.glob("*.json"))
