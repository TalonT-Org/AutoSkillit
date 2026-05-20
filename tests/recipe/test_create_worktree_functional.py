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
from autoskillit.recipe.io import builtin_scripts_dir

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

CREATE_WORKTREE_SCRIPT = builtin_scripts_dir() / "create_worktree.sh"

# create_impl_worktree.sh only exists in the worktree branch.
# Reference it via pkg_root() / "recipes" / "scripts" / "create_impl_worktree.sh"
# as instructed.
CREATE_IMPL_WORKTREE_SCRIPT = pkg_root() / "recipes" / "scripts" / "create_impl_worktree.sh"

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


def _run_impl_create_worktree(
    source_dir: Path,
    worktree_name: str,
    autoskillit_temp: str = ".autoskillit/temp",
) -> subprocess.CompletedProcess[str]:
    """Run create_impl_worktree.sh with given source_dir and return the result.

    Args:
        source_dir: Directory to run the script from (must be an initialized git repo).
        worktree_name: Name for the new worktree.
        autoskillit_temp: Relative path under MAIN_ROOT for sidecar files.
    """
    result = subprocess.run(
        [
            "bash",
            str(CREATE_IMPL_WORKTREE_SCRIPT),
            worktree_name,
            autoskillit_temp,
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
        assert len(new_worktrees) == 1, f"Expected one new worktree, found: {new_worktrees}"

        expected_sidecar = main / ".autoskillit" / "temp" / "worktrees"
        base_branch_files = list(expected_sidecar.glob("*/base-branch"))
        assert len(base_branch_files) >= 1, (
            f"base-branch should exist under main root's temp dir: {expected_sidecar}"
        )

        wt1_sidecar = wt1_dir / ".autoskillit" / "temp" / "worktrees"
        assert not wt1_sidecar.exists(), (
            f"base-branch should NOT be inside source worktree: {wt1_sidecar}"
        )


class TestCreateImplWorktreeFunctional:
    def test_impl_worktree_created_outside_main_root(self, tmp_path: Path) -> None:
        """Worktree lands at <project_root>/../worktrees/<name>, NOT inside project_root."""
        main = tmp_path / "main_repo"
        _init_repo(main)

        result = _run_impl_create_worktree(main, "impl-test-wt")

        assert result.returncode == 0, f"Script failed: {result.stderr}"

        worktrees_dir = tmp_path / "worktrees"
        worktree_dirs = list(worktrees_dir.glob("impl-test-wt"))
        assert len(worktree_dirs) == 1, (
            f"Expected exactly one worktree at {worktrees_dir}, found: {worktree_dirs}"
        )

        worktree_path = worktree_dirs[0]
        assert not str(worktree_path).startswith(str(main)), (
            f"Worktree should not be inside main repo: {worktree_path}"
        )

    def test_impl_worktree_placement_assertion_rejects_nested(self, tmp_path: Path) -> None:
        """Script exits non-zero when called with no args (missing worktree_name and temp_dir)."""
        main = tmp_path / "main_repo"
        _init_repo(main)

        # Run with no arguments — script must exit 1.
        result = subprocess.run(
            ["bash", str(CREATE_IMPL_WORKTREE_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(main),
            env=_GIT_ENV,
        )
        assert result.returncode != 0, (
            f"Expected non-zero exit when called with no args; got {result.returncode}"
        )

        # Verify set -euo pipefail behaviour: missing second arg also fails.
        result2 = subprocess.run(
            ["bash", str(CREATE_IMPL_WORKTREE_SCRIPT), "some-name"],
            capture_output=True,
            text=True,
            cwd=str(main),
            env=_GIT_ENV,
        )
        assert result2.returncode != 0, (
            f"Expected non-zero exit when called with only one arg; got {result2.returncode}"
        )

    def test_impl_worktree_emits_structured_tokens(self, tmp_path: Path) -> None:
        """stdout contains WORKTREE_PATH=, BRANCH_NAME=, and BASE_BRANCH= shell assignments."""
        main = tmp_path / "main_repo"
        _init_repo(main)

        result = _run_impl_create_worktree(main, "token-test-wt")

        assert result.returncode == 0, f"Script failed: {result.stderr}"

        lines = [ln for ln in result.stdout.strip().splitlines() if ln]
        assert len(lines) == 3, f"Expected exactly 3 stdout lines, got {len(lines)}: {lines}"

        keys = {ln.split("=", 1)[0] for ln in lines}
        assert keys == {"WORKTREE_PATH", "BRANCH_NAME", "BASE_BRANCH"}, (
            f"Expected keys {{WORKTREE_PATH, BRANCH_NAME, BASE_BRANCH}}, got {keys}"
        )

        for ln in lines:
            key, val = ln.split("=", 1)
            assert val.startswith("'") and val.endswith("'"), (
                f"Value for {key} must be single-quoted: {val}"
            )

    def test_impl_worktree_from_linked_worktree_source(self, tmp_path: Path) -> None:
        """Running from a linked worktree still places new worktree outside main root."""
        main = tmp_path / "main_repo"
        _init_repo(main)

        # Create a linked worktree (not in the worktrees/ directory of main).
        wt_source = tmp_path / "wt_source"
        _git("-C", str(main), "worktree", "add", "-b", "wt-source", str(wt_source))

        result = _run_impl_create_worktree(wt_source, "impl-from-worktree")

        assert result.returncode == 0, f"Script failed: {result.stderr}"

        # New worktree should be at tmp_path/worktrees/impl-from-worktree.
        worktrees_dir = tmp_path / "worktrees"
        new_wts = list(worktrees_dir.glob("impl-from-worktree"))
        assert len(new_wts) == 1, f"Expected one worktree at {worktrees_dir}, found: {new_wts}"

        new_wt = new_wts[0]
        assert not any(str(p).startswith(str(wt_source)) for p in new_wt.parents[:-1]), (
            f"New worktree should not be nested inside source worktree: {new_wt}"
        )

    def test_impl_worktree_sidecar_written_atomically(self, tmp_path: Path) -> None:
        """base-branch sidecar exists under MAIN_ROOT/.autoskillit/temp/worktrees/<name>/."""
        main = tmp_path / "main_repo"
        _init_repo(main)
        autoskillit_temp = ".autoskillit/temp"

        result = _run_impl_create_worktree(main, "sidecar-test-wt", autoskillit_temp)

        assert result.returncode == 0, f"Script failed: {result.stderr}"

        expected_sidecar_dir = main / autoskillit_temp / "worktrees" / "sidecar-test-wt"
        base_branch_file = expected_sidecar_dir / "base-branch"

        assert base_branch_file.exists(), f"base-branch should exist at {base_branch_file}"
        content = base_branch_file.read_text().strip()
        assert content != "", "base-branch should not be empty"
        # CURRENT_BRANCH is captured before git worktree add, so it is the current branch
        # of the source repo (here: "main", the initial branch of main_repo).
        assert content == "main", (
            f"base-branch should contain the source repo's current branch 'main', got: {content}"
        )

    def test_impl_worktree_stdout_only_variable_assignments(self, tmp_path: Path) -> None:
        """Every non-empty stdout line is a shell variable assignment. No diagnostics on stdout."""
        import re

        main = tmp_path / "main_repo"
        _init_repo(main)

        result = _run_impl_create_worktree(main, "stdout-test-wt")

        assert result.returncode == 0, f"Script failed: {result.stderr}"

        lines = result.stdout.splitlines()
        non_empty = [ln for ln in lines if ln.strip()]

        assignment_pattern = re.compile(r"^[A-Z_]+='.*'$")
        for ln in non_empty:
            assert assignment_pattern.match(ln), (
                f"Non-empty stdout line does not match variable assignment pattern: {ln!r}"
            )

        # No diagnostic messages or git output should appear on stdout.
        assert not any("ERROR" in ln for ln in lines), (
            f"ERROR messages should go to stderr, not stdout: {result.stdout}"
        )
        assert not any("warning" in ln.lower() for ln in lines), (
            f"Warnings should go to stderr, not stdout: {result.stdout}"
        )
        assert not any("git" in ln.lower() for ln in lines), (
            f"Git output should go to stderr, not stdout: {result.stdout}"
        )
