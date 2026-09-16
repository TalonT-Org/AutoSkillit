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


def _git_run(repo_path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run git in a real isolated repository with commit identity configured."""
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        check=check,
        env={
            **_DZC_GIT_ENV,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        },
        text=True,
    )


def test_check_ref_state_reads_pushed_remote_not_isolated_origin(
    remote_only_base_clone: tuple[Path, str],
) -> None:
    """The authoritative probe reads upstream, not a stale isolated-origin ref."""
    from autoskillit.smoke_utils import check_ref_state

    clone_path, _ = remote_only_base_clone
    _git_run(clone_path, "commit", "--allow-empty", "-m", "local feature work")
    _git_run(clone_path, "push", "upstream", "feat")
    _git_run(clone_path, "update-ref", "-d", "refs/remotes/origin/feat")

    assert check_ref_state(str(clone_path), "feat") == {
        "remote_ref_state": "present",
        "remote_is_ancestor": "true",
    }


def test_check_ref_state_genuine_divergence_returns_false(
    remote_only_base_clone: tuple[Path, str],
) -> None:
    """A direct remote probe detects independently advanced remote history."""
    from autoskillit.smoke_utils import check_ref_state

    clone_path, upstream_url = remote_only_base_clone
    _git_run(clone_path, "commit", "--allow-empty", "-m", "local advance")

    writer_path = clone_path.parent / "upstream-writer"
    _git_run(clone_path.parent, "clone", "--branch", "feat", upstream_url, str(writer_path))
    _git_run(writer_path, "commit", "--allow-empty", "-m", "remote advance")
    _git_run(writer_path, "push", "origin", "feat")

    assert check_ref_state(str(clone_path), "feat") == {
        "remote_ref_state": "present",
        "remote_is_ancestor": "false",
    }


def test_check_ref_state_missing_branch_is_unknown(
    remote_only_base_clone: tuple[Path, str],
) -> None:
    """A missing qualified local branch cannot make a remote-state claim."""
    from autoskillit.smoke_utils import check_ref_state

    clone_path, _ = remote_only_base_clone

    assert check_ref_state(str(clone_path), "nonexistent") == {
        "remote_ref_state": "unknown",
        "remote_is_ancestor": "false",
    }


def test_check_ref_state_absent_remote_ref_returns_absent(
    remote_only_base_clone: tuple[Path, str],
) -> None:
    """A local branch missing from the network remote is explicitly absent."""
    from autoskillit.smoke_utils import check_ref_state

    clone_path, _ = remote_only_base_clone
    _git_run(clone_path, "checkout", "-b", "unpublished")

    assert check_ref_state(str(clone_path), "unpublished") == {
        "remote_ref_state": "absent",
        "remote_is_ancestor": "false",
    }


def test_check_ref_state_remote_probe_failure_is_not_absence(
    remote_only_base_clone: tuple[Path, str],
) -> None:
    """An ls-remote transport failure is unknown rather than an absent ref."""
    from autoskillit.smoke_utils import check_ref_state

    clone_path, _ = remote_only_base_clone
    _git_run(clone_path, "remote", "set-url", "upstream", "invalid://unreachable")

    assert check_ref_state(str(clone_path), "feat") == {
        "remote_ref_state": "unknown",
        "remote_is_ancestor": "false",
    }
