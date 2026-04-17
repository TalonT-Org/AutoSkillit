"""Smoke-test pipeline: structural validation and end-to-end execution tests.

The smoke-test recipe is a lightweight end-to-end sanity check that clones the
repository, creates an isolated branch, implements a trivial micro-task
(smoke_canary), runs the full project test suite, pushes to remote, opens a
GitHub PR, and immediately closes it without merging.

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
import re
from pathlib import Path

import pytest

from autoskillit.recipe.io import builtin_recipes_dir
from autoskillit.server.tools_recipe import list_recipes, validate_recipe

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

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


# ---------------------------------------------------------------------------
# New structural tests for rewritten recipe (T_SP_NEW_1 through T_SP_NEW_12+)
# ---------------------------------------------------------------------------


# T_SP_NEW_1
def test_required_lifecycle_steps_present(smoke_recipe) -> None:
    """All new lifecycle steps must exist in the recipe."""
    required = {
        "clone",
        "create_branch",
        "setup",
        "implement_task",
        "run_tests",
        "push_branch",
        "create_pr",
        "close_pr",
        "delete_remote_branch",
        "register_clone_success",
        "fail_delete_remote_branch",
        "register_clone_failure",
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
def test_close_pr_routes_to_delete_remote_branch_on_both_outcomes(smoke_recipe) -> None:
    """REQ-GUARD-002: cleanup must be reached regardless of close_pr result."""
    step = smoke_recipe.steps["close_pr"]
    assert step.on_success == "delete_remote_branch"
    assert step.on_failure == "delete_remote_branch"


# T_SP_NEW_8
def test_delete_remote_branch_routes_to_register_clone_success(smoke_recipe) -> None:
    """delete_remote_branch is non-critical — routes to register_clone_success."""
    step = smoke_recipe.steps["delete_remote_branch"]
    assert step.on_success == "register_clone_success"
    assert step.on_failure == "register_clone_success"


# T_SP_NEW_9
def test_register_clone_failure_routes_to_escalate(smoke_recipe) -> None:
    """register_clone_failure preserves failure semantics — must route to escalate."""
    step = smoke_recipe.steps["register_clone_failure"]
    assert step.on_success == "escalate"
    assert step.on_failure == "escalate"


# T_SP_NEW_10
def test_test_step_routes_to_fail_delete_on_failure(smoke_recipe) -> None:
    """REQ-GUARD-002: test failure must clean up before escalating."""
    step = smoke_recipe.steps["run_tests"]
    assert step.tool == "run_cmd"
    assert "task test-check" in step.with_args.get("cmd", "")
    assert step.on_failure == "fail_delete_remote_branch"


# T_SP_NEW_11
def test_recipe_has_no_collect_on_branch_ingredient(smoke_recipe) -> None:
    """Old collect_on_branch complexity is gone — ingredient must be absent."""
    assert "collect_on_branch" not in smoke_recipe.ingredients


# T_SP_NEW_12
def test_done_and_escalate_are_stop_actions(smoke_recipe) -> None:
    """Both terminal steps must have action=stop."""
    assert smoke_recipe.steps["done"].action == "stop"
    assert smoke_recipe.steps["escalate"].action == "stop"


# T_SP_NEW_13
def test_clone_step_is_first_and_uses_clone_repo(smoke_recipe) -> None:
    """The first step must be clone using clone_repo tool."""
    first_step_name = next(iter(smoke_recipe.steps))
    assert first_step_name == "clone"
    step = smoke_recipe.steps["clone"]
    assert step.tool == "clone_repo"
    assert step.with_args["source_dir"] == "${{ inputs.source_dir }}"


# T_SP_NEW_14
def test_push_branch_uses_push_to_remote(smoke_recipe) -> None:
    """push_branch must use push_to_remote tool (not run_cmd git push)."""
    step = smoke_recipe.steps["push_branch"]
    assert step.tool == "push_to_remote"
    assert step.with_args["clone_path"] == "${{ context.work_dir }}"
    assert step.with_args["branch"] == "${{ context.branch_name }}"
    assert step.with_args["remote_url"] == "${{ context.remote_url }}"


# T_SP_NEW_15
def test_ingredient_renamed_to_source_dir(smoke_recipe) -> None:
    """Ingredient must be source_dir, not workspace."""
    assert "source_dir" in smoke_recipe.ingredients
    assert "workspace" not in smoke_recipe.ingredients


# T_SP_NEW_16
def test_register_clone_success_routes_to_done(smoke_recipe) -> None:
    """register_clone_success must route to done."""
    step = smoke_recipe.steps["register_clone_success"]
    assert step.tool == "register_clone_status"
    assert step.on_success == "done"
    assert step.on_failure == "done"


# T_SP_NEW_17
def test_no_step_uses_inputs_as_cwd_after_clone(smoke_recipe) -> None:
    """No step after clone should use inputs.* as cwd."""
    input_re = re.compile(r"\$\{\{\s*inputs\.\w+\s*\}\}")
    seen_clone = False
    for name, step in smoke_recipe.steps.items():
        if step.tool == "clone_repo":
            seen_clone = True
            continue
        if seen_clone and step.with_args:
            cwd = step.with_args.get("cwd", "")
            assert not input_re.search(cwd), (
                f"Step '{name}' uses inputs.* as cwd after clone: {cwd}"
            )


# T_SP_NEW_18
def test_smoke_recipe_passes_isolation_rules(smoke_recipe) -> None:
    """Smoke recipe must pass all isolation semantic rules."""
    from autoskillit.recipe.validator import run_semantic_rules

    findings = run_semantic_rules(smoke_recipe)
    isolation_findings = [
        f for f in findings if f.rule in ("source-isolation-violation", "git-mutation-on-source")
    ]
    assert isolation_findings == []


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
