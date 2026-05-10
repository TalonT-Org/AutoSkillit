"""Functional tests for scripts/recipe/create_worktree.sh.

These tests actually execute the shell script against real temporary git repos,
unlike the static-analysis tests in test_research_context_tracking.py.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from autoskillit.core.paths import pkg_root

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

SCRIPTS_DIR = pkg_root().parent.parent / "scripts" / "recipe"
CREATE_WORKTREE_SCRIPT = SCRIPTS_DIR / "create_worktree.sh"

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.com",
}


def _git(*args: str) -> None:
    subprocess.run(["git", *args], capture_output=True, text=True, check=True, env=_GIT_ENV)


def _init_repo(path: Path) -> None:
    """Create a real git repo with an initial commit."""
    path.mkdir(parents=True, exist_ok=True)
    _git("init", str(path))
    _git("-C", str(path), "commit", "--allow-empty", "-m", "init")


def _run_create_worktree(
    source_dir: Path,
    experiment_plan: Path | None = None,
    temp_name: str = ".autoskillit/temp",
) -> subprocess.CompletedProcess[str]:
    """Run create_worktree.sh with given source_dir and return the result."""
    if experiment_plan is None:
        experiment_plan = source_dir / "experiment-plan.md"
        experiment_plan.write_text("# Experiment Plan\n")

    result = subprocess.run(
        [
            "bash",
            str(CREATE_WORKTREE_SCRIPT),
            str(source_dir),
            "test-task",
            str(experiment_plan),
            "",  # scope_report
            "",  # eval_dashboard
            "",  # visualization_plan
            "",  # report_plan
            temp_name,
            "",  # vis_trace_path
        ],
        capture_output=True,
        text=True,
        cwd=str(source_dir),
        env=_GIT_ENV,
    )
    return result


class TestCreateWorktreeFunctional:
    def test_worktree_created_as_sibling_of_project_root(self, tmp_path: Path) -> None:
        """Worktree is created at <tmp_path>/worktrees/<branch>, NOT inside source_dir."""
        project = tmp_path / "project"
        _init_repo(project)

        result = _run_create_worktree(project)

        assert result.returncode == 0, f"Script failed: {result.stderr}"

        worktrees_dir = tmp_path / "worktrees"
        worktree_dirs = list(worktrees_dir.glob("research-*"))
        assert len(worktree_dirs) == 1, f"Expected exactly one worktree, found: {worktree_dirs}"

        worktree_path = worktree_dirs[0]
        assert not str(worktree_path).startswith(str(project)), (
            f"Worktree should not be inside project: {worktree_path}"
        )
        assert not any("worktrees/worktrees" in str(p) for p in worktree_path.parents), (
            f"Worktree path contains nested worktrees/: {worktree_path}"
        )

    def test_worktree_from_worktree_source_dir_resolves_to_main_root(self, tmp_path: Path) -> None:
        """When source_dir is a linked worktree, new worktree goes next to main root."""
        main = tmp_path / "main_repo"
        _init_repo(main)

        wt1_dir = tmp_path / "worktrees" / "wt1"
        _git("-C", str(main), "worktree", "add", "-b", "wt1", str(wt1_dir))

        result = _run_create_worktree(wt1_dir)

        assert result.returncode == 0, f"Script failed: {result.stderr}"

        worktrees_dir = tmp_path / "worktrees"
        new_worktrees = [d for d in worktrees_dir.glob("research-*") if d != wt1_dir]
        assert len(new_worktrees) == 1, f"Expected one new worktree, found: {new_worktrees}"

        new_wt = new_worktrees[0]
        assert not any("worktrees/worktrees" in str(p) for p in new_wt.parents), (
            f"Worktree path contains nested worktrees/: {new_wt}"
        )
        assert not str(new_wt).startswith(str(wt1_dir)), (
            f"New worktree should not be inside source worktree: {new_wt}"
        )

    def test_git_init_guard_does_not_trigger_for_worktree(self, tmp_path: Path) -> None:
        """Worktree .git is a FILE (not dir) -- git init must NOT be called."""
        main = tmp_path / "main_repo"
        _init_repo(main)

        wt1_dir = tmp_path / "worktrees" / "wt1"
        _git("-C", str(main), "worktree", "add", "-b", "wt1", str(wt1_dir))

        result = _run_create_worktree(wt1_dir)

        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "Initialized empty Git repository" not in result.stderr, (
            "git init should NOT run when .git file exists (worktree source)"
        )

    def test_worktree_path_is_absolute_in_output(self, tmp_path: Path) -> None:
        """stdout contains worktree_path=<absolute_path> without nested worktrees/."""
        project = tmp_path / "project"
        _init_repo(project)

        result = _run_create_worktree(project)

        assert result.returncode == 0, f"Script failed: {result.stderr}"

        worktree_line = next(
            (
                line
                for line in result.stdout.strip().splitlines()
                if line.startswith("worktree_path=")
            ),
            None,
        )
        assert worktree_line is not None, f"No worktree_path= in stdout: {result.stdout}"

        worktree_path = Path(worktree_line.split("=", 1)[1])
        assert worktree_path.is_absolute(), f"worktree_path is not absolute: {worktree_path}"
        assert "worktrees/worktrees" not in str(worktree_path), (
            f"worktree_path contains nested worktrees/: {worktree_path}"
        )

    def test_sidecar_base_branch_written_under_main_root(self, tmp_path: Path) -> None:
        """base-branch sidecar is written under main repo root, not inside source worktree."""
        main = tmp_path / "main_repo"
        _init_repo(main)

        wt1_dir = tmp_path / "worktrees" / "wt1"
        _git("-C", str(main), "worktree", "add", "-b", "wt1", str(wt1_dir))

        result = _run_create_worktree(wt1_dir)

        assert result.returncode == 0, f"Script failed: {result.stderr}"

        new_worktrees = [d for d in (tmp_path / "worktrees").glob("research-*") if d != wt1_dir]
        assert len(new_worktrees) == 1

        expected_sidecar = main / ".autoskillit" / "temp" / "worktrees"
        base_branch_files = list(expected_sidecar.glob("*/base-branch"))
        assert len(base_branch_files) >= 1, (
            f"base-branch should exist under main root's temp dir: {expected_sidecar}"
        )

        wt1_sidecar = wt1_dir / ".autoskillit" / "temp" / "worktrees"
        assert not wt1_sidecar.exists(), (
            f"base-branch should NOT be inside source worktree: {wt1_sidecar}"
        )
