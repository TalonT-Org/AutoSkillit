"""Session git-evidence authority for headless execution."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from autoskillit.core import (
    EvidenceWorktree,
    EvidenceWorktreeSource,
    WorktreeRecord,
    get_logger,
    is_git_main_checkout,
    is_git_worktree,
    parse_worktree_porcelain,
)
from autoskillit.execution.headless._headless_path_tokens import (
    _extract_worktree_path,
    _normalize_messages,
)

logger = get_logger(__name__)

_LAUNCH_HEAD_UNAVAILABLE = "launch_head_unavailable"
_WORKTREE_LIST_FAILED = "worktree_list_failed"
_TOKEN_IGNORED_LINKED_WORKTREE = "token_ignored:launch_is_linked_worktree"
_TOKEN_REJECTED_NOT_ABSOLUTE = "token_rejected:not_absolute"
_TOKEN_REJECTED_PRUNABLE = "token_rejected:prunable"
_TOKEN_REJECTED_UNKNOWN = "token_rejected:not_a_linked_worktree_of_launch_repo"


@dataclass(frozen=True, slots=True)
class PreSessionGitState:
    launch_cwd: str
    launch_root: str
    launch_kind: Literal["non_git", "main_checkout", "linked_worktree"]
    launch_head: str
    linked_worktree_heads: Mapping[str, str]
    known_commits: frozenset[str]


@dataclass(frozen=True, slots=True)
class SessionGitEvidence:
    worktree: EvidenceWorktree
    git_writes_detected: bool
    loc_baseline_sha: str
    new_worktrees: tuple[WorktreeRecord, ...]


def _read_worktree_records(cwd: str) -> tuple[WorktreeRecord, ...] | None:
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain", "-z"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("worktree_list_failed", cwd=cwd, exc_info=True)
        return None
    if result.returncode != 0:
        return None
    return parse_worktree_porcelain(result.stdout)


def _capture_main_checkout_state(
    launch_root: str,
    launch_head: str,
) -> tuple[dict[str, str], frozenset[str]]:
    linked_worktree_heads: dict[str, str] = {}
    known_commits = {launch_head}
    records = _read_worktree_records(launch_root)
    if records is not None:
        for record in records:
            if record.head:
                known_commits.add(record.head)
            if not record.is_main and not record.prunable:
                linked_worktree_heads[os.path.realpath(record.path)] = record.head

    try:
        refs = subprocess.run(
            [
                "git",
                "for-each-ref",
                "--format=%(objectname) %(*objectname)",
                "refs/heads",
                "refs/remotes",
                "refs/tags",
            ],
            cwd=launch_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("capture_pre_session_git_refs_failed", cwd=launch_root, exc_info=True)
    else:
        if refs.returncode == 0:
            for line in refs.stdout.splitlines():
                fields = line.split()
                if fields:
                    known_commits.add(fields[1] if len(fields) > 1 else fields[0])

    return linked_worktree_heads, frozenset(known_commits)


def _capture_pre_session_git_state(cwd: str) -> PreSessionGitState:
    resolved_cwd = Path(cwd).resolve()
    if is_git_worktree(resolved_cwd):
        launch_kind: Literal["non_git", "main_checkout", "linked_worktree"] = "linked_worktree"
    elif is_git_main_checkout(resolved_cwd):
        launch_kind = "main_checkout"
    else:
        return PreSessionGitState(cwd, str(resolved_cwd), "non_git", "", {}, frozenset())

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel", "HEAD"],
            cwd=str(resolved_cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("capture_pre_session_git_state_failed", cwd=cwd, exc_info=True)
        return PreSessionGitState(cwd, str(resolved_cwd), launch_kind, "", {}, frozenset())
    lines = result.stdout.splitlines()
    if result.returncode != 0 or len(lines) != 2 or not lines[0] or not lines[1]:
        return PreSessionGitState(cwd, str(resolved_cwd), launch_kind, "", {}, frozenset())

    launch_root = os.path.realpath(lines[0])
    launch_head = lines[1]
    if launch_kind == "main_checkout":
        linked_worktree_heads, known_commits = _capture_main_checkout_state(
            launch_root, launch_head
        )
    else:
        linked_worktree_heads = {}
        known_commits = frozenset({launch_head})

    return PreSessionGitState(
        cwd,
        launch_root,
        launch_kind,
        launch_head,
        linked_worktree_heads,
        known_commits,
    )


def _has_git_writes(
    path: str,
    baseline_sha: str,
    known_commits: frozenset[str],
    *,
    new_worktree: bool,
) -> bool:
    if not path or not baseline_sha:
        return False
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if head.returncode != 0:
            return False
        current_head = head.stdout.strip()
        if not current_head:
            return False
        if not new_worktree:
            return current_head != baseline_sha
        stdin = current_head + "\n" + "".join(f"^{sha}\n" for sha in sorted(known_commits))
        reachable = subprocess.run(
            ["git", "rev-list", "--max-count=1", "--stdin"],
            cwd=path,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return reachable.returncode == 0 and bool(reachable.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        logger.debug("observe_session_git_writes_failed", path=path, exc_info=True)
        return False


def _match_main_checkout_token(
    token: str | None,
    launch_root: str,
    post_records: tuple[WorktreeRecord, ...],
) -> tuple[str, WorktreeRecord | None, str]:
    """Return token realpath, its post-session record, and any rejection detail."""
    if token is None:
        return "", None, ""
    if not Path(token).is_absolute():
        return "", None, _TOKEN_REJECTED_NOT_ABSOLUTE

    token_root = os.path.realpath(token)
    matching_record = next(
        (record for record in post_records if os.path.realpath(record.path) == token_root),
        None,
    )
    if matching_record is not None and matching_record.prunable:
        return token_root, matching_record, _TOKEN_REJECTED_PRUNABLE
    if token_root == launch_root or (matching_record is not None and not matching_record.is_main):
        return token_root, matching_record, ""
    return token_root, matching_record, _TOKEN_REJECTED_UNKNOWN


def _token_selected_scope(
    token_root: str,
    matching_record: WorktreeRecord | None,
    pre: PreSessionGitState,
) -> tuple[str, str, bool] | None:
    if matching_record is None or matching_record.is_main or matching_record.prunable:
        return None
    baseline_sha = pre.linked_worktree_heads.get(token_root)
    if baseline_sha is not None:
        return token_root, baseline_sha, False
    return token_root, pre.launch_head, True


def _observe_session_git_evidence(
    pre: PreSessionGitState,
    assistant_messages: Sequence[str],
) -> SessionGitEvidence:
    token = _extract_worktree_path(_normalize_messages(list(assistant_messages)))

    def finish(
        worktree: EvidenceWorktree,
        baseline_sha: str = "",
        *,
        new_scope: bool = False,
        new_worktrees: tuple[WorktreeRecord, ...] = (),
    ) -> SessionGitEvidence:
        logger.info(
            "evidence_worktree_resolved",
            source=worktree.source.value,
            path=worktree.path,
            detail=worktree.detail,
        )
        return SessionGitEvidence(
            worktree=worktree,
            git_writes_detected=_has_git_writes(
                worktree.path,
                baseline_sha,
                pre.known_commits,
                new_worktree=new_scope,
            ),
            loc_baseline_sha=baseline_sha,
            new_worktrees=new_worktrees,
        )

    if pre.launch_kind == "non_git":
        return finish(EvidenceWorktree("", EvidenceWorktreeSource.NON_GIT))
    if not pre.launch_head:
        return finish(
            EvidenceWorktree("", EvidenceWorktreeSource.UNRESOLVED, _LAUNCH_HEAD_UNAVAILABLE)
        )

    if pre.launch_kind == "linked_worktree":
        detail = (
            _TOKEN_IGNORED_LINKED_WORKTREE
            if token is not None and os.path.realpath(token) != pre.launch_root
            else ""
        )
        return finish(
            EvidenceWorktree(pre.launch_root, EvidenceWorktreeSource.LAUNCH_WORKTREE, detail),
            pre.launch_head,
        )

    post_records = _read_worktree_records(pre.launch_root)
    if post_records is None:
        return finish(
            EvidenceWorktree(
                pre.launch_root, EvidenceWorktreeSource.UNRESOLVED, _WORKTREE_LIST_FAILED
            ),
            pre.launch_head,
        )

    new_worktrees = tuple(
        record
        for record in post_records
        if not record.is_main
        and not record.prunable
        and os.path.realpath(record.path) not in pre.linked_worktree_heads
    )
    token_root, matching_record, rejected_detail = _match_main_checkout_token(
        token, pre.launch_root, post_records
    )
    if token_root == pre.launch_root and not rejected_detail:
        return finish(
            EvidenceWorktree(pre.launch_root, EvidenceWorktreeSource.LAUNCH_CHECKOUT),
            pre.launch_head,
            new_worktrees=new_worktrees,
        )
    selected_scope = _token_selected_scope(token_root, matching_record, pre)
    if selected_scope is not None:
        path, baseline_sha, new_scope = selected_scope
        return finish(
            EvidenceWorktree(path, EvidenceWorktreeSource.TOKEN_SELECTED),
            baseline_sha,
            new_scope=new_scope,
            new_worktrees=new_worktrees,
        )

    if len(new_worktrees) == 1:
        recovered = new_worktrees[0]
        return finish(
            EvidenceWorktree(
                os.path.realpath(recovered.path),
                EvidenceWorktreeSource.GIT_DIFF_RECOVERED,
                rejected_detail,
            ),
            pre.launch_head,
            new_scope=True,
            new_worktrees=new_worktrees,
        )
    if len(new_worktrees) > 1:
        return finish(
            EvidenceWorktree(
                pre.launch_root,
                EvidenceWorktreeSource.UNRESOLVED,
                f"ambiguous_new_worktrees:{len(new_worktrees)}",
            ),
            pre.launch_head,
            new_worktrees=new_worktrees,
        )
    return finish(
        EvidenceWorktree(
            pre.launch_root,
            EvidenceWorktreeSource.LAUNCH_CHECKOUT,
            rejected_detail,
        ),
        pre.launch_head,
        new_worktrees=new_worktrees,
    )


def _parse_numstat(numstat_output: str) -> tuple[int, int]:
    """Parse `git diff --numstat` output into (insertions, deletions)."""
    insertions = deletions = 0
    for line in numstat_output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 2:
            continue
        try:
            insertions += int(parts[0])
            deletions += int(parts[1])
        except ValueError:
            continue
    return insertions, deletions


def _compute_loc_changed(evidence: SessionGitEvidence) -> tuple[int, int]:
    """Run git diff against the selected worktree baseline; return zero on failure."""
    path = evidence.worktree.path
    baseline_sha = evidence.loc_baseline_sha
    if not path or not baseline_sha:
        return 0, 0
    try:
        result = subprocess.run(
            ["git", "diff", "--numstat", baseline_sha],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return 0, 0
        return _parse_numstat(result.stdout)
    except (OSError, subprocess.SubprocessError):
        logger.debug("compute_loc_changed_failed", cwd=path, pre_sha=baseline_sha, exc_info=True)
        return 0, 0
