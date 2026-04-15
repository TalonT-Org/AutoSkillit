# tests/workspace/test_constants.py
"""Asserts that workspace directory name constants are exported from workspace/__init__."""


def test_runs_dir_constant_is_exported():
    from autoskillit.workspace import RUNS_DIR

    assert RUNS_DIR == "autoskillit-runs"


def test_worktrees_dir_constant_is_exported():
    from autoskillit.workspace import WORKTREES_DIR

    assert WORKTREES_DIR == "worktrees"
