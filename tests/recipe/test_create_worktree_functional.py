"""Functional tests for scripts/recipe/create_worktree.sh.

These tests actually execute the shell script against real temporary git repos,
unlike the static-analysis tests in test_research_context_tracking.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoskillit.core.paths import pkg_root

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

SCRIPTS_DIR = pkg_root().parent.parent / "scripts" / "recipe"
CREATE_WORKTREE_SCRIPT = SCRIPTS_DIR / "create_worktree.sh"


def _run_create_worktree(
    source_dir: Path,
    experiment_plan: Path | None = None,
    temp_name: str = ".autoskillit/temp",
) -> subprocess.CompletedProcess:
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
    )
    return result


class TestCreateWorktreeFunctional:
    def test_worktree_created_as_sibling_of_project_root(self, tmp_path: Path) -> None:
        """Worktree is created at <tmp_path>/worktrees/<branch>, NOT inside source_dir."""
        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()

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
        # Set up main repo
        main = tmp_path / "main_repo"
        main.mkdir()
        (main / ".git").mkdir()

        # Create first worktree at main/worktrees/wt1
        wt1_dir = tmp_path / "worktrees" / "wt1"
        wt1_dir.mkdir(parents=True)
        # Simulate worktree .git file pointing back to main
        (wt1_dir / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt1'}\n")

        # Register the worktree in main's worktrees list
        worktrees_dir = main / ".git" / "worktrees"
        worktrees_dir.mkdir(parents=True)
        wt1_gitdir = worktrees_dir / "wt1"
        wt1_gitdir.mkdir()

        # Now run create_worktree.sh with wt1_dir as source
        result = _run_create_worktree(wt1_dir)

        assert result.returncode == 0, f"Script failed: {result.stderr}"

        # The new worktree should be at tmp_path/worktrees/research-*, NOT nested
        worktrees_dir = tmp_path / "worktrees"
        new_worktrees = [d for d in worktrees_dir.glob("research-*") if d != wt1_dir]
        assert len(new_worktrees) == 1, f"Expected one new worktree, found: {new_worktrees}"

        new_wt = new_worktrees[0]
        assert not any("worktrees/worktrees" in str(p) for p in new_wt.parents), (
            f"Worktree path contains nested worktrees/: {new_wt}"
        )
        # Verify it does NOT live inside wt1_dir
        assert not str(new_wt).startswith(str(wt1_dir)), (
            f"New worktree should not be inside source worktree: {new_wt}"
        )

    def test_git_init_guard_does_not_trigger_for_worktree(self, tmp_path: Path) -> None:
        """Worktree .git is a FILE (not dir) — git init must NOT be called."""
        main = tmp_path / "main_repo"
        main.mkdir()
        (main / ".git").mkdir()

        # Create worktree at worktrees/wt1
        wt1_dir = tmp_path / "worktrees" / "wt1"
        wt1_dir.mkdir(parents=True)
        (wt1_dir / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt1'}\n")

        # Register in main's worktrees list
        worktrees_dir = main / ".git" / "worktrees"
        worktrees_dir.mkdir(parents=True)
        wt1_gitdir = worktrees_dir / "wt1"
        wt1_gitdir.mkdir()

        # Check stderr for "Initialized empty Git repository" — should NOT appear
        result = _run_create_worktree(wt1_dir)

        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "Initialized empty Git repository" not in result.stderr, (
            "git init should NOT run when .git file exists (worktree source)"
        )

    def test_worktree_path_is_absolute_in_output(self, tmp_path: Path) -> None:
        """stdout contains worktree_path=<absolute_path> without nested worktrees/."""
        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()

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
        main.mkdir()
        (main / ".git").mkdir()

        # Create worktree at worktrees/wt1
        wt1_dir = tmp_path / "worktrees" / "wt1"
        wt1_dir.mkdir(parents=True)
        (wt1_dir / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt1'}\n")

        # Register in main's worktrees list
        worktrees_dir = main / ".git" / "worktrees"
        worktrees_dir.mkdir(parents=True)
        wt1_gitdir = worktrees_dir / "wt1"
        wt1_gitdir.mkdir()

        result = _run_create_worktree(wt1_dir)

        assert result.returncode == 0, f"Script failed: {result.stderr}"

        # Find the created worktree
        worktrees_dir = tmp_path / "worktrees"
        new_worktrees = [d for d in worktrees_dir.glob("research-*") if d != wt1_dir]
        assert len(new_worktrees) == 1

        # The sidecar should be under main_repo, not inside wt1_dir
        expected_sidecar = main / ".autoskillit" / "temp" / "worktrees"
        base_branch_files = list(expected_sidecar.glob("*/base-branch"))
        assert len(base_branch_files) >= 1, (
            f"base-branch should exist under main root's temp dir: {expected_sidecar}"
        )

        # Verify no base-branch was written inside wt1_dir
        wt1_sidecar = wt1_dir / ".autoskillit" / "temp" / "worktrees"
        assert not wt1_sidecar.exists(), (
            f"base-branch should NOT be inside source worktree: {wt1_sidecar}"
        )
