"""Dispatch artifact reset — full cleanup of git/PR artifacts for failed L2 sessions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, assert_never

from autoskillit.core import (
    LABEL_LIFECYCLE_REGISTRY,
    IssueLabelState,
    _parse_issue_ref,
    get_logger,
)
from autoskillit.fleet.sidecar import SidecarReadResult, SidecarReadStatus, read_sidecar_from_path
from autoskillit.fleet.state import (
    CampaignStateMutator,
    DispatchStatus,
    read_state,
    reset_blocking_dispatch,
)
from autoskillit.workspace import (
    WORKTREES_DIR,
    remove_git_worktree,
    remove_worktree_sidecar,
)

if TYPE_CHECKING:
    from autoskillit.core import GitHubFetcher, SubprocessRunner
    from autoskillit.fleet.state_types import DispatchRecord

logger = get_logger(__name__)

__all__ = [
    "ResetReport",
    "find_dispatch_in_campaigns",
    "compute_reset_labels",
    "format_resettable_statuses",
    "reset_dispatch_artifacts",
    "resolve_worktrees_dir",
    "update_campaign_state",
    "_RESETTABLE_STATUSES",
]

_RESETTABLE_STATUSES: frozenset[DispatchStatus] = frozenset(
    {DispatchStatus.FAILURE, DispatchStatus.INTERRUPTED, DispatchStatus.REFUSED}
)


@dataclass
class ResetReport:
    dispatch_name: str = ""
    branch_name: str = ""
    labels_reset: bool | None = None
    worktree_removed: bool = False
    sidecar_removed: bool = False
    local_branch_deleted: bool = False
    remote_branch_deleted: bool = False
    prs_closed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    state_updated: bool = False
    has_protected_artifacts: bool = False
    protected_prs: list[str] = field(default_factory=list)


def find_dispatch_in_campaigns(
    dispatch_id: str, campaign_state_paths: list[Path]
) -> tuple[DispatchRecord, Path] | None:
    for state_path in campaign_state_paths:
        state = read_state(state_path)
        if state is None:
            continue
        for d in state.dispatches:
            if d.dispatch_id and d.dispatch_id == dispatch_id:
                return d, state_path
        for d in state.dispatches:
            if d.name == dispatch_id:
                return d, state_path
    return None


def compute_reset_labels(target_state: IssueLabelState) -> tuple[list[str], list[str]]:
    label_def = LABEL_LIFECYCLE_REGISTRY[target_state]
    remove = sorted(s.value for s in label_def.removes_on_entry | {IssueLabelState.IN_PROGRESS})
    add = [target_state.value]
    return remove, add


def format_resettable_statuses() -> str:
    return str(sorted(s.value for s in _RESETTABLE_STATUSES))


def resolve_worktrees_dir(project_dir: Path, worktree_root: str | None) -> Path:
    if worktree_root:
        return Path(worktree_root)
    return project_dir.parent / WORKTREES_DIR


async def _handle_sidecar_label_swap(
    dispatch: DispatchRecord,
    sidecar_result: SidecarReadResult | None,
    github_client: GitHubFetcher | None,
    remove_labels: list[str],
    add_labels: list[str],
    report: ResetReport,
) -> None:
    if dispatch.sidecar_path is None:
        report.labels_reset = True
        return
    if github_client is None:
        report.labels_reset = False
        report.errors.append("github_client unavailable — label swap skipped")
        return
    if sidecar_result is None:
        report.labels_reset = False
        report.errors.append("sidecar read failed — label swap skipped")
        return

    match sidecar_result.source:
        case SidecarReadStatus.FOUND:
            all_ok = True
            for entry in sidecar_result.entries:
                try:
                    owner, repo, number = _parse_issue_ref(entry.issue_url)
                except ValueError as exc:
                    report.errors.append(f"parse_issue_ref({entry.issue_url}): {exc}")
                    all_ok = False
                    continue
                try:
                    result = await github_client.swap_labels(
                        owner, repo, number, remove_labels=remove_labels, add_labels=add_labels
                    )
                    if not result.get("success"):
                        all_ok = False
                        logger.warning(
                            "swap_labels_unsuccessful", issue=entry.issue_url, result=result
                        )
                        report.errors.append(
                            f"swap_labels_unsuccessful({entry.issue_url}): {result}"
                        )
                except Exception as exc:
                    logger.warning("swap_labels_failed", issue=entry.issue_url, error=str(exc))
                    report.errors.append(f"swap_labels({entry.issue_url}): {exc}")
                    all_ok = False
            report.labels_reset = all_ok
        case SidecarReadStatus.MISSING:
            report.labels_reset = False
            report.errors.append(
                f"sidecar file missing at {dispatch.sidecar_path} — label swap skipped"
            )
        case SidecarReadStatus.ERROR:
            report.labels_reset = False
            report.errors.append(
                f"sidecar file unreadable at {dispatch.sidecar_path} — label swap skipped"
            )
        case _ as unreachable:
            assert_never(unreachable)


async def reset_dispatch_artifacts(
    dispatch: DispatchRecord,
    *,
    project_dir: Path,
    worktrees_dir: Path,
    runner: SubprocessRunner,
    github_client: GitHubFetcher | None,
    target_state: IssueLabelState,
    force: bool = False,
) -> ResetReport:
    report = ResetReport(
        dispatch_name=dispatch.name,
        branch_name=dispatch.branch_name or dispatch.name,
    )
    remove_labels, add_labels = compute_reset_labels(target_state)

    sidecar_result = None
    if dispatch.sidecar_path is not None:
        try:
            sidecar_result = read_sidecar_from_path(Path(dispatch.sidecar_path))
        except Exception as exc:
            logger.warning("sidecar_read_failed", error=str(exc))
            report.errors.append(f"sidecar_read: {exc}")

    await _handle_sidecar_label_swap(
        dispatch, sidecar_result, github_client, remove_labels, add_labels, report
    )

    worktree_path = worktrees_dir / dispatch.name
    try:
        wt_result = await remove_git_worktree(worktree_path, project_dir, runner)
        report.worktree_removed = bool(wt_result.deleted) or bool(wt_result.skipped)
    except Exception as exc:
        logger.warning("remove_worktree_failed", error=str(exc))
        report.errors.append(f"remove_worktree: {exc}")

    try:
        sc_result = remove_worktree_sidecar(project_dir, dispatch.name)
        report.sidecar_removed = bool(sc_result.deleted) or bool(sc_result.skipped)
    except Exception as exc:
        logger.warning("remove_sidecar_failed", error=str(exc))
        report.errors.append(f"remove_sidecar: {exc}")

    pr_urls: list[str] = []
    if sidecar_result is not None and sidecar_result.source == SidecarReadStatus.FOUND:
        pr_urls = [e.pr_url for e in sidecar_result.entries if e.pr_url is not None]

    if not pr_urls and dispatch.sidecar_path is not None:
        for _head_name in dict.fromkeys(
            [dispatch.name]
            + (
                [dispatch.branch_name]
                if dispatch.branch_name and dispatch.branch_name != dispatch.name
                else []
            )
        ):
            try:
                gh_result = await runner(
                    ["gh", "pr", "list", "--head", _head_name, "--json", "url", "--limit", "5"],
                    cwd=project_dir,
                    timeout=15,
                )
                if gh_result.returncode == 0 and gh_result.stdout:
                    parsed = json.loads(gh_result.stdout)
                    pr_urls = [item["url"] for item in parsed if "url" in item]
                    if pr_urls:
                        break
            except Exception as exc:
                logger.warning("pr_fallback_search_failed", error=str(exc))
                report.errors.append(f"pr_fallback_search: {exc}")

    for pr_url in pr_urls:
        if not force:
            try:
                _view_result = await runner(
                    ["gh", "pr", "view", pr_url, "--json", "reviewDecision,state"],
                    cwd=project_dir,
                    timeout=15,
                )
                if _view_result.returncode == 0 and _view_result.stdout:
                    _pr_data = json.loads(_view_result.stdout)
                    _is_open = _pr_data.get("state") == "OPEN"
                    _review = _pr_data.get("reviewDecision", "")
                    if _is_open and _review in ("APPROVED", "CHANGES_REQUESTED"):
                        report.has_protected_artifacts = True
                        report.protected_prs.append(pr_url)
                        continue
            except Exception:
                logger.warning("pr_protection_check_failed", pr_url=pr_url, exc_info=True)
                report.has_protected_artifacts = True
                report.protected_prs.append(pr_url)
                continue
        try:
            close_result = await runner(
                [
                    "gh",
                    "pr",
                    "close",
                    pr_url,
                    "--comment",
                    "Cleaned up by reset_dispatch — dispatch artifacts removed.",
                ],
                cwd=project_dir,
                timeout=15,
            )
            if close_result.returncode == 0:
                report.prs_closed.append(pr_url)
            else:
                report.errors.append(f"pr_close({pr_url}): {close_result.stderr}")
        except Exception as exc:
            logger.warning("pr_close_failed", pr_url=pr_url, error=str(exc))
            report.errors.append(f"pr_close({pr_url}): {exc}")

    try:
        branch_result = await runner(
            ["git", "-C", str(project_dir), "branch", "-D", dispatch.name],
            cwd=project_dir,
            timeout=10,
        )
        report.local_branch_deleted = branch_result.returncode == 0
    except Exception as exc:
        logger.warning("local_branch_delete_failed", error=str(exc))
        report.errors.append(f"local_branch_delete: {exc}")

    try:
        push_result = await runner(
            ["git", "-C", str(project_dir), "push", "origin", "--delete", dispatch.name],
            cwd=project_dir,
            timeout=30,
        )
        report.remote_branch_deleted = push_result.returncode == 0
    except Exception as exc:
        logger.warning("remote_branch_delete_failed", error=str(exc))
        report.errors.append(f"remote_branch_delete: {exc}")

    return report


async def update_campaign_state(
    dispatch_name: str, state_path: Path, *, reset_to_queued: bool, labels_reset: bool = False
) -> bool:
    if reset_to_queued:
        try:
            return reset_blocking_dispatch(state_path, dispatch_name)
        except OSError as exc:
            logger.warning(
                "reset_dispatch_write_failed", state_path=str(state_path), error=str(exc)
            )
            return False

    try:
        with CampaignStateMutator(state_path) as m:
            if m.state is None:
                return False
            for d in m.state.dispatches:
                if d.name == dispatch_name:
                    d.labels_cleaned = labels_reset
                    m.mark_dirty()
                    return True
        return False
    except Exception as exc:
        logger.warning("reset_dispatch_labels_mark_failed", error=str(exc))
        return False
