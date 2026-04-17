"""Protocol conformance and behavioral tests for tests/fakes.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.types import (
    CIWatcher,
    DatabaseReader,
    HeadlessExecutor,
    MergeQueueWatcher,
    RecipeRepository,
    SkillResult,
    SubprocessRunner,
    TestResult,
    TestRunner,
)
from tests.fakes import (
    InMemoryCIWatcher,
    InMemoryDatabaseReader,
    InMemoryHeadlessExecutor,
    InMemoryMergeQueueWatcher,
    InMemoryRecipeRepository,
    InMemoryTestRunner,
    MockSubprocessRunner,
)

# ---------------------------------------------------------------------------
# T1: isinstance protocol conformance
# ---------------------------------------------------------------------------


def test_in_memory_headless_executor_satisfies_protocol():
    assert isinstance(InMemoryHeadlessExecutor(), HeadlessExecutor)


def test_in_memory_test_runner_satisfies_protocol():
    assert isinstance(InMemoryTestRunner(), TestRunner)


def test_in_memory_recipe_repository_satisfies_protocol():
    assert isinstance(InMemoryRecipeRepository(), RecipeRepository)


def test_in_memory_ci_watcher_satisfies_protocol():
    assert isinstance(InMemoryCIWatcher(), CIWatcher)


def test_in_memory_merge_queue_watcher_satisfies_protocol():
    assert isinstance(InMemoryMergeQueueWatcher(), MergeQueueWatcher)


def test_in_memory_database_reader_satisfies_protocol():
    assert isinstance(InMemoryDatabaseReader(), DatabaseReader)


def test_mock_subprocess_runner_satisfies_protocol():
    assert isinstance(MockSubprocessRunner(), SubprocessRunner)


# ---------------------------------------------------------------------------
# T2: InMemoryHeadlessExecutor behavioral tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_executor_returns_configured_result():
    result = SkillResult(
        success=True,
        result="ok",
        session_id="s1",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason="none",
        stderr="",
    )
    executor = InMemoryHeadlessExecutor(default_result=result)
    got = await executor.run("/skill", "/cwd")
    assert got == result


@pytest.mark.anyio
async def test_executor_records_calls():
    executor = InMemoryHeadlessExecutor()
    await executor.run("/skill", "/cwd", model="opus")
    assert len(executor.calls) == 1
    assert executor.calls[0].skill_command == "/skill"
    assert executor.calls[0].cwd == "/cwd"
    assert executor.calls[0].model == "opus"


@pytest.mark.anyio
async def test_executor_pops_from_queue():
    r1 = SkillResult(
        success=True,
        result="first",
        session_id="",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason="none",
        stderr="",
    )
    r2 = SkillResult(
        success=False,
        result="second",
        session_id="",
        subtype="error",
        is_error=True,
        exit_code=1,
        needs_retry=False,
        retry_reason="none",
        stderr="",
    )
    executor = InMemoryHeadlessExecutor()
    executor.push(r1)
    executor.push(r2)
    assert (await executor.run("/s", "/c")).result == "first"
    assert (await executor.run("/s", "/c")).result == "second"


# ---------------------------------------------------------------------------
# T3: InMemoryTestRunner behavioral tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_runner_returns_configured_results_in_order():
    runner = InMemoryTestRunner(
        results=[
            TestResult(passed=True, stdout="ok", stderr=""),
            TestResult(passed=False, stdout="fail", stderr="err"),
        ]
    )
    assert (await runner.run(Path("/a"))).passed is True
    assert (await runner.run(Path("/b"))).passed is False


@pytest.mark.anyio
async def test_runner_fallback_after_exhaustion():
    runner = InMemoryTestRunner(results=[])
    r = await runner.run(Path("/a"))
    assert r.passed is True


@pytest.mark.anyio
async def test_runner_tracks_call_count():
    runner = InMemoryTestRunner(results=[])
    assert runner.call_count == 0
    await runner.run(Path("/a"))
    assert runner.call_count == 1
    await runner.run(Path("/b"))
    assert runner.call_count == 2


# ---------------------------------------------------------------------------
# T4: InMemoryCIWatcher and InMemoryMergeQueueWatcher behavioral tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ci_watcher_returns_configured_wait_result():
    watcher = InMemoryCIWatcher(wait_result={"run_id": 42, "conclusion": "failure"})
    result = await watcher.wait("main")
    assert result["run_id"] == 42
    assert result["conclusion"] == "failure"


@pytest.mark.anyio
async def test_ci_watcher_records_wait_calls():
    watcher = InMemoryCIWatcher()
    await watcher.wait("main", repo="owner/repo")
    assert len(watcher.wait_calls) == 1
    assert watcher.wait_calls[0]["branch"] == "main"
    assert watcher.wait_calls[0]["repo"] == "owner/repo"


@pytest.mark.anyio
async def test_ci_watcher_side_effect_raises():
    watcher = InMemoryCIWatcher()
    watcher.wait_side_effect = RuntimeError("network timeout")
    with pytest.raises(RuntimeError, match="network timeout"):
        await watcher.wait("main")


@pytest.mark.anyio
async def test_merge_queue_watcher_records_calls():
    watcher = InMemoryMergeQueueWatcher()
    await watcher.wait(123, "main")
    assert len(watcher.wait_calls) == 1
    assert watcher.wait_calls[0]["pr_number"] == 123
    assert watcher.wait_calls[0]["target_branch"] == "main"


@pytest.mark.anyio
async def test_merge_queue_watcher_toggle():
    watcher = InMemoryMergeQueueWatcher()
    result = await watcher.toggle(42, "main")
    assert result["toggled"] is True
    assert len(watcher.toggle_calls) == 1


@pytest.mark.anyio
async def test_database_reader_returns_configured_result():
    reader = InMemoryDatabaseReader(
        query_result={"columns": ["id"], "rows": [[1]], "row_count": 1}
    )
    result = reader.query("test.db", "SELECT 1", [], 30, 100)
    assert result["row_count"] == 1
    assert len(reader.calls) == 1


# ---------------------------------------------------------------------------
# T5: InMemoryRecipeRepository call recording
# ---------------------------------------------------------------------------


def test_recipe_repository_find_records_call():
    repo = InMemoryRecipeRepository()
    repo.find("my-recipe", Path("/proj"))
    assert len(repo.calls) == 1
    call = repo.calls[0]
    assert call["method"] == "find"
    assert call["name"] == "my-recipe"
    assert call["project_dir"] == Path("/proj")


def test_recipe_repository_load_and_validate_records_call():
    repo = InMemoryRecipeRepository()
    repo.load_and_validate(
        "my-recipe",
        Path("/proj"),
        suppressed=["rule-a"],
        resolved_defaults={"k": "v"},
        ingredient_overrides={"x": "y"},
        temp_dir=Path("/tmp"),
        temp_dir_relpath=".autoskillit/temp",
    )
    assert len(repo.calls) == 1
    call = repo.calls[0]
    assert call["method"] == "load_and_validate"
    assert call["name"] == "my-recipe"
    assert call["project_dir"] == Path("/proj")
    assert call["suppressed"] == ["rule-a"]
    assert call["resolved_defaults"] == {"k": "v"}
    assert call["ingredient_overrides"] == {"x": "y"}
    assert call["temp_dir"] == Path("/tmp")
    assert call["temp_dir_relpath"] == ".autoskillit/temp"


def test_recipe_repository_list_all_records_call():
    repo = InMemoryRecipeRepository()
    repo.list_all(project_dir=Path("/proj"))
    assert len(repo.calls) == 1
    call = repo.calls[0]
    assert call["method"] == "list_all"
    assert call["project_dir"] == Path("/proj")


def test_recipe_repository_list_all_records_call_with_none_project_dir():
    repo = InMemoryRecipeRepository()
    repo.list_all()
    assert len(repo.calls) == 1
    assert repo.calls[0]["project_dir"] is None


def test_recipe_repository_calls_accumulate():
    repo = InMemoryRecipeRepository()
    repo.find("r1", Path("/a"))
    repo.list_all()
    repo.load_and_validate("r1", Path("/a"))
    assert len(repo.calls) == 3
    assert repo.calls[0]["method"] == "find"
    assert repo.calls[1]["method"] == "list_all"
    assert repo.calls[2]["method"] == "load_and_validate"


def test_recipe_repository_calls_starts_empty():
    repo = InMemoryRecipeRepository()
    assert repo.calls == []
