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
