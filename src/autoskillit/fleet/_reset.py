"""Dispatch artifact reset — full cleanup of git/PR artifacts for failed L2 sessions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    LABEL_LIFECYCLE_REGISTRY,
    IssueLabelState,
    _parse_issue_ref,
    get_logger,
)
from autoskillit.fleet.sidecar import SidecarReadStatus, read_sidecar_from_path
from autoskillit.fleet.state import (
    CampaignStateMutator,
    DispatchStatus,
    read_state,
    reset_blocking_dispatch,
)

if TYPE_CHECKING:
    from autoskillit.core import GitHubFetcher, SubprocessRunner
    from autoskillit.fleet.state_types import DispatchRecord

logger = get_logger(__name__)

__all__ = [
    "ResetReport",
    "find_dispatch_in_campaigns",
    "compute_reset_labels",
    "reset_dispatch_artifacts",
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
    labels_reset: bool = False
    worktree_removed: bool = False
    sidecar_removed: bool = False
    local_branch_deleted: bool = False
    remote_branch_deleted: bool = False
    prs_closed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    state_updated: bool = False


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


async def reset_dispatch_artifacts(
    dispatch: DispatchRecord,
    *,
    project_dir: Path,
    worktrees_dir: Path,
    runner: SubprocessRunner,
    github_client: GitHubFetcher | None,
    target_state: IssueLabelState,
) -> ResetReport:
    report = ResetReport(dispatch_name=dispatch.name, branch_name=dispatch.name)
    remove_labels, add_labels = compute_reset_labels(target_state)

    if dispatch.sidecar_path is None or github_client is None:
        report.labels_reset = True
    else:
        try:
            sidecar_result = read_sidecar_from_path(Path(dispatch.sidecar_path))
        except Exception as exc:
            report.errors.append(f"sidecar_read: {exc}")
            sidecar_result = None

        if sidecar_result is not None and sidecar_result.source == SidecarReadStatus.FOUND:
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
                except Exception as exc:
                    report.errors.append(f"swap_labels({entry.issue_url}): {exc}")
                    all_ok = False
            report.labels_reset = all_ok

    worktree_path = worktrees_dir / dispatch.name
    try:
        from autoskillit.workspace.worktree import remove_git_worktree

        wt_result = await remove_git_worktree(worktree_path, project_dir, runner)
        report.worktree_removed = bool(wt_result.deleted) or bool(wt_result.skipped)
    except Exception as exc:
        report.errors.append(f"remove_worktree: {exc}")

    try:
        from autoskillit.workspace.worktree import remove_worktree_sidecar

        sc_result = remove_worktree_sidecar(project_dir, dispatch.name)
        report.sidecar_removed = bool(sc_result.deleted) or bool(sc_result.skipped)
    except Exception as exc:
        report.errors.append(f"remove_sidecar: {exc}")

    pr_urls: list[str] = []
    if dispatch.sidecar_path is not None:
        try:
            sr = read_sidecar_from_path(Path(dispatch.sidecar_path))
            if sr.source == SidecarReadStatus.FOUND:
                pr_urls = [e.pr_url for e in sr.entries if e.pr_url is not None]
        except Exception as exc:
            report.errors.append(f"sidecar_pr_read: {exc}")

    if not pr_urls and dispatch.sidecar_path is not None:
        try:
            gh_result = await runner(
                ["gh", "pr", "list", "--head", dispatch.name, "--json", "url", "--limit", "5"],
                cwd=project_dir,
                timeout=15,
            )
            if gh_result.returncode == 0 and gh_result.stdout:
                parsed = json.loads(gh_result.stdout)
                pr_urls = [item["url"] for item in parsed if "url" in item]
        except Exception as exc:
            report.errors.append(f"pr_fallback_search: {exc}")

    for pr_url in pr_urls:
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
            report.errors.append(f"pr_close({pr_url}): {exc}")

    try:
        branch_result = await runner(
            ["git", "-C", str(project_dir), "branch", "-D", dispatch.name],
            cwd=project_dir,
            timeout=10,
        )
        report.local_branch_deleted = branch_result.returncode == 0
    except Exception as exc:
        report.errors.append(f"local_branch_delete: {exc}")

    try:
        push_result = await runner(
            ["git", "-C", str(project_dir), "push", "origin", "--delete", dispatch.name],
            cwd=project_dir,
            timeout=30,
        )
        report.remote_branch_deleted = push_result.returncode == 0
    except Exception as exc:
        report.errors.append(f"remote_branch_delete: {exc}")

    return report


async def update_campaign_state(
    dispatch_name: str, state_path: Path, *, reset_to_queued: bool
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
                    d.labels_cleaned = True
                    m.mark_dirty()
                    return True
        return False
    except Exception as exc:
        logger.warning("reset_dispatch_labels_mark_failed", error=str(exc))
        return False
