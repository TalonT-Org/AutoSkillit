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
from autoskillit.fleet.campaign_state.state import (
    TERMINAL_UNCLEANED_STATUSES,
    CampaignStateMutator,
    DispatchStatus,
)
from autoskillit.fleet.sidecar import SidecarReadStatus, read_sidecar_from_path

if TYPE_CHECKING:
    from autoskillit.core import GitHubFetcher

logger = get_logger(__name__)

_REMOVE_LABELS: list[str] = sorted(
    s.value
    for s in LABEL_LIFECYCLE_REGISTRY[IssueLabelState.FAIL].removes_on_entry
    | {IssueLabelState.IN_PROGRESS}
)
_ADD_LABELS: list[str] = [IssueLabelState.FAIL.value]


async def _cleanup_single_issue(
    github_client: GitHubFetcher,
    issue_url: str,
    *,
    remove_labels: list[str],
    add_labels: list[str],
) -> bool:
    """Swap labels for a single issue URL. Returns True on success."""
    try:
        owner, repo, number = _parse_issue_ref(issue_url)
    except ValueError:
        logger.warning("infra_label_cleanup_skip_bad_url", issue_url=issue_url)
        return False
    try:
        result = await github_client.swap_labels(
            owner, repo, number, remove_labels=remove_labels, add_labels=add_labels
        )
        success = bool(result.get("success"))
        logger.info("infra_label_cleanup", issue_url=issue_url, success=success)
        return success
    except Exception:
        logger.warning("infra_label_cleanup_swap_failed", issue_url=issue_url, exc_info=True)
        return False


async def _cleanup_issue_urls(
    github_client: GitHubFetcher,
    issue_url: str,
    *,
    remove_labels: list[str],
    add_labels: list[str],
) -> bool:
    """Split a CSV issue_url string and call _cleanup_single_issue per URL."""
    urls = [u.strip() for u in issue_url.split(",") if u.strip()]
    if not urls:
        return False
    all_ok = True
    for url in urls:
        if not await _cleanup_single_issue(
            github_client, url, remove_labels=remove_labels, add_labels=add_labels
        ):
            all_ok = False
    return all_ok


def _collect_label_cleanup_candidates(
    state_path: Path,
) -> tuple[list[tuple[str, str | None, str]], list[tuple[str, str | None, str]]] | None:
    """Collect label-cleanup candidates and persist dead RUNNING transitions."""
    stale_dispatches: list[tuple[str, str | None, str]] = []
    terminal_uncleaned: list[tuple[str, str | None, str]] = []
    try:
        with CampaignStateMutator(state_path) as m:
            if m.state is None:
                return stale_dispatches, terminal_uncleaned
            for dispatch in m.state.dispatches:
                if dispatch.status == DispatchStatus.RUNNING:
                    if is_dispatch_session_alive(dispatch):
                        continue
                    stale_dispatches.append(
                        (dispatch.name, dispatch.sidecar_path, dispatch.issue_url)
                    )
                    dispatch.status = DispatchStatus.INTERRUPTED
                    m.mark_dirty()
                elif (
                    dispatch.status in TERMINAL_UNCLEANED_STATUSES
                    and not dispatch.labels_cleaned
                    and (dispatch.sidecar_path is not None or dispatch.issue_url)
                ):
                    terminal_uncleaned.append(
                        (dispatch.name, dispatch.sidecar_path, dispatch.issue_url)
                    )
    except Exception:
        logger.warning(
            "startup_label_sweep_failed",
            state_path=str(state_path),
            exc_info=True,
        )
        return None
    return stale_dispatches, terminal_uncleaned


async def _cleanup_label_candidates(
    stale_dispatches: list[tuple[str, str | None, str]],
    terminal_uncleaned: list[tuple[str, str | None, str]],
    github_client: GitHubFetcher | None,
) -> dict[str, bool]:
    """Clean stale candidates before terminal ones, without holding a state lock."""
    cleanup_results: dict[str, bool] = {}
    for name, sidecar_path, issue_url in stale_dispatches:
        cleanup_results[name] = await cleanup_orphaned_labels(
            sidecar_path, github_client, issue_url=issue_url
        )
    for name, sidecar_path, issue_url in terminal_uncleaned:
        cleanup_results[name] = await cleanup_orphaned_labels(
            sidecar_path, github_client, issue_url=issue_url
        )
    return cleanup_results


def _mark_cleaned_label_candidates(state_path: Path, cleanup_results: dict[str, bool]) -> None:
    """Persist successful label cleanup after all remote operations complete."""
    if not cleanup_results:
        return
    cleaned_names = sorted(name for name, success in cleanup_results.items() if success)
    try:
        with CampaignStateMutator(state_path) as m:
            if m.state is not None:
                for dispatch in m.state.dispatches:
                    if dispatch.name in cleanup_results and cleanup_results[dispatch.name]:
                        dispatch.labels_cleaned = True
                        m.mark_dirty()
    except (OSError, FileNotFoundError, PermissionError) as exc:
        logger.warning(
            "startup_label_sweep_mark_cleaned_failed",
            state_path=str(state_path),
            cleaned_names=cleaned_names,
            error=str(exc),
        )
    except Exception:
        logger.error(
            "startup_label_sweep_mark_cleaned_unexpected",
            state_path=str(state_path),
            cleaned_names=cleaned_names,
            exc_info=True,
        )


async def cleanup_orphaned_labels(
    sidecar_path: str | None,
    github_client: GitHubFetcher | None,
    *,
    issue_url: str = "",
    remove_labels: list[str] | None = None,
    add_labels: list[str] | None = None,
) -> bool:
    """Remove in-progress labels for all issues in a dispatch sidecar.

    Safe to call unconditionally from a finally block — returns immediately when
    sidecar_path or github_client is None, and swallows all errors so the
    original exception is never suppressed.

    Returns True when all swap_labels calls succeeded (or there was nothing to
    clean). Returns False when any call failed or raised.
    """
    if github_client is None:
        logger.debug(
            "infra_label_cleanup_skipped",
            reason="no_client",
        )
        return True

    rl = remove_labels if remove_labels is not None else _REMOVE_LABELS
    al = add_labels if add_labels is not None else _ADD_LABELS

    if sidecar_path is None:
        if issue_url:
            return await _cleanup_issue_urls(
                github_client, issue_url, remove_labels=rl, add_labels=al
            )
        logger.debug(
            "infra_label_cleanup_skipped",
            reason="no_sidecar_or_no_issue_url",
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
        if issue_url:
            return await _cleanup_issue_urls(
                github_client, issue_url, remove_labels=rl, add_labels=al
            )
        return False

    if sidecar_result.source != SidecarReadStatus.FOUND:
        logger.warning(
            "infra_label_cleanup_sidecar_unavailable",
            sidecar_path=sidecar_path,
            source=sidecar_result.source,
        )
        if issue_url:
            return await _cleanup_issue_urls(
                github_client, issue_url, remove_labels=rl, add_labels=al
            )
        return False

    if not sidecar_result.entries:
        return True

    all_succeeded = True
    for entry in sidecar_result.entries:
        if not await _cleanup_single_issue(
            github_client,
            entry.issue_url,
            remove_labels=rl,
            add_labels=al,
        ):
            all_succeeded = False
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
        candidates = _collect_label_cleanup_candidates(state_path)
        if candidates is None:
            continue
        stale_dispatches, terminal_uncleaned = candidates
        cleanup_results = await _cleanup_label_candidates(
            stale_dispatches, terminal_uncleaned, github_client
        )
        _mark_cleaned_label_candidates(state_path, cleanup_results)


def discover_campaign_state_files(project_dir: Path) -> list[Path]:
    """Return all campaign state JSON files in the dispatches directory."""
    dispatches_dir = project_dir / ".autoskillit" / "temp" / "dispatches"
    if not dispatches_dir.exists():
        return []
    return list(dispatches_dir.glob("*.json"))
