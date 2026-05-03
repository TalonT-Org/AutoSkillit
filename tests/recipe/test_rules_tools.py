"""Tests for the unknown-tool semantic rule."""

import importlib
import inspect as _inspect

import pytest

from autoskillit.core import GATED_TOOLS, UNGATED_TOOLS, Severity
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


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


def test_rules_tools_batch_cleanup_clones_accepts_all_owners_param() -> None:
    """T20 — batch_cleanup_clones with all_owners param must not trigger dead-with-param."""
    from autoskillit.recipe.rules.rules_tools import _TOOL_PARAMS

    assert "all_owners" in _TOOL_PARAMS["batch_cleanup_clones"]

    recipe = _make_recipe_with_args(
        "batch_cleanup_clones",
        {"all_owners": "true", "registry_path": "/tmp/reg.json"},
    )
    findings = run_semantic_rules(recipe)
    dead = [f for f in findings if f.rule == "dead-with-param"]
    assert not dead, f"all_owners must not trigger dead-with-param: {dead}"


# ---------------------------------------------------------------------------
# rebase-then-push-requires-force rule tests (T6, T7)
# ---------------------------------------------------------------------------


def _make_rebase_then_push_recipe(force: str | None = None) -> Recipe:
    """Two-step recipe: resolve-merge-conflicts → push_to_remote."""
    push_args: dict[str, str] = {"clone_path": "x", "remote_url": "r", "branch": "b"}
    if force is not None:
        push_args["force"] = force
    return Recipe(
        name="test-recipe",
        description="Test rebase-then-push rule.",
        version="0.2.0",
        kitchen_rules="Use run_skill only.",
        steps={
            "step_a": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:resolve-merge-conflicts worktree plan base"
                },
                on_success="step_b",
            ),
            "step_b": RecipeStep(
                tool="push_to_remote",
                with_args=push_args,
            ),
        },
    )


def test_rebase_then_push_without_force_raises_error() -> None:
    """T6: push_to_remote after resolve-merge-conflicts without force=true is an ERROR."""
    recipe = _make_rebase_then_push_recipe(force=None)
    findings = run_semantic_rules(recipe)
    errors = [
        f
        for f in findings
        if f.rule == "rebase-then-push-requires-force" and f.severity == Severity.ERROR
    ]
    assert errors, "Expected ERROR finding for rebase-then-push-requires-force"
    assert any(f.step_name == "step_b" for f in errors)


def test_rebase_then_push_with_force_true_passes_validation() -> None:
    """T7: push_to_remote after resolve-merge-conflicts with force='true' passes."""
    recipe = _make_rebase_then_push_recipe(force="true")
    findings = run_semantic_rules(recipe)
    errors = [f for f in findings if f.rule == "rebase-then-push-requires-force"]
    assert not errors, "force='true' must not trigger rebase-then-push-requires-force"


# ---------------------------------------------------------------------------
# _TOOL_PARAMS sync test (T8)
# ---------------------------------------------------------------------------

_SERVER_TOOL_MODULES = [
    "autoskillit.server.tools.tools_ci",
    "autoskillit.server.tools.tools_ci_watch",
    "autoskillit.server.tools.tools_ci_merge_queue",
    "autoskillit.server.tools.tools_clone",
    "autoskillit.server.tools.tools_execution",
    "autoskillit.server.tools.tools_git",
    "autoskillit.server.tools.tools_recipe",
    "autoskillit.server.tools.tools_status",
    "autoskillit.server.tools.tools_github",
    "autoskillit.server.tools.tools_issue_lifecycle",
    "autoskillit.server.tools.tools_pr_ops",
    "autoskillit.server.tools.tools_workspace",
]

_FRAMEWORK_PARAMS = frozenset({"ctx"})


def _build_handler_map() -> dict[str, object]:
    handler_map: dict[str, object] = {}
    for mod_name in _SERVER_TOOL_MODULES:
        mod = importlib.import_module(mod_name)
        for name, obj in _inspect.getmembers(mod):
            if _inspect.iscoroutinefunction(obj) and not name.startswith("_"):
                handler_map[name] = obj
    return handler_map


def test_tool_params_matches_mcp_handler_signatures() -> None:
    """T8: _TOOL_PARAMS keys match actual MCP handler signatures — drift fails CI."""
    from autoskillit.recipe.rules.rules_tools import _TOOL_PARAMS

    handler_map = _build_handler_map()
    mismatches: list[str] = []

    for tool_name, expected_params in _TOOL_PARAMS.items():
        if tool_name not in handler_map:
            mismatches.append(f"{tool_name}: handler not found in server modules")
            continue
        handler = handler_map[tool_name]
        sig = _inspect.signature(handler)
        actual_params = frozenset(name for name in sig.parameters if name not in _FRAMEWORK_PARAMS)
        if actual_params != expected_params:
            missing = expected_params - actual_params
            extra = actual_params - expected_params
            mismatches.append(
                f"{tool_name}: _TOOL_PARAMS={sorted(expected_params)} "
                f"handler={sorted(actual_params)} "
                f"(missing={sorted(missing)}, extra={sorted(extra)})"
            )

    assert not mismatches, (
        "_TOOL_PARAMS is out of sync with MCP handler signatures:\n" + "\n".join(mismatches)
    )


class TestConstantStepWithArgs:
    def test_constant_with_args_fires_error(self):
        recipe = Recipe(
            name="test-recipe",
            description="Test constant rule",
            steps={
                "s": RecipeStep(constant="val", with_args={"x": "1"}, on_success="done"),
                "done": RecipeStep(action="stop", message="Done checking constant."),
            },
        )
        findings = [f for f in run_semantic_rules(recipe) if f.rule == "constant-step-with-args"]
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR

    def test_constant_only_is_clean(self):
        recipe = Recipe(
            name="test-recipe",
            description="Test constant rule",
            steps={
                "s": RecipeStep(constant="val", on_success="done"),
                "done": RecipeStep(action="stop", message="Done checking constant."),
            },
        )
        findings = [f for f in run_semantic_rules(recipe) if f.rule == "constant-step-with-args"]
        assert len(findings) == 0

    def test_with_args_only_is_clean(self):
        recipe = Recipe(
            name="test-recipe",
            description="Test constant rule",
            steps={
                "s": RecipeStep(tool="run_cmd", with_args={"cmd": "echo hi"}, on_success="done"),
                "done": RecipeStep(action="stop", message="Done checking constant."),
            },
        )
        findings = [f for f in run_semantic_rules(recipe) if f.rule == "constant-step-with-args"]
        assert len(findings) == 0


# ---------------------------------------------------------------------------
# release-issue-requires-disposition rule tests
# ---------------------------------------------------------------------------


def test_release_issue_requires_disposition_fires_on_bare_call() -> None:
    """Rule fires when release_issue step has neither fail_label nor target_branch."""
    recipe = _make_recipe_with_args(
        "release_issue",
        {"issue_url": "https://github.com/o/r/issues/1"},
    )
    findings = run_semantic_rules(recipe)
    hits = [f for f in findings if f.rule == "release-issue-requires-disposition"]
    assert hits
    assert hits[0].severity == Severity.ERROR
    assert "run" in hits[0].step_name


def test_release_issue_requires_disposition_passes_with_fail_label() -> None:
    """Rule does NOT fire when fail_label is present."""
    recipe = _make_recipe_with_args(
        "release_issue",
        {"issue_url": "https://github.com/o/r/issues/1", "fail_label": "fail"},
    )
    findings = run_semantic_rules(recipe)
    hits = [f for f in findings if f.rule == "release-issue-requires-disposition"]
    assert not hits


def test_release_issue_requires_disposition_passes_with_target_branch() -> None:
    """Rule does NOT fire when target_branch is present."""
    recipe = _make_recipe_with_args(
        "release_issue",
        {
            "issue_url": "https://github.com/o/r/issues/1",
            "target_branch": "${{ inputs.base_branch }}",
        },
    )
    findings = run_semantic_rules(recipe)
    hits = [f for f in findings if f.rule == "release-issue-requires-disposition"]
    assert not hits
