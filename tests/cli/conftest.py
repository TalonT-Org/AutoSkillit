"""CLI test fixtures — shared across tests/cli/*.

Auto-patches the worktree guard so tests that call sync_hooks_to_settings()
or _register_all() can run from git worktrees (e.g. during task install-worktree
development). Tests that explicitly test the worktree guard monkeypatch
is_git_worktree to True themselves.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _patch_worktree_guard_for_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent the worktree guard from firing in tests running inside a worktree."""
    import autoskillit.core.paths as _core_paths

    import autoskillit.cli._hooks as _hooks_mod

    monkeypatch.setattr(_hooks_mod, "is_git_worktree", lambda path: False)
    monkeypatch.setattr(_core_paths, "is_git_worktree", lambda path: False)
