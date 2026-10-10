"""Tests for allowed_write_prefix computation in run_skill — decoupled from read_only."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.server.tools._execution_helpers import scope_covers_cwd
from autoskillit.server.tools.tools_execution import (
    _compute_write_prefixes,
    run_skill,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


# ---------------------------------------------------------------------------
# AC1: _compute_write_prefixes shape-aware tests
# ---------------------------------------------------------------------------


def _make_worktree_layout(tmp_path: Path) -> tuple[Path, Path]:
    """Build a clone-root + linked-worktree layout for prefix tests.

    Returns (clone_root, worktree_path).
    """
    from tests._git_topology import add_linked_worktree, init_checkout

    clone_root = init_checkout(tmp_path / "repo")
    (clone_root / "worktrees").mkdir()
    worktree = add_linked_worktree(clone_root, "impl-fix-123")
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
    assert all_prefixes[1:] == (
        str(worktree.resolve()) + "/",
        str(worktree.parent.resolve()) + "/",
    )
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
    worktree_prefixes = list(all_prefixes[1:])
    assert worktree_prefixes == [str((clone_root.parent / "worktrees").resolve()) + "/"]
    assert not scope_covers_cwd(tuple(worktree_prefixes), str(clone_root))
    # Worktree was created but unused in this test.
    assert worktree.exists()


def test_main_checkout_named_worktrees_is_not_covered_by_worktree_prefix(
    tmp_path: Path,
) -> None:
    from tests._git_topology import init_checkout

    checkout = init_checkout(tmp_path / "worktrees")
    watch_dir = tmp_path / "watch"
    watch_dir.mkdir()

    _primary, all_prefixes = _compute_write_prefixes(
        write_watch_dirs=[watch_dir],
        cwd=str(checkout),
        skill_command="/autoskillit:implement-worktree-no-merge plan.md",
    )

    worktree_prefixes = all_prefixes[1:]
    assert not scope_covers_cwd(worktree_prefixes, str(checkout))


def test_non_git_cwd_gets_no_worktree_prefix(tmp_path: Path) -> None:
    non_git = tmp_path / "plain-directory"
    non_git.mkdir()

    _primary, all_prefixes = _compute_write_prefixes(
        write_watch_dirs=[non_git],
        cwd=str(non_git),
        skill_command="/autoskillit:implement-worktree-no-merge plan.md",
    )

    assert all_prefixes[1:] == ()


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
    tool_ctx_kitchen_open, monkeypatch, tmp_path, git_checkout
) -> None:
    """When write scope does NOT cover cwd for a WORKTREE_SKILLS dispatch, return gate_error."""
    import json

    from tests.fakes import InMemoryHeadlessExecutor

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
    result = await run_skill(
        f"/autoskillit:implement-worktree-no-merge {plan_path}",
        cwd=str(git_checkout),
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
    assert call.skill_command == f"/implement-worktree-no-merge {plan_path}"
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


# ---------------------------------------------------------------------------
# Closure write scope and output_dir narrowing through the typed write scope
# ---------------------------------------------------------------------------


def _scoped_skill(tmp_path: Path, name: str, declaration: str):
    from autoskillit.core import SkillSource
    from autoskillit.workspace.skills import _skill_info_from_frontmatter

    path = tmp_path / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"---\nname: {name}\ndescription: Scope fixture.\nwrite_paths: {declaration}\n---\n",
        encoding="utf-8",
    )
    info = _skill_info_from_frontmatter(name, SkillSource.BUNDLED_EXTENDED, path)
    assert not info.invalidities
    return info


def _scope_state(tmp_path: Path, root, *deps, output_dir: str = ""):
    from types import SimpleNamespace

    cwd = tmp_path / "project"
    cwd.mkdir(exist_ok=True)
    first = cwd / output_dir if output_dir else cwd / ".autoskillit" / "temp" / root.name
    return SimpleNamespace(
        write_watch_dirs=[first],
        invocation=SimpleNamespace(root=root, closure=(root, *deps)),
        cwd=str(cwd),
        output_dir=output_dir,
    )


def _extend(state) -> dict | None:
    import json

    from autoskillit.server.tools.tools_execution._run_skill_session import (
        _extend_closure_write_scope,
    )

    terminal = _extend_closure_write_scope(state)
    return None if terminal is None else json.loads(terminal)


def test_bounded_root_rejects_output_dir_outside_its_scope(tmp_path: Path) -> None:
    root = _scoped_skill(tmp_path, "widget", "['{{AUTOSKILLIT_TEMP}}/widgets/']")

    state = _scope_state(tmp_path, root, output_dir=".autoskillit/temp/other")
    original_dirs = state.write_watch_dirs.copy()

    failure = _extend(state)

    assert state.write_watch_dirs == original_dirs

    assert failure is not None
    for fragment in ("'widget'", "'.autoskillit/temp/other'", "{{AUTOSKILLIT_TEMP}}/widgets/"):
        assert fragment in failure["error"]
    assert failure["stage"] == "validate_args:run_skill"


def test_bounded_root_rejects_worktree_root_output_dir(tool_ctx, tmp_path: Path) -> None:
    from autoskillit.core import SkillExecutionRole

    invocation = tool_ctx.skill_resolver.resolve_invocation(
        "make-plan", tool_ctx.project_dir, SkillExecutionRole.SESSION
    )

    failure = _extend(_scope_state(tmp_path, invocation.root, output_dir="."))

    assert failure is not None
    assert failure["stage"] == "validate_args:run_skill"
    for fragment in ("'make-plan'", "'.'", "{{AUTOSKILLIT_TEMP}}/make-plan/"):
        assert fragment in failure["error"]


def test_bounded_root_admits_output_dir_inside_its_scope(tmp_path: Path) -> None:
    root = _scoped_skill(tmp_path, "widget", "['{{AUTOSKILLIT_TEMP}}/widgets/']")
    state = _scope_state(tmp_path, root, output_dir=".autoskillit/temp/widgets/run-1")

    assert _extend(state) is None
    assert (Path(state.cwd) / ".autoskillit" / "temp" / "widgets").resolve() in (
        state.write_watch_dirs
    )


def test_bounded_root_without_output_dir_does_not_check_the_default_floor(
    tmp_path: Path,
) -> None:
    root = _scoped_skill(tmp_path, "widget", "['{{AUTOSKILLIT_TEMP}}/widgets/']")

    assert _extend(_scope_state(tmp_path, root)) is None


@pytest.mark.parametrize("declaration", ["unrestricted", "inherit"])
def test_unbounded_root_skips_output_dir_containment(tmp_path: Path, declaration: str) -> None:
    root = _scoped_skill(tmp_path, "free", declaration)

    assert _extend(_scope_state(tmp_path, root, output_dir="elsewhere/out")) is None


def test_closure_member_escaping_the_temp_root_fails_dispatch(tmp_path: Path) -> None:
    root = _scoped_skill(tmp_path, "root", "inherit")
    dependency = _scoped_skill(tmp_path, "escape", "['{{AUTOSKILLIT_TEMP}}/escape/']")
    state = _scope_state(tmp_path, root, dependency)
    temp = Path(state.cwd) / ".autoskillit" / "temp"
    temp.mkdir(parents=True)
    (temp / "escape").symlink_to(tmp_path, target_is_directory=True)
    original_dirs = state.write_watch_dirs.copy()

    failure = _extend(state)

    assert state.write_watch_dirs == original_dirs
    assert failure is not None
    assert "declared write scope for closure member escape" in failure["error"]
    assert failure["stage"] == "validate_args:run_skill"
