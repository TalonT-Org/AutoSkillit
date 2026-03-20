"""Tests for the unknown-tool semantic rule."""

import pytest

from autoskillit.core import Severity
from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep


def _make_recipe(tool: str | None = None, action: str | None = None) -> Recipe:
    """Minimal recipe factory for unknown-tool rule tests."""
    step: dict = {}
    if tool is not None:
        step["tool"] = tool
        step["with_args"] = {"skill_command": "/autoskillit:investigate"}
    elif action is not None:
        step["action"] = action
        step["message"] = "done"
    return Recipe(
        name="test-recipe",
        description="Test recipe for unknown-tool rule.",
        version="0.2.0",
        kitchen_rules="Use run_skill only.",
        steps={"run": RecipeStep(**step)},
    )


def test_run_skill_retry_flagged_as_error() -> None:
    """Recipe step with removed tool run_skill_retry produces unknown-tool ERROR."""
    recipe = _make_recipe(tool="run_skill_retry")
    findings = run_semantic_rules(recipe)
    unknown = [f for f in findings if f.rule == "unknown-tool"]
    assert unknown, "Expected unknown-tool finding for run_skill_retry"
    assert all(f.severity == Severity.ERROR for f in unknown)
    assert any("run_skill_retry" in f.message for f in unknown)


def test_run_recipe_flagged_as_unknown_tool() -> None:
    """Recipe step with removed tool run_recipe produces unknown-tool ERROR.

    run_recipe was removed from GATED_TOOLS; recipes using it must be flagged
    by the unknown-tool validator rule so orchestrators cannot accidentally call
    a non-existent tool (REQ-TEST-004).
    """
    recipe = _make_recipe(tool="run_recipe")
    findings = run_semantic_rules(recipe)
    unknown = [f for f in findings if f.rule == "unknown-tool"]
    assert unknown, "Expected unknown-tool finding for run_recipe"
    assert all(f.severity == Severity.ERROR for f in unknown)
    assert any("run_recipe" in f.message for f in unknown)


def test_arbitrary_unknown_tool_flagged() -> None:
    """Any unregistered tool name produces unknown-tool ERROR."""
    recipe = _make_recipe(tool="bogus_tool_xyz")
    findings = run_semantic_rules(recipe)
    unknown = [f for f in findings if f.rule == "unknown-tool"]
    assert unknown, "Expected unknown-tool finding for bogus_tool_xyz"


def test_none_tool_not_checked() -> None:
    """Steps with tool=None (action/python steps) are not flagged by unknown-tool."""
    recipe = _make_recipe(action="stop")
    findings = run_semantic_rules(recipe)
    unknown = [f for f in findings if f.rule == "unknown-tool"]
    assert not unknown, "action steps must not trigger unknown-tool"


@pytest.mark.parametrize("tool_name", sorted(GATED_TOOLS | UNGATED_TOOLS))
def test_all_registered_tools_pass(tool_name: str) -> None:
    """Every tool in GATED_TOOLS | UNGATED_TOOLS is accepted without unknown-tool finding."""
    recipe = _make_recipe(tool=tool_name)
    findings = run_semantic_rules(recipe)
    unknown = [f for f in findings if f.rule == "unknown-tool"]
    assert not unknown, f"Registered tool '{tool_name}' must not trigger unknown-tool"


# ---------------------------------------------------------------------------
# dead-with-param rule tests
# ---------------------------------------------------------------------------


def _make_recipe_with_args(tool: str, with_args: dict[str, str] | None = None) -> Recipe:
    """Minimal recipe factory with explicit with_args."""
    step_kwargs: dict = {"tool": tool}
    if with_args is not None:
        step_kwargs["with_args"] = with_args
    else:
        step_kwargs["with_args"] = {"skill_command": "/autoskillit:investigate"}
    return Recipe(
        name="test-recipe",
        description="Test recipe for dead-with-param rule.",
        version="0.2.0",
        kitchen_rules="Use run_skill only.",
        steps={"run": RecipeStep(**step_kwargs)},
    )


def test_dead_with_param_detects_unknown_key() -> None:
    """with key 'add_dir' on run_skill produces dead-with-param WARNING."""
    recipe = _make_recipe_with_args(
        "run_skill",
        {"skill_command": "/autoskillit:investigate", "cwd": "/tmp", "add_dir": "/some/path"},
    )
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert dead, "Expected dead-with-param finding for 'add_dir'"
    assert all(f.severity == Severity.WARNING for f in dead)
    assert any("add_dir" in f.message for f in dead)


def test_dead_with_param_allows_valid_keys() -> None:
    """Valid run_skill keys (skill_command, cwd, model, step_name) pass clean."""
    recipe = _make_recipe_with_args(
        "run_skill",
        {"skill_command": "/autoskillit:investigate", "cwd": "/tmp", "model": "sonnet"},
    )
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert not dead, "Valid keys must not trigger dead-with-param"


def test_dead_with_param_skips_unknown_tools() -> None:
    """Steps with unknown tools are skipped (caught by unknown-tool rule instead)."""
    recipe = _make_recipe_with_args(
        "bogus_tool",
        {"bogus_key": "value"},
    )
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert not dead, "Unknown tools must not trigger dead-with-param"


# ---------------------------------------------------------------------------
# REQ-C4-01: _TOOL_PARAMS correctness tests
# ---------------------------------------------------------------------------


def test_run_cmd_rejects_command_param() -> None:
    recipe = _make_recipe_with_args("run_cmd", {"command": "echo hi", "cwd": "/tmp"})
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert dead, "stale param 'command' must trigger dead-with-param"
    assert any("command" in f.message for f in dead)


def test_run_cmd_accepts_cmd_param() -> None:
    recipe = _make_recipe_with_args("run_cmd", {"cmd": "echo hi", "cwd": "/tmp"})
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert not dead, "valid param 'cmd' must not trigger dead-with-param"


def test_run_python_rejects_callable_path_param() -> None:
    recipe = _make_recipe_with_args("run_python", {"callable_path": "mod.fn"})
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert dead, "stale param 'callable_path' must trigger dead-with-param"


def test_run_python_accepts_callable_param() -> None:
    recipe = _make_recipe_with_args("run_python", {"callable": "mod.fn", "args": {}})
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert not dead, "valid params 'callable'/'args' must not trigger dead-with-param"


def test_clone_repo_rejects_stale_params() -> None:
    recipe = _make_recipe_with_args("clone_repo", {"repo": "owner/repo", "target_dir": "/tmp/x"})
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert len(dead) >= 2, "stale params 'repo' and 'target_dir' must trigger dead-with-param"


def test_clone_repo_accepts_source_dir_param() -> None:
    recipe = _make_recipe_with_args("clone_repo", {"source_dir": "/src", "run_name": "impl"})
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert not dead, "valid params 'source_dir'/'run_name' must not trigger dead-with-param"


def test_remove_clone_rejects_clone_dir() -> None:
    recipe = _make_recipe_with_args("remove_clone", {"clone_dir": "/tmp/clone"})
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert dead, "stale param 'clone_dir' must trigger dead-with-param"


def test_remove_clone_accepts_clone_path() -> None:
    recipe = _make_recipe_with_args("remove_clone", {"clone_path": "/tmp/clone", "keep": "false"})
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert not dead


def test_list_recipes_rejects_cwd_param() -> None:
    recipe = _make_recipe_with_args("list_recipes", {"cwd": "/tmp"})
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert dead, "param 'cwd' must trigger dead-with-param on list_recipes (no params)"


def test_migrate_recipe_rejects_recipe_path() -> None:
    recipe = _make_recipe_with_args("migrate_recipe", {"recipe_path": "/tmp/r.yaml"})
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert dead, "stale param 'recipe_path' must trigger dead-with-param"


def test_migrate_recipe_accepts_name() -> None:
    recipe = _make_recipe_with_args("migrate_recipe", {"name": "my-recipe"})
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert not dead


def test_load_recipe_rejects_recipe_name() -> None:
    recipe = _make_recipe_with_args("load_recipe", {"recipe_name": "impl", "ingredients": {}})
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert len(dead) >= 2


def test_load_recipe_accepts_name_and_overrides() -> None:
    recipe = _make_recipe_with_args("load_recipe", {"name": "impl", "overrides": {}})
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert not dead


def test_fetch_github_issue_rejects_stale_params() -> None:
    recipe = _make_recipe_with_args(
        "fetch_github_issue",
        {"issue_number": "42", "repo": "owner/repo", "cwd": "/tmp"},
    )
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert len(dead) >= 3


def test_fetch_github_issue_accepts_issue_url() -> None:
    recipe = _make_recipe_with_args(
        "fetch_github_issue",
        {"issue_url": "https://github.com/owner/repo/issues/42"},
    )
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert not dead


def test_wait_for_ci_rejects_poll_interval() -> None:
    recipe = _make_recipe_with_args(
        "wait_for_ci",
        {"branch": "main", "poll_interval": "30"},
    )
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert dead, "phantom param 'poll_interval' must trigger dead-with-param on wait_for_ci"
