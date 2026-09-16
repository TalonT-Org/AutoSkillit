"""Real-repository regressions for server Git ref qualification."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoskillit.execution.process import DefaultSubprocessRunner
from autoskillit.server.git import GitMergeTarget, _verify_merge_target

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


def _git_stdout(repo_path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()


@pytest.mark.anyio
async def test_verify_merge_target_ignores_shadowing_tag(
    remote_only_base_clone: tuple[Path, str],
) -> None:
    """The checked-out local branch wins over a same-named tag."""
    clone_path, _ = remote_only_base_clone
    tag_target = _git_stdout(clone_path, "rev-parse", "refs/heads/feat^")
    subprocess.run(
        ["git", "tag", "feat", tag_target],
        cwd=clone_path,
        capture_output=True,
        check=True,
        text=True,
    )
    branch_sha = _git_stdout(clone_path, "rev-parse", "refs/heads/feat^{commit}")
    tag_sha = _git_stdout(clone_path, "rev-parse", "refs/tags/feat^{commit}")
    assert tag_sha != branch_sha
    assert _git_stdout(clone_path, "rev-parse", "feat^{commit}") == tag_sha

    result = await _verify_merge_target(str(clone_path), "feat", DefaultSubprocessRunner())

    assert isinstance(result, GitMergeTarget)
    assert result.sha == branch_sha
