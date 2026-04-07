"""Smoke-test pipeline: structural validation and end-to-end execution tests.

The smoke-test recipe is a lightweight end-to-end sanity check that creates an
isolated branch and worktree, implements a trivial micro-task (smoke_canary),
runs the full project test suite, opens a GitHub PR, and immediately closes it
without merging.

**Running tests:**

- Structural tests (no API): ``task test-all`` (included automatically)
- Smoke execution test (requires API + GitHub auth): ``task test-smoke``
  - Requires ``ANTHROPIC_API_KEY`` and authenticated ``gh`` CLI
  - Expected duration: under 10 minutes
  - Excluded from ``task test-all`` to avoid API costs in routine development
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from autoskillit.recipe.io import builtin_recipes_dir
from autoskillit.server.tools_recipe import list_recipes, validate_recipe

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SMOKE_SCRIPT = PROJECT_ROOT / ".autoskillit" / "recipes" / "smoke-test.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def smoke_recipe():
    from autoskillit.recipe.io import load_recipe as _load_recipe

    return _load_recipe(SMOKE_SCRIPT)


@pytest.fixture()
def smoke_script_path() -> Path:
    return SMOKE_SCRIPT


@pytest.fixture()
def smoke_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temp project dir with smoke-test as a project-local recipe.

    smoke-test is a project-local recipe (not bundled), so it must be copied
    into the temp dir's .autoskillit/recipes/ for discovery via list_recipes().
    """
    import shutil

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    shutil.copy2(SMOKE_SCRIPT, recipes_dir / "smoke-test.yaml")
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Structural Validation Tests (no API required)
# ---------------------------------------------------------------------------


class TestSmokeScriptValidation:
    """Validate the smoke-test pipeline YAML structure."""

    @pytest.fixture(autouse=True)
    def _setup_ctx(self, tool_ctx):
        """Pull tool_ctx into scope to trigger its monkeypatch side-effect.

        The server module's _ctx is set by the tool_ctx fixture. Tests in this
        class call server tools (e.g. validate_recipe) that read _ctx. Without
        this fixture, _ctx is None and every server call raises.
        """

    async def test_script_validates(self, smoke_script_path: Path) -> None:
        result = json.loads(await validate_recipe(script_path=str(smoke_script_path)))
        assert result["valid"] is True
        assert result["errors"] == []

    async def test_script_discoverable(self, smoke_project: Path) -> None:
        result = json.loads(await list_recipes())
        names = [s["name"] for s in result["recipes"]]
        assert "smoke-test" in names

    def test_smoke_test_not_in_bundled_dir(self) -> None:
        """smoke-test.yaml must not exist in the bundled recipes directory."""
        assert not (builtin_recipes_dir() / "smoke-test.yaml").exists()

    def test_smoke_test_exists_in_project_local(self) -> None:
        """smoke-test.yaml must exist in the project-local recipes directory."""
        assert SMOKE_SCRIPT.exists(), f"Expected smoke-test at {SMOKE_SCRIPT}"

    async def test_smoke_test_source_is_project(self, smoke_project: Path) -> None:
        """smoke-test must be listed with source PROJECT, not BUILTIN."""
        result = json.loads(await list_recipes())
        smoke = next((r for r in result["recipes"] if r["name"] == "smoke-test"), None)
        assert smoke is not None, "smoke-test not found in list_recipes output"
        assert smoke["source"] == "project"

    async def test_smoke_test_invisible_from_external_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """smoke-test must NOT appear in list_recipes from a project without it."""
        bare_dir = tmp_path / "external"
        bare_dir.mkdir()
        monkeypatch.chdir(bare_dir)
        result = json.loads(await list_recipes())
        names = [r["name"] for r in result["recipes"]]
        assert "smoke-test" not in names

    def test_smoke_recipe_is_ready_to_execute(self) -> None:
        """Smoke recipe exists, validates, and is project-local."""
        assert SMOKE_SCRIPT.exists()
        pipeline = yaml.safe_load(SMOKE_SCRIPT.read_text())
        assert pipeline["name"] == "smoke-test"
        assert "create_pr" in pipeline["steps"]
        assert "close_pr" in pipeline["steps"]


# ---------------------------------------------------------------------------
# New structural tests for rewritten recipe (T_SP_NEW_1 through T_SP_NEW_12)
# ---------------------------------------------------------------------------


# T_SP_NEW_1
def test_required_lifecycle_steps_present(smoke_recipe) -> None:
    """All new lifecycle steps must exist in the recipe."""
    required = {
        "create_branch",
        "create_worktree",
        "setup_worktree",
        "implement_task",
        "test",
        "push_branch",
        "create_pr",
        "close_pr",
        "cleanup",
        "fail_cleanup",
        "done",
        "escalate",
    }
    missing = required - set(smoke_recipe.steps)
    assert not missing, f"Missing steps: {missing}"


# T_SP_NEW_2
def test_no_merge_worktree_step(smoke_recipe) -> None:
    """REQ-GUARD-001: No step may use the merge_worktree tool."""
    for name, step in smoke_recipe.steps.items():
        assert step.tool != "merge_worktree", (
            f"Step '{name}' uses merge_worktree — REQ-GUARD-001 violation"
        )


# T_SP_NEW_3
def test_no_legacy_pipeline_steps(smoke_recipe) -> None:
    """Old investigate/rectify/assess/classify/merge steps must be absent."""
    legacy = {"investigate", "rectify", "assess", "classify", "merge"}
    present = legacy & set(smoke_recipe.steps)
    assert not present, f"Legacy steps still present: {present}"


# T_SP_NEW_4
def test_implement_task_references_smoke_canary(smoke_recipe) -> None:
    """REQ-TASK-001: implement_task skill_command must reference smoke_canary."""
    step = smoke_recipe.steps["implement_task"]
    cmd = step.with_args.get("skill_command", "")
    assert "smoke_canary" in cmd, "implement_task must describe the smoke_canary micro-task"


# T_SP_NEW_5
def test_create_pr_step_uses_gh_pr_create(smoke_recipe) -> None:
    """REQ-PIPE-003: create_pr step must invoke gh pr create."""
    step = smoke_recipe.steps["create_pr"]
    assert step.tool == "run_cmd"
    assert "gh pr create" in step.with_args.get("cmd", "")


# T_SP_NEW_6
def test_close_pr_step_uses_gh_pr_close(smoke_recipe) -> None:
    """REQ-PIPE-004: close_pr step must invoke gh pr close."""
    step = smoke_recipe.steps["close_pr"]
    assert step.tool == "run_cmd"
    assert "gh pr close" in step.with_args.get("cmd", "")


# T_SP_NEW_7
def test_close_pr_routes_to_cleanup_on_both_outcomes(smoke_recipe) -> None:
    """REQ-GUARD-002: cleanup must be reached regardless of close_pr result."""
    step = smoke_recipe.steps["close_pr"]
    assert step.on_success == "cleanup", "close_pr must route to cleanup on success"
    assert step.on_failure == "cleanup", "close_pr must route to cleanup on failure"


# T_SP_NEW_8
def test_cleanup_routes_to_done_regardless(smoke_recipe) -> None:
    """cleanup is non-critical — must route to done on both success and failure."""
    step = smoke_recipe.steps["cleanup"]
    assert step.on_success == "done"
    assert step.on_failure == "done"


# T_SP_NEW_9
def test_fail_cleanup_routes_to_escalate(smoke_recipe) -> None:
    """fail_cleanup preserves failure semantics — must route to escalate."""
    step = smoke_recipe.steps["fail_cleanup"]
    assert step.on_success == "escalate"
    assert step.on_failure == "escalate"


# T_SP_NEW_10
def test_test_step_routes_to_fail_cleanup_on_failure(smoke_recipe) -> None:
    """REQ-GUARD-002: test failure must clean up before escalating."""
    step = smoke_recipe.steps["test"]
    assert step.on_failure == "fail_cleanup"


# T_SP_NEW_11
def test_recipe_has_no_collect_on_branch_ingredient(smoke_recipe) -> None:
    """Old collect_on_branch complexity is gone — ingredient must be absent."""
    assert "collect_on_branch" not in smoke_recipe.ingredients


# T_SP_NEW_12
def test_done_and_escalate_are_stop_actions(smoke_recipe) -> None:
    """Both terminal steps must have action=stop."""
    assert smoke_recipe.steps["done"].action == "stop"
    assert smoke_recipe.steps["escalate"].action == "stop"


# ---------------------------------------------------------------------------
# Smoke Execution Tests (API required)
# ---------------------------------------------------------------------------


class TestSmokePipelineExecution:
    """Full end-to-end smoke execution.

    Run via ``task test-smoke`` which sets SMOKE_TEST=1 and invokes the
    recipe against the actual project repository. This class is a
    documentation anchor — the execution itself uses `autoskillit cook`.
    """

    pytestmark = pytest.mark.skipif(not os.environ.get("SMOKE_TEST"), reason="SMOKE_TEST not set")
