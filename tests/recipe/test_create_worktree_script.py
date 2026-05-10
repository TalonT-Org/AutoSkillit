"""Tests for create_worktree.sh script."""

from __future__ import annotations

import subprocess

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_create_worktree_emits_research_dir_rel(tmp_path):
    """create_worktree.sh must emit research_dir_rel alongside research_dir."""
    from autoskillit.recipe.io import builtin_recipes_dir

    script_path = builtin_recipes_dir() / "scripts" / "create_worktree.sh"
    if not script_path.exists():
        pytest.skip("create_worktree.sh not found")

    # Create a minimal git repo as the source directory
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source_dir, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=source_dir,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=source_dir,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=source_dir,
        check=True,
    )

    # Create a minimal experiment plan file
    plan_dir = tmp_path / "plans"
    plan_dir.mkdir()
    experiment_plan = plan_dir / "experiment-plan.md"
    experiment_plan.write_text("# Experiment Plan\n\nTest experiment.")

    # Run create_worktree.sh
    result = subprocess.run(
        [
            "bash",
            str(script_path),
            str(source_dir),
            "test-task",
            str(experiment_plan),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"

    stdout = result.stdout
    assert "research_dir_rel=research/" in stdout, (
        f"stdout must contain 'research_dir_rel=research/...' line. Got:\n{stdout}"
    )
    # The rel path should follow pattern: research/YYYY-MM-DD-test-task
    import re

    rel_match = re.search(r"research_dir_rel=(research/[\d-]+-[\w-]+)", stdout)
    assert rel_match, f"Could not find research_dir_rel pattern in stdout:\n{stdout}"
    assert result.returncode == 0
