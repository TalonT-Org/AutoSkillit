"""Real-Git tests for pre-session state capture and evidence selection."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from autoskillit.core import EvidenceWorktreeSource
from autoskillit.execution.headless._headless_git import (
    _capture_pre_session_git_state,
    _compute_loc_changed,
    _observe_session_git_evidence,
)
from tests._git_topology import (
    _run_git as _git,
)
from tests._git_topology import (
    add_linked_worktree,
    commit_file,
    head,
    init_checkout,
    init_empty_checkout,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def test_capture_pre_session_git_state_classifies_launch_topologies(tmp_path: Path) -> None:
    non_git = tmp_path / "ordinary"
    non_git.mkdir()
    non_git_state = _capture_pre_session_git_state(str(non_git))
    assert non_git_state.launch_kind == "non_git"
    assert non_git_state.launch_cwd == str(non_git)

    main = init_checkout(tmp_path / "clone")
    worktree = add_linked_worktree(main, "existing")
    main_state = _capture_pre_session_git_state(str(main))
    assert main_state.launch_kind == "main_checkout"
    assert main_state.launch_root == os.path.realpath(main)
    assert main_state.launch_head == head(main)
    assert main_state.linked_worktree_heads == {os.path.realpath(worktree): head(worktree)}

    linked_state = _capture_pre_session_git_state(str(worktree))
    assert linked_state.launch_kind == "linked_worktree"
    assert linked_state.launch_root == os.path.realpath(worktree)
    assert linked_state.launch_head == head(worktree)

    subdirectory = main / "nested" / "launch"
    subdirectory.mkdir(parents=True)
    subdirectory_state = _capture_pre_session_git_state(str(subdirectory))
    assert subdirectory_state.launch_kind == "main_checkout"
    assert subdirectory_state.launch_cwd == str(subdirectory)
    assert subdirectory_state.launch_root == os.path.realpath(main)


def test_capture_pre_session_git_state_collects_known_ref_tips_and_peeled_tags(
    tmp_path: Path,
) -> None:
    source = init_checkout(tmp_path / "source")
    tagged_commit = head(source)
    _git("tag", "-a", "release-tag", "-m", "release", tagged_commit, cwd=source)
    _git("branch", "side-tip", tagged_commit, cwd=source)
    main_tip = commit_file(source, "main.py", "main = True\n", "main tip")

    first_clone = tmp_path / "first-clone"
    second_clone = tmp_path / "second-clone"
    _git("clone", "--no-local", str(source), str(first_clone))
    _git("clone", "--no-local", str(first_clone), str(second_clone))
    _git("branch", "local-side-tip", tagged_commit, cwd=second_clone)

    state = _capture_pre_session_git_state(str(second_clone))
    default_branch = _git("branch", "--show-current", cwd=source).stdout.strip()
    remote_tip = _git(
        "rev-parse", f"refs/remotes/origin/{default_branch}", cwd=second_clone
    ).stdout.strip()
    branch_tip = _git("rev-parse", "refs/heads/local-side-tip", cwd=second_clone).stdout.strip()
    peeled_tag = _git("rev-parse", "refs/tags/release-tag^{}", cwd=second_clone).stdout.strip()

    assert state.launch_kind == "main_checkout"
    assert main_tip in state.known_commits
    assert remote_tip in state.known_commits
    assert branch_tip in state.known_commits
    assert peeled_tag == tagged_commit
    assert peeled_tag in state.known_commits


def test_prunable_worktree_is_not_a_baseline_or_recovered_worktree(tmp_path: Path) -> None:
    repo = init_checkout(tmp_path / "clone")
    prunable = add_linked_worktree(repo, "vanished")
    shutil.rmtree(prunable)

    pre = _capture_pre_session_git_state(str(repo))
    evidence = _observe_session_git_evidence(pre, [f"worktree_path = {prunable}"])

    assert os.path.realpath(prunable) not in pre.linked_worktree_heads
    assert evidence.new_worktrees == ()
    assert evidence.worktree.source is EvidenceWorktreeSource.LAUNCH_CHECKOUT
    assert evidence.worktree.path == os.path.realpath(repo)
    assert evidence.worktree.detail == "token_rejected:prunable"
    assert evidence.git_writes_detected is False


def test_unborn_head_is_empty_and_resolves_as_unavailable(tmp_path: Path) -> None:
    repo = init_empty_checkout(tmp_path / "empty")
    pre = _capture_pre_session_git_state(str(repo))
    evidence = _observe_session_git_evidence(pre, [])

    assert pre.launch_head == ""
    assert evidence.worktree.source is EvidenceWorktreeSource.UNRESOLVED
    assert evidence.worktree.path == ""
    assert evidence.worktree.detail == "launch_head_unavailable"
    assert evidence.git_writes_detected is False


def test_symlinked_launch_cwd_is_classified_by_resolved_checkout(tmp_path: Path) -> None:
    repo = init_checkout(tmp_path / "clone")
    symlink = tmp_path / "launch-alias"
    symlink.symlink_to(repo, target_is_directory=True)

    state = _capture_pre_session_git_state(str(symlink))

    assert state.launch_cwd == str(symlink)
    assert state.launch_kind == "main_checkout"
    assert state.launch_root == os.path.realpath(repo)


def test_symlinked_token_selects_worktree_by_realpath_and_uses_its_loc_baseline(
    tmp_path: Path,
) -> None:
    repo = init_checkout(tmp_path / "clone")
    pre = _capture_pre_session_git_state(str(repo))
    worktree = add_linked_worktree(repo, "created")
    commit_file(worktree, "feature.py", "value = 1\n" * 5, "feature commit")
    alias = tmp_path / "worktree-alias"
    alias.symlink_to(worktree, target_is_directory=True)

    evidence = _observe_session_git_evidence(pre, [f"worktree_path = {alias}"])
    insertions, deletions = _compute_loc_changed(evidence)

    assert evidence.worktree.source is EvidenceWorktreeSource.TOKEN_SELECTED
    assert evidence.worktree.path == os.path.realpath(worktree)
    assert evidence.loc_baseline_sha == pre.launch_head
    assert evidence.git_writes_detected is True
    assert insertions > 0
    assert deletions == 0
