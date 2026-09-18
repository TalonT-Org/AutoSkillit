"""Real-repository coverage for the bounded self-revert detector."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from autoskillit.execution.process import DefaultSubprocessRunner
from autoskillit.server.git import detect_self_reverts

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.local",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.local",
}


def _git(repo: Path, *args: str) -> str:
    """Run Git in the fixture repository and return its standard output."""
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    ).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    """Commit every fixture change and return the resulting object ID."""
    _git(repo, "add", "--all")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def self_revert_repo(tmp_path: Path) -> Path:
    """Create an isolated repository with one committed source file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@test.local")
    _git(repo, "config", "user.name", "Test")
    (repo / "target.py").write_text("def configured_limit() -> int:\n    return 1024\n")
    _commit(repo, "test: establish base")
    return repo


@pytest.mark.anyio
async def test_detect_self_reverts_finds_inverse_after_unrelated_line_shift(
    self_revert_repo: Path,
) -> None:
    """A true inverse remains detectable when an unrelated insertion moves it."""
    base_ref = _git(self_revert_repo, "rev-parse", "HEAD")
    target = self_revert_repo / "target.py"
    original = target.read_text()
    target.write_text(original + "\nSAFETY_MARGIN = 64\n")
    added_sha = _commit(self_revert_repo, "feat: add safety margin")

    (self_revert_repo / "notes.txt").write_text("unrelated change\n")
    _commit(self_revert_repo, "docs: add unrelated note")
    target.write_text("# unrelated preface\n" + target.read_text())
    _commit(self_revert_repo, "style: shift target line")
    target.write_text("# unrelated preface\n" + original)
    reverted_sha = _commit(self_revert_repo, "fix: remove safety margin")

    result = await detect_self_reverts(
        DefaultSubprocessRunner(),
        str(self_revert_repo),
        base_ref,
        "HEAD",
    )

    assert result["complete"] is True
    pair = next(
        pair
        for pair in result["pairs"]
        if pair["earlier_sha"] == added_sha and pair["later_sha"] == reverted_sha
    )
    assert pair["reverted_hunks"]


@pytest.mark.anyio
async def test_detect_self_reverts_ignores_later_refinement_of_same_region(
    self_revert_repo: Path,
) -> None:
    """Replacing an earlier value with a new value is not an inverse."""
    base_ref = _git(self_revert_repo, "rev-parse", "HEAD")
    target = self_revert_repo / "target.py"
    target.write_text("def configured_limit() -> int:\n    return 64\n")
    _commit(self_revert_repo, "feat: configure initial limit")
    target.write_text("def configured_limit() -> int:\n    return 96\n")
    _commit(self_revert_repo, "feat: refine configured limit")

    result = await detect_self_reverts(
        DefaultSubprocessRunner(),
        str(self_revert_repo),
        base_ref,
        "HEAD",
    )

    assert result["complete"] is True
    assert result["pairs"] == []


@pytest.mark.anyio
async def test_detect_self_reverts_does_not_cross_match_distinct_reordered_regions(
    self_revert_repo: Path,
) -> None:
    """Opposite text changes need matching context, not merely matching strings."""
    base_ref = _git(self_revert_repo, "rev-parse", "HEAD")
    target = self_revert_repo / "target.py"
    target.write_text(
        "def first_region() -> str:\n"
        "    return 'old'\n\n"
        "def second_region() -> str:\n"
        "    return 'new'\n"
    )
    _commit(self_revert_repo, "test: add distinct regions")
    target.write_text(
        "def first_region() -> str:\n"
        "    return 'new'\n\n"
        "def second_region() -> str:\n"
        "    return 'new'\n"
    )
    _commit(self_revert_repo, "feat: update first region")
    target.write_text(
        "def second_region() -> str:\n"
        "    return 'new'\n\n"
        "def first_region() -> str:\n"
        "    return 'new'\n"
    )
    _commit(self_revert_repo, "refactor: reorder regions")
    target.write_text(
        "def second_region() -> str:\n"
        "    return 'old'\n\n"
        "def first_region() -> str:\n"
        "    return 'new'\n"
    )
    _commit(self_revert_repo, "feat: update second region")

    result = await detect_self_reverts(
        DefaultSubprocessRunner(),
        str(self_revert_repo),
        base_ref,
        "HEAD",
    )

    assert result["complete"] is True
    assert result["pairs"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("boundary", ["rename", "binary", "merge"])
async def test_detect_self_reverts_reports_incomplete_for_unscannable_boundaries(
    self_revert_repo: Path,
    boundary: str,
) -> None:
    """The bounded scan must not claim coverage across an unsupported boundary."""
    base_ref = _git(self_revert_repo, "rev-parse", "HEAD")

    if boundary == "rename":
        _git(self_revert_repo, "mv", "target.py", "renamed_target.py")
        _commit(self_revert_repo, "refactor: rename target")
    elif boundary == "binary":
        binary = self_revert_repo / "payload.bin"
        binary.write_bytes(b"\x00first payload\xff")
        _commit(self_revert_repo, "test: add binary payload")
    else:
        _git(self_revert_repo, "checkout", "-b", "side")
        (self_revert_repo / "side.py").write_text("SIDE = True\n")
        _commit(self_revert_repo, "feat: add side change")
        _git(self_revert_repo, "checkout", "main")
        (self_revert_repo / "main.py").write_text("MAIN = True\n")
        _commit(self_revert_repo, "feat: add main change")
        _git(self_revert_repo, "merge", "--no-ff", "side", "-m", "merge side change")

    result = await detect_self_reverts(
        DefaultSubprocessRunner(),
        str(self_revert_repo),
        base_ref,
        "HEAD",
    )

    assert result["complete"] is False
    assert result["scan_error"]
