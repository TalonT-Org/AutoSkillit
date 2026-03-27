"""Integration tests verifying perform_merge() aborts before cleanup on poisoned installs."""

from pathlib import Path

import pytest


@pytest.mark.anyio
async def test_perform_merge_aborts_before_cleanup_on_poisoned_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    When scan_editable_installs_for_worktree returns non-empty results after the merge
    step, perform_merge must return an error result WITHOUT calling git worktree remove.
    """
    import autoskillit.server._editable_guard as _editable_guard

    poisoned_report = ["autoskillit editable at file:///fake/worktree/src (autoskillit-0.6.12)"]

    # Patch the guard to always return a poisoned result
    monkeypatch.setattr(
        _editable_guard,
        "scan_editable_installs_for_worktree",
        lambda worktree_path, site_packages_dirs=None: poisoned_report,
    )

    # NOTE FOR IMPLEMENTER: The full integration test requires either:
    # (a) A conftest.py fixture that creates a real git worktree (preferred for fidelity), or
    # (b) Mocking of _run_git calls in perform_merge with carefully crafted return values
    #     that simulate a successful merge up to step 8, then assert the guard fires.
    # The unit tests in test_editable_guard.py cover the guard function itself.
    # The key contract to enforce: after implementing step 8.5, add an assertion that
    # result["merge_succeeded"] is False and "editable" appears in result["error"]
    # when the guard returns non-empty results.
    pytest.skip("Requires git worktree fixture — implement after _editable_guard.py exists")


@pytest.mark.anyio
async def test_perform_merge_proceeds_normally_when_guard_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    When scan_editable_installs_for_worktree returns [] (no poisoned installs),
    perform_merge must NOT abort — it must proceed to cleanup normally.
    """
    import autoskillit.server._editable_guard as guard_module

    monkeypatch.setattr(
        guard_module,
        "scan_editable_installs_for_worktree",
        lambda worktree_path, site_packages_dirs=None: [],
    )
    # Verify the guard is wired in but does not block clean merges
    # Full test requires git worktree fixture
    pytest.skip("Requires git worktree fixture — implement after _editable_guard.py exists")
