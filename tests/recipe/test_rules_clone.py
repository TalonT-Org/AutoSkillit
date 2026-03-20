"""Tests for semantic validation rules in recipe/rules_clone.py."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    """Minimal recipe factory for rules_clone tests."""
    return Recipe(
        name="test-clone-rules",
        description="Test recipe for clone/push/multipart rules.",
        kitchen_rules="Use run_skill.",
        steps=steps,
    )


# ---------------------------------------------------------------------------
# multipart-plan-parts-not-captured
# ---------------------------------------------------------------------------


def test_make_plan_without_plan_parts_capture_fires_error() -> None:
    """make-plan step with no plan_parts in capture_list must fire ERROR."""
    recipe = _make_recipe(
        {
            "plan_step": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:make-plan plan.md",
                    "cwd": "/tmp",
                },
                capture_list={},
            )
        }
    )
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "multipart-plan-parts-not-captured"]
    assert len(rule_findings) == 1
    assert rule_findings[0].severity == Severity.ERROR


def test_make_plan_with_plan_parts_capture_passes() -> None:
    """make-plan step that captures plan_parts must not fire the rule."""
    recipe = _make_recipe(
        {
            "plan_step": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:make-plan plan.md",
                    "cwd": "/tmp",
                },
                capture_list={"plan_parts": "${result.plan_parts}"},
            )
        }
    )
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "multipart-plan-parts-not-captured"]
    assert rule_findings == []


def test_rectify_without_plan_parts_capture_fires_error() -> None:
    """rectify step with no plan_parts in capture_list must fire ERROR."""
    recipe = _make_recipe(
        {
            "rectify_step": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:rectify plan.md",
                    "cwd": "/tmp",
                },
                capture_list={},
            )
        }
    )
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "multipart-plan-parts-not-captured"]
    assert len(rule_findings) == 1
    assert rule_findings[0].severity == Severity.ERROR


def test_non_multipart_skill_not_flagged() -> None:
    """investigate skill step must not fire multipart-plan-parts-not-captured."""
    recipe = _make_recipe(
        {
            "investigate_step": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:investigate plan.md",
                    "cwd": "/tmp",
                },
                capture_list={},
            )
        }
    )
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "multipart-plan-parts-not-captured"]
    assert rule_findings == []


# ---------------------------------------------------------------------------
# skill-command-missing-prefix
# ---------------------------------------------------------------------------


def test_skill_command_with_slash_prefix_passes() -> None:
    """skill_command with '/' prefix must not fire skill-command-missing-prefix."""
    recipe = _make_recipe(
        {
            "investigate_step": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:investigate plan.md",
                    "cwd": "/tmp",
                },
            )
        }
    )
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "skill-command-missing-prefix"]
    assert rule_findings == []


def test_skill_command_without_slash_fires_warning() -> None:
    """skill_command without leading '/' must fire WARNING."""
    recipe = _make_recipe(
        {
            "bad_step": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "investigate plan.md",
                    "cwd": "/tmp",
                },
            )
        }
    )
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "skill-command-missing-prefix"]
    assert len(rule_findings) == 1
    assert rule_findings[0].severity == Severity.WARNING


def test_skill_command_none_does_not_fire() -> None:
    """run_skill step with no skill_command key must not fire (fail-open)."""
    recipe = _make_recipe(
        {
            "no_cmd_step": RecipeStep(
                tool="run_skill",
                with_args={"cwd": "/tmp"},
            )
        }
    )
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "skill-command-missing-prefix"]
    assert rule_findings == []


def test_non_run_skill_tool_not_checked() -> None:
    """run_cmd step must not trigger skill-command-missing-prefix."""
    recipe = _make_recipe(
        {
            "cmd_step": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo hello", "cwd": "/tmp"},
            )
        }
    )
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "skill-command-missing-prefix"]
    assert rule_findings == []


# ---------------------------------------------------------------------------
# push-missing-explicit-remote-url
# ---------------------------------------------------------------------------


def test_push_without_remote_url_fires_warning() -> None:
    """push_to_remote step without remote_url must fire WARNING."""
    recipe = _make_recipe(
        {
            "push_step": RecipeStep(
                tool="push_to_remote",
                with_args={"source_dir": "/tmp/repo"},
            )
        }
    )
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "push-missing-explicit-remote-url"]
    assert len(rule_findings) == 1
    assert rule_findings[0].severity == Severity.WARNING


def test_push_with_remote_url_passes() -> None:
    """push_to_remote step with remote_url must not fire the rule."""
    recipe = _make_recipe(
        {
            "push_step": RecipeStep(
                tool="push_to_remote",
                with_args={
                    "source_dir": "/tmp/repo",
                    "remote_url": "https://github.com/org/repo.git",
                },
            )
        }
    )
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "push-missing-explicit-remote-url"]
    assert rule_findings == []


def test_other_tool_not_flagged() -> None:
    """run_cmd step must not fire push-missing-explicit-remote-url."""
    recipe = _make_recipe(
        {
            "cmd_step": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo hello", "cwd": "/tmp"},
            )
        }
    )
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "push-missing-explicit-remote-url"]
    assert rule_findings == []
