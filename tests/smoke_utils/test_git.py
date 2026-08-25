from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from autoskillit.smoke_utils import (
    check_bug_report_non_empty,
)

pytestmark = [pytest.mark.medium]


def test_returns_false_when_bug_report_missing(tmp_path: Path) -> None:
    """Returns {"non_empty": "false"} when bug_report.json does not exist."""
    result = check_bug_report_non_empty(str(tmp_path))
    assert result == {"non_empty": "false"}


def test_returns_false_when_bug_report_empty_array(tmp_path: Path) -> None:
    """Returns {"non_empty": "false"} when bug_report.json contains []."""
    (tmp_path / "bug_report.json").write_text("[]")
    result = check_bug_report_non_empty(str(tmp_path))
    assert result == {"non_empty": "false"}


def test_returns_true_when_bug_report_has_items(tmp_path: Path) -> None:
    """Returns {"non_empty": "true"} when bug_report.json has at least one item."""
    (tmp_path / "bug_report.json").write_text(json.dumps([{"bug": "x"}]))
    result = check_bug_report_non_empty(str(tmp_path))
    assert result == {"non_empty": "true"}


def test_returns_false_when_bug_report_malformed(tmp_path: Path) -> None:
    """Returns {"non_empty": "false"} when bug_report.json contains malformed JSON."""
    (tmp_path / "bug_report.json").write_text("{not valid json")
    result = check_bug_report_non_empty(str(tmp_path))
    assert result == {"non_empty": "false"}


_DZC_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.com",
}


def test_detect_zero_changes_uncommitted_files(tmp_path: Path) -> None:
    """detect_zero_changes returns has_changes=true for uncommitted files."""
    from autoskillit.smoke_utils import detect_zero_changes

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    (tmp_path / "new_file.txt").write_text("content")
    result = detect_zero_changes(str(tmp_path), "HEAD")
    assert result["has_changes"] == "true"
    assert result["has_uncommitted_changes"] == "true"


def test_detect_zero_changes_override_does_not_skip_git_on_clean_tree(tmp_path: Path) -> None:
    """write_evidence_override=true must not short-circuit git verification.

    On a clean repo, override=true forces has_changes=true via OR-combination,
    but the git signals (commit_count, has_uncommitted_changes) must STILL be
    populated — override is an OR-condition, not a bypass.
    """
    from autoskillit.smoke_utils import detect_zero_changes

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    result = detect_zero_changes(str(tmp_path), "HEAD", write_evidence_override="True")
    assert result["has_changes"] == "true"
    assert result["write_evidence_override"] == "true"
    assert result["commit_count"] == "0"
    assert result["has_uncommitted_changes"] == "false"


def test_detect_zero_changes_override_false_with_commits(tmp_path: Path) -> None:
    """write_evidence_override=false reports commits ahead via git."""
    from autoskillit.smoke_utils import detect_zero_changes

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "second"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "third"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    result = detect_zero_changes(str(tmp_path), base_commit, write_evidence_override="false")
    assert result["has_changes"] == "true"
    assert result["commit_count"] == "2"


def test_detect_zero_changes_override_true_with_commits(tmp_path: Path) -> None:
    """Override and commit signals agree without skipping git."""
    from autoskillit.smoke_utils import detect_zero_changes

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "second"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    result = detect_zero_changes(str(tmp_path), base_commit, write_evidence_override="True")
    assert result["has_changes"] == "true"
    assert result["write_evidence_override"] == "true"
    assert result["commit_count"] == "1"


def test_detect_zero_changes_git_error_fallback(tmp_path: Path) -> None:
    """detect_zero_changes returns has_changes=true on git subprocess errors."""
    from autoskillit.smoke_utils import detect_zero_changes

    result = detect_zero_changes(str(tmp_path), "HEAD", write_evidence_override="false")
    assert result["has_changes"] == "true"
    assert "error" in result
    assert result["commit_count"] == "error"
    assert result["has_uncommitted_changes"] == "error"


def test_detect_zero_changes_clean_repo(tmp_path: Path) -> None:
    """detect_zero_changes returns has_changes=false for a clean repo with no commits ahead."""
    from autoskillit.smoke_utils import detect_zero_changes

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    result = detect_zero_changes(str(tmp_path), "HEAD")
    assert result["has_changes"] == "false"
    assert result["commit_count"] == "0"
    assert result["has_uncommitted_changes"] == "false"


def test_check_ref_state_local_ahead_returns_true(tmp_path: Path) -> None:
    """Local branch ahead of remote tracking ref returns remote_is_ancestor=true.

    Constructs a repo with one initial commit (simulating remote), then
    advances a local ``feature`` branch by one commit (local ahead).
    ``check_ref_state`` must detect that origin/feature is an ancestor of
    feature and return ``{"remote_is_ancestor": "true"}``.

    Issue #4274 Part B: this is the benign-exhaustion case — local work is
    audit-approved and trivially push-recoverable; the recipe must route to
    ``register_clone_unconfirmed`` instead of escalating to ``fail``.
    """
    from autoskillit.smoke_utils import check_ref_state

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    # Capture the base tip via HEAD (not by name) — the initial branch name
    # created by ``git init`` depends on ``init.defaultBranch``, which is not
    # guaranteed to be ``main`` in every environment (e.g. CI runners).
    base_tip = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "checkout", "-b", "feature"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "feature work"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    # Simulate a remote tracking ref pointing at the original commit.
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/feature", base_tip],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )

    result = check_ref_state(str(tmp_path), "feature")
    assert result == {"remote_is_ancestor": "true"}


def test_check_ref_state_genuine_divergence_returns_false(tmp_path: Path) -> None:
    """Genuine local/remote divergence returns remote_is_ancestor=false.

    Constructs a repo where ``feature`` and the simulated remote tracking
    ref have both advanced independently from the same base — true
    divergence. ``check_ref_state`` must NOT report ancestor relationship.
    """
    from autoskillit.smoke_utils import check_ref_state

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    base_tip = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()

    subprocess.run(
        ["git", "checkout", "-b", "feature"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "local advance"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )

    # Simulate remote having advanced from the same base as a different branch.
    subprocess.run(
        ["git", "checkout", base_tip],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    subprocess.run(
        ["git", "checkout", "-b", "remote_tip"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "remote advance"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    divergent_remote = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/feature", divergent_remote],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )

    result = check_ref_state(str(tmp_path), "feature")
    assert result == {"remote_is_ancestor": "false"}


def test_check_ref_state_missing_branch_returns_false(tmp_path: Path) -> None:
    """Missing local branch returns remote_is_ancestor=false (no ancestry to test)."""
    from autoskillit.smoke_utils import check_ref_state

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    result = check_ref_state(str(tmp_path), "nonexistent")
    assert result == {"remote_is_ancestor": "false"}
