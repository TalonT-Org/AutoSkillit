"""Executor-level session Git evidence tests for real checkout topologies."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_SKILL = "/autoskillit:implement-worktree-no-merge plan.md"


async def _run_session(
    tmp_path: Path,
    minimal_ctx,
    scripted_session_runner,
    *,
    cwd: Path,
    side_effect,
):
    from autoskillit.execution.headless import run_headless_core
    from tests.execution.conftest import _mock_backend

    minimal_ctx.backend = _mock_backend()
    minimal_ctx.runner = scripted_session_runner(
        side_effect, usage={"input_tokens": 1, "output_tokens": 1}
    )
    minimal_ctx.config.linux_tracing.log_dir = str(tmp_path)
    return await run_headless_core(_SKILL, str(cwd), minimal_ctx, completion_marker="")


def _assert_recorded_evidence(
    tmp_path: Path,
    launch_cwd: Path,
    *,
    writes: bool,
    path: str,
    source: str,
    detail: str,
) -> tuple[dict, dict]:
    summary_paths = list((tmp_path / "sessions").glob("*/summary.json"))
    assert len(summary_paths) == 1
    summary = json.loads(summary_paths[0].read_text(encoding="utf-8"))
    index = json.loads((tmp_path / "sessions.jsonl").read_text(encoding="utf-8").strip())
    for row in (summary, index):
        assert row["cwd"] == str(launch_cwd)
        assert row["evidence_worktree_path"] == path
        assert row["evidence_worktree_source"] == source
        assert row["evidence_worktree_detail"] == detail
    assert index["git_writes_detected"] is writes
    return summary, index


def _loc_insertions(ctx) -> int:
    return sum(row["loc_insertions"] for row in ctx.token_log.get_report())


@pytest.mark.anyio
async def test_main_checkout_token_selects_committed_session_worktree(
    tmp_path: Path, minimal_ctx, scripted_session_runner
) -> None:
    from tests._git_topology import add_linked_worktree, commit_file, init_checkout

    repo = init_checkout(tmp_path / "clone")
    created: dict[str, Path] = {}

    def side_effect() -> str:
        wt = add_linked_worktree(repo, "impl")
        commit_file(wt, "feature.py", "value = 1\n" * 4, "implement feature")
        created["worktree"] = wt
        return f"worktree_path = {wt}"

    result = await _run_session(
        tmp_path,
        minimal_ctx,
        scripted_session_runner,
        cwd=repo,
        side_effect=side_effect,
    )

    wt = created["worktree"]
    assert result.evidence.git_writes_detected is True
    _assert_recorded_evidence(
        tmp_path,
        repo,
        writes=True,
        path=os.path.realpath(wt),
        source="token_selected",
        detail="",
    )
    assert _loc_insertions(minimal_ctx) > 0


@pytest.mark.anyio
async def test_main_checkout_token_worktree_without_commit_has_no_git_write(
    tmp_path: Path, minimal_ctx, scripted_session_runner
) -> None:
    from tests._git_topology import add_linked_worktree, init_checkout

    repo = init_checkout(tmp_path / "clone")
    created: dict[str, Path] = {}

    def side_effect() -> str:
        wt = add_linked_worktree(repo, "impl")
        created["worktree"] = wt
        return f"worktree_path = {wt}"

    result = await _run_session(
        tmp_path,
        minimal_ctx,
        scripted_session_runner,
        cwd=repo,
        side_effect=side_effect,
    )

    wt = created["worktree"]
    assert result.evidence.git_writes_detected is False
    _assert_recorded_evidence(
        tmp_path,
        repo,
        writes=False,
        path=os.path.realpath(wt),
        source="token_selected",
        detail="",
    )


@pytest.mark.anyio
async def test_main_checkout_recovers_one_committed_worktree_without_token(
    tmp_path: Path, minimal_ctx, scripted_session_runner
) -> None:
    from tests._git_topology import add_linked_worktree, commit_file, init_checkout

    repo = init_checkout(tmp_path / "clone")
    created: dict[str, Path] = {}

    def side_effect() -> str:
        wt = add_linked_worktree(repo, "impl")
        commit_file(wt, "feature.py", "value = 1\n", "implement feature")
        created["worktree"] = wt
        return "session completed"

    result = await _run_session(
        tmp_path,
        minimal_ctx,
        scripted_session_runner,
        cwd=repo,
        side_effect=side_effect,
    )

    wt = created["worktree"]
    assert result.evidence.git_writes_detected is True
    assert result.worktree_path == str(wt)
    _assert_recorded_evidence(
        tmp_path,
        repo,
        writes=True,
        path=os.path.realpath(wt),
        source="git_diff_recovered",
        detail="",
    )


@pytest.mark.parametrize(
    "bad_token,expected_detail",
    [
        ("nonexistent", "token_rejected:not_a_linked_worktree_of_launch_repo"),
        ("relative", "token_rejected:not_absolute"),
        ("non_git", "token_rejected:not_a_linked_worktree_of_launch_repo"),
        ("subdirectory", "token_rejected:not_a_linked_worktree_of_launch_repo"),
        ("foreign_worktree", "token_rejected:not_a_linked_worktree_of_launch_repo"),
        ("foreign_main", "token_rejected:not_a_linked_worktree_of_launch_repo"),
    ],
)
@pytest.mark.anyio
async def test_main_checkout_rejects_untrusted_token_and_recovers_launch_worktree(
    tmp_path: Path,
    minimal_ctx,
    scripted_session_runner,
    bad_token: str,
    expected_detail: str,
) -> None:
    from tests._git_topology import add_linked_worktree, commit_file, init_checkout

    repo = init_checkout(tmp_path / "clone")
    existing_non_git = tmp_path / "ordinary-directory"
    existing_non_git.mkdir()
    foreign_main = init_checkout(tmp_path / "foreign")
    foreign_worktree = add_linked_worktree(foreign_main, "foreign-wt")
    created: dict[str, Path] = {}

    def side_effect() -> str:
        wt = add_linked_worktree(repo, "impl")
        commit_file(wt, "feature.py", "value = 1\n", "implement feature")
        created["worktree"] = wt
        if bad_token == "nonexistent":
            token = tmp_path / "does-not-exist"
        elif bad_token == "relative":
            token = Path("relative/worktree")
        elif bad_token == "non_git":
            token = existing_non_git
        elif bad_token == "subdirectory":
            token = wt / "nested"
            token.mkdir()
        elif bad_token == "foreign_worktree":
            commit_file(foreign_worktree, "foreign.py", "foreign = True\n", "foreign commit")
            token = foreign_worktree
        else:
            commit_file(foreign_worktree, "foreign.py", "foreign = True\n", "foreign commit")
            token = foreign_main
        return f"worktree_path = {token}"

    result = await _run_session(
        tmp_path,
        minimal_ctx,
        scripted_session_runner,
        cwd=repo,
        side_effect=side_effect,
    )

    wt = created["worktree"]
    assert result.evidence.git_writes_detected is True
    _assert_recorded_evidence(
        tmp_path,
        repo,
        writes=True,
        path=os.path.realpath(wt),
        source="git_diff_recovered",
        detail=expected_detail,
    )
    assert _loc_insertions(minimal_ctx) > 0


@pytest.mark.anyio
async def test_foreign_committed_worktree_token_never_redirects_loc_measurement(
    tmp_path: Path, minimal_ctx, scripted_session_runner
) -> None:
    from tests._git_topology import add_linked_worktree, commit_file, init_checkout

    repo = init_checkout(tmp_path / "clone")
    foreign_main = init_checkout(tmp_path / "foreign")
    foreign_worktree = add_linked_worktree(foreign_main, "foreign-wt")

    def side_effect() -> str:
        commit_file(foreign_worktree, "foreign.py", "foreign = True\n" * 6, "foreign commit")
        return f"worktree_path = {foreign_worktree}"

    result = await _run_session(
        tmp_path,
        minimal_ctx,
        scripted_session_runner,
        cwd=repo,
        side_effect=side_effect,
    )

    assert result.evidence.git_writes_detected is False
    _assert_recorded_evidence(
        tmp_path,
        repo,
        writes=False,
        path=os.path.realpath(repo),
        source="launch_checkout",
        detail="token_rejected:not_a_linked_worktree_of_launch_repo",
    )
    assert _loc_insertions(minimal_ctx) == 0


@pytest.mark.parametrize("commit", [False, True], ids=["no-commit", "commit"])
@pytest.mark.anyio
async def test_linked_launch_measures_its_own_head(
    tmp_path: Path,
    minimal_ctx,
    scripted_session_runner,
    commit: bool,
) -> None:
    from tests._git_topology import add_linked_worktree, commit_file, init_checkout

    repo = init_checkout(tmp_path / "clone")
    launch = add_linked_worktree(repo, "launch")

    def side_effect() -> str:
        if commit:
            commit_file(launch, "feature.py", "launch = True\n" * 3, "launch commit")
        return "session completed"

    result = await _run_session(
        tmp_path,
        minimal_ctx,
        scripted_session_runner,
        cwd=launch,
        side_effect=side_effect,
    )

    assert result.evidence.git_writes_detected is commit
    _assert_recorded_evidence(
        tmp_path,
        launch,
        writes=commit,
        path=os.path.realpath(launch),
        source="launch_worktree",
        detail="",
    )


@pytest.mark.parametrize("token_target", ["main", "other"])
@pytest.mark.anyio
async def test_linked_launch_ignores_same_repo_token_and_measures_launch(
    tmp_path: Path,
    minimal_ctx,
    scripted_session_runner,
    token_target: str,
) -> None:
    from tests._git_topology import add_linked_worktree, commit_file, init_checkout

    repo = init_checkout(tmp_path / "clone")
    other = add_linked_worktree(repo, "other")
    launch = add_linked_worktree(repo, "launch")
    token = repo if token_target == "main" else other

    def side_effect() -> str:
        commit_file(launch, "feature.py", "launch = True\n" * 3, "launch commit")
        return f"worktree_path = {token}"

    result = await _run_session(
        tmp_path,
        minimal_ctx,
        scripted_session_runner,
        cwd=launch,
        side_effect=side_effect,
    )

    assert result.evidence.git_writes_detected is True
    _assert_recorded_evidence(
        tmp_path,
        launch,
        writes=True,
        path=os.path.realpath(launch),
        source="launch_worktree",
        detail="token_ignored:launch_is_linked_worktree",
    )
    assert _loc_insertions(minimal_ctx) > 0


@pytest.mark.anyio
async def test_unborn_launch_head_is_unresolved(
    tmp_path: Path, minimal_ctx, scripted_session_runner
) -> None:
    from tests._git_topology import init_empty_checkout

    repo = init_empty_checkout(tmp_path / "empty")
    result = await _run_session(
        tmp_path,
        minimal_ctx,
        scripted_session_runner,
        cwd=repo,
        side_effect=lambda: "session completed",
    )

    assert result.evidence.git_writes_detected is False
    _assert_recorded_evidence(
        tmp_path,
        repo,
        writes=False,
        path="",
        source="unresolved",
        detail="launch_head_unavailable",
    )


@pytest.mark.anyio
async def test_non_git_launch_does_not_measure_session_created_foreign_worktree(
    tmp_path: Path, minimal_ctx, scripted_session_runner
) -> None:
    from tests._git_topology import add_linked_worktree, commit_file, init_checkout

    launch = tmp_path / "autoskillit-runs" / "worktrees"
    launch.mkdir(parents=True)
    repo = init_checkout(tmp_path / "clone")
    created: dict[str, Path] = {}

    def side_effect() -> str:
        wt = add_linked_worktree(repo, "impl")
        commit_file(wt, "feature.py", "value = 1\n", "foreign commit")
        created["worktree"] = wt
        return f"worktree_path = {wt}"

    result = await _run_session(
        tmp_path,
        minimal_ctx,
        scripted_session_runner,
        cwd=launch,
        side_effect=side_effect,
    )

    assert created["worktree"].exists()
    assert result.evidence.git_writes_detected is False
    _assert_recorded_evidence(
        tmp_path,
        launch,
        writes=False,
        path="",
        source="non_git",
        detail="",
    )
    assert _loc_insertions(minimal_ctx) == 0


@pytest.mark.anyio
async def test_ambiguous_new_worktrees_keep_first_routing_candidate(
    tmp_path: Path, minimal_ctx, scripted_session_runner
) -> None:
    from tests._git_topology import add_linked_worktree, commit_file, init_checkout

    repo = init_checkout(tmp_path / "clone")
    created: dict[str, Path] = {}

    def side_effect() -> str:
        first = add_linked_worktree(repo, "a-first")
        second = add_linked_worktree(repo, "b-second")
        commit_file(second, "feature.py", "value = 1\n", "second worktree commit")
        created.update(first=first, second=second)
        return "session completed"

    result = await _run_session(
        tmp_path,
        minimal_ctx,
        scripted_session_runner,
        cwd=repo,
        side_effect=side_effect,
    )

    first = created["first"]
    assert result.evidence.git_writes_detected is False
    assert result.worktree_path == str(first)
    _assert_recorded_evidence(
        tmp_path,
        repo,
        writes=False,
        path=os.path.realpath(repo),
        source="unresolved",
        detail="ambiguous_new_worktrees:2",
    )


@pytest.mark.parametrize("start_kind", ["branch", "head_parent", "annotated_tag"])
@pytest.mark.anyio
async def test_uncommitted_worktree_from_known_non_head_has_no_git_write(
    tmp_path: Path,
    minimal_ctx,
    scripted_session_runner,
    start_kind: str,
) -> None:
    import subprocess

    from tests._git_topology import add_linked_worktree, commit_file, head, init_checkout

    repo = init_checkout(tmp_path / "clone")
    first_sha = head(repo)
    commit_file(repo, "later.py", "later = True\n", "second main commit")
    if start_kind == "branch":
        subprocess.run(
            ["git", "branch", "known-side", first_sha],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        start = "known-side"
    elif start_kind == "head_parent":
        start = "HEAD~1"
    else:
        subprocess.run(
            ["git", "tag", "-a", "known-tag", "-m", "known tag", first_sha],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        start = "known-tag"
    created: dict[str, Path] = {}

    def side_effect() -> str:
        created["worktree"] = add_linked_worktree(repo, "from-known", start=start)
        return "session completed"

    result = await _run_session(
        tmp_path,
        minimal_ctx,
        scripted_session_runner,
        cwd=repo,
        side_effect=side_effect,
    )

    assert result.evidence.git_writes_detected is False
    _assert_recorded_evidence(
        tmp_path,
        repo,
        writes=False,
        path=os.path.realpath(created["worktree"]),
        source="git_diff_recovered",
        detail="",
    )


@pytest.mark.parametrize("commit", [False, True], ids=["no-commit", "commit"])
@pytest.mark.anyio
async def test_preexisting_linked_worktree_uses_its_own_session_baseline(
    tmp_path: Path,
    minimal_ctx,
    scripted_session_runner,
    commit: bool,
) -> None:
    from tests._git_topology import add_linked_worktree, commit_file, init_checkout

    repo = init_checkout(tmp_path / "clone")
    existing = add_linked_worktree(repo, "preexisting")

    def side_effect() -> str:
        if commit:
            commit_file(existing, "feature.py", "existing = True\n", "existing worktree commit")
        return f"worktree_path = {existing}"

    result = await _run_session(
        tmp_path,
        minimal_ctx,
        scripted_session_runner,
        cwd=repo,
        side_effect=side_effect,
    )

    assert result.evidence.git_writes_detected is commit
    _assert_recorded_evidence(
        tmp_path,
        repo,
        writes=commit,
        path=os.path.realpath(existing),
        source="token_selected",
        detail="",
    )


@pytest.mark.parametrize("stale", [False, True], ids=["natural-exit", "stale-exit"])
@pytest.mark.anyio
async def test_executor_parses_session_stdout_once(
    tmp_path: Path,
    minimal_ctx,
    scripted_session_runner,
    monkeypatch,
    stale: bool,
) -> None:
    import autoskillit.execution.headless._headless_adjudication as adjudication
    import autoskillit.execution.headless._headless_execute as execute
    import autoskillit.execution.headless._headless_result as result_module
    from autoskillit.core.types import TerminationReason
    from autoskillit.execution.headless import run_headless_core
    from tests._git_topology import init_checkout

    repo = init_checkout(tmp_path / "clone")
    calls = 0
    for module in (execute, result_module, adjudication):
        original = module._parse_stdout

        def spy(*args, _original=original, **kwargs):
            nonlocal calls
            calls += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, "_parse_stdout", spy)

    runner = scripted_session_runner(lambda: "session completed")
    if stale:

        async def stale_runner(cmd, *, cwd, timeout, **kwargs):
            result = await runner(cmd, cwd=cwd, timeout=timeout, **kwargs)
            if cmd[0] == "git":
                return result
            return replace(
                result,
                returncode=-15,
                termination=TerminationReason.STALE,
            )

        minimal_ctx.runner = stale_runner
    else:
        minimal_ctx.runner = runner

    from tests.execution.conftest import _mock_backend

    minimal_ctx.backend = _mock_backend()
    minimal_ctx.config.linux_tracing.log_dir = str(tmp_path)
    executed = await run_headless_core(
        _SKILL,
        str(repo),
        minimal_ctx,
        completion_marker="",
    )

    assert calls == 1
    assert executed.evidence.git_writes_detected is False
