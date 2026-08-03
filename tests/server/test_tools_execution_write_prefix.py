"""Tests for allowed_write_prefix computation in run_skill — decoupled from read_only."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.server.tools.tools_execution import (
    _compute_write_prefixes,
    run_skill,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# ---------------------------------------------------------------------------
# AC1: _compute_write_prefixes shape-aware tests
# ---------------------------------------------------------------------------


def _make_worktree_layout(tmp_path: Path) -> tuple[Path, Path]:
    """Build a clone-root + linked-worktree layout for prefix tests.

    Returns (clone_root, worktree_path).
    """
    clone_root = tmp_path / "repo"
    clone_root.mkdir()
    worktrees_dir = clone_root / "worktrees"
    worktrees_dir.mkdir()
    worktree = worktrees_dir / "impl-fix-123"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: ../../.git/worktrees/impl-fix-123\n")
    return clone_root, worktree


def test_worktree_cwd_shape_produces_correct_parent_prefix(tmp_path: Path) -> None:
    """When cwd IS a linked worktree, parent prefix is the worktree's own parent."""
    clone_root, worktree = _make_worktree_layout(tmp_path)

    primary, all_prefixes = _compute_write_prefixes(
        write_watch_dirs=[worktree],
        cwd=str(worktree),
        skill_command="/autoskillit:implement-worktree-no-merge /some/path.md",
    )

    # Sanity: clone_root was created and primary reflects first write_watch_dir.
    assert clone_root.exists()
    assert primary == str(worktree) + "/"
    # Must include the cwd itself (the session's own tracked tree)
    assert (str(worktree) + "/") in all_prefixes
    # Must include the worktree parent (worktrees/) — NOT worktrees/worktrees/
    worktree_parent_str = str(worktree.parent) + "/"
    assert worktree_parent_str in all_prefixes
    # Must NOT double-include as "worktrees/worktrees/"
    assert (str(worktree.parent) + "/worktrees/") not in all_prefixes


def test_clone_root_cwd_shape_still_produces_worktrees_sibling(tmp_path: Path) -> None:
    """When cwd is the clone root, worktree-parent prefix is the sibling worktrees/ directory."""
    clone_root, worktree = _make_worktree_layout(tmp_path)

    primary, all_prefixes = _compute_write_prefixes(
        write_watch_dirs=[clone_root],
        cwd=str(clone_root),
        skill_command="/autoskillit:implement-worktree-no-merge /some/path.md",
    )

    assert primary == str(clone_root) + "/"
    worktrees_parent_str = str(clone_root / "worktrees") + "/"
    assert worktrees_parent_str in all_prefixes
    # Worktree was created but unused in this test.
    assert worktree.exists()


def test_worktree_cwd_self_inclusion(tmp_path: Path) -> None:
    """When cwd is a linked worktree, the session's tracked tree (cwd) MUST be allowed."""
    clone_root, worktree = _make_worktree_layout(tmp_path)

    primary, all_prefixes = _compute_write_prefixes(
        write_watch_dirs=[worktree],
        cwd=str(worktree),
        skill_command="/autoskillit:retry-worktree",
    )

    assert clone_root.exists()
    assert primary == str(worktree) + "/"
    assert (str(worktree) + "/") in all_prefixes


def test_non_worktree_skill_no_worktree_prefix(tmp_path: Path) -> None:
    """For non-WORKTREE_SKILLS, no worktree-parent prefix is added regardless of cwd."""
    clone_root, worktree = _make_worktree_layout(tmp_path)

    primary, all_prefixes = _compute_write_prefixes(
        write_watch_dirs=[worktree],
        cwd=str(worktree),
        skill_command="/autoskillit:investigate regression",
    )

    assert clone_root.exists()
    assert primary == str(worktree) + "/"
    # No worktree-related entries — just the base_prefix from write_watch_dirs
    worktree_parent_str = str(worktree.parent) + "/"
    assert worktree_parent_str not in all_prefixes


# ---------------------------------------------------------------------------
# AC2: dispatch preflight tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fail_fast_when_scope_excludes_cwd(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """When write scope does NOT cover cwd for a WORKTREE_SKILLS dispatch, return gate_error."""
    import json

    from tests.fakes import InMemoryHeadlessExecutor

    clone_root, worktree = _make_worktree_layout(tmp_path)
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# plan")
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    # write scope is temp-only — does NOT cover cwd.
    # cwd is a location outside the worktree/worktree-parent scope, so the
    # preflight's _scope_covers_cwd check must reject the dispatch.
    temp_dir = tmp_path / "elsewhere"
    temp_dir.mkdir()
    non_worktree_cwd = tmp_path / "non_worktree_cwd"
    non_worktree_cwd.mkdir()
    result = await run_skill(
        f"/autoskillit:implement-worktree-no-merge {plan_path}",
        cwd=str(non_worktree_cwd),
        output_dir=str(temp_dir),
    )

    assert len(executor.calls) == 0, "No session should be dispatched"
    parsed = json.loads(result)
    assert parsed["is_error"] is True
    assert parsed["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_pass_when_scope_covers_cwd(tool_ctx_kitchen_open, monkeypatch, tmp_path) -> None:
    """When write scope covers cwd for a WORKTREE_SKILLS dispatch, dispatch proceeds normally."""
    from tests.fakes import InMemoryHeadlessExecutor

    clone_root, worktree = _make_worktree_layout(tmp_path)
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# plan")
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    output_dir = str(worktree)
    await run_skill(
        f"/autoskillit:implement-worktree-no-merge {plan_path}",
        cwd=str(worktree),
        output_dir=output_dir,
    )
    # A session was dispatched
    assert len(executor.calls) == 1
    call = executor.calls[0]
    assert call.capability_contract is not None
    assert not hasattr(call.capability_contract, "resolved_command")
    assert call.skill_command.startswith("/implement-worktree-no-merge")
    assert call.capability_contract.cwd == str(worktree.resolve())
    assert call.cwd == str(worktree.resolve())


@pytest.mark.anyio
async def test_preflight_fires_for_conditional_contract_worktree_skills(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """Preflight fires for worktree skills regardless of write-behavior mode."""

    from tests.fakes import InMemoryHeadlessExecutor

    clone_root, worktree = _make_worktree_layout(tmp_path)
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# plan")
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    # Scope covers cwd — preflight evaluates and passes (dispatch proceeds)
    await run_skill(
        f"/autoskillit:implement-worktree-no-merge {plan_path}",
        cwd=str(worktree),
        output_dir=str(worktree),
    )

    # Preflight passed → session dispatched
    assert len(executor.calls) == 1


@pytest.mark.anyio
async def test_preflight_does_not_fire_for_non_worktree_skills(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """Non-worktree skills bypass the preflight (fail-open for them)."""
    from tests.fakes import InMemoryHeadlessExecutor

    clone_root, worktree = _make_worktree_layout(tmp_path)
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    # investigate has its own temp dir under cwd; the preflight should NOT fire
    # even if write_watch_dirs is temp-only.
    await run_skill("/autoskillit:investigate regression", cwd=str(worktree))
    # investigate dispatches — no gate_error from preflight
    assert len(executor.calls) == 1


# ---------------------------------------------------------------------------
# Existing run_skill integration tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_allowed_write_prefix_set_from_output_dir_even_when_not_read_only(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """allowed_write_prefix is set from output_dir even for non-read-only skills."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    output_dir = str(tmp_path / "planner" / "run-xyz")
    await run_skill("/test planner-skill", str(tmp_path), output_dir=output_dir)

    assert len(executor.calls) == 1
    assert executor.calls[0].allowed_write_prefix == output_dir + "/"
    assert executor.calls[0].allowed_write_prefixes == (output_dir + "/",)


@pytest.mark.anyio
async def test_allowed_write_prefix_uses_fallback_without_output_dir(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """When no output_dir is given, fallback computes prefix from skill name."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/test skill", str(tmp_path))

    assert len(executor.calls) == 1
    expected = str(tmp_path / ".autoskillit" / "temp" / "test") + "/"
    assert executor.calls[0].allowed_write_prefix == expected
    assert executor.calls[0].allowed_write_prefixes == (expected,)


@pytest.mark.anyio
async def test_investigate_contract_runs_writable_with_report_watch_dir(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """investigate must not be launched as a read-only skill because it writes a report."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/autoskillit:investigate regression", str(tmp_path))

    assert len(executor.calls) == 1
    call = executor.calls[0]
    assert call.readonly_skill is False
    assert call.write_behavior is not None
    assert call.write_behavior.mode == "always"
    assert call.expected_output_patterns

    report_dir = tmp_path / ".autoskillit" / "temp" / "investigate"
    assert Path(report_dir) in call.write_watch_dirs
    assert call.allowed_write_prefix == str(report_dir) + "/"
