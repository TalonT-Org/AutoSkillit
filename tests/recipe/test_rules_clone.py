"""Tests for semantic validation rules in recipe/rules_clone.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
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


# ---------------------------------------------------------------------------
# clone-local-strategy-with-remote-url-capture
# ---------------------------------------------------------------------------

_RULE = "clone-local-strategy-with-remote-url-capture"
_CLONE_REPO = "autoskillit.workspace.clone.clone_repo"


def test_rule_rejects_clone_local_with_remote_url_capture() -> None:
    """clone_local strategy + remote_url capture must fire ERROR (B1)."""
    recipe = _make_recipe(
        {
            "clone": RecipeStep(
                tool="run_python",
                python=_CLONE_REPO,
                with_args={"strategy": "clone_local", "run_name": "t"},
                capture={"remote_url": "${{ result.remote_url }}"},
            ),
            "push": RecipeStep(
                tool="push_to_remote",
                with_args={
                    "remote_url": "${{ context.remote_url }}",
                    "clone_path": "${{ context.clone_path }}",
                },
            ),
        }
    )
    findings = run_semantic_rules(recipe)
    matching = [f for f in findings if f.rule == _RULE]
    assert len(matching) == 1
    assert matching[0].severity == Severity.ERROR
    assert "clone_local" in matching[0].message
    assert "remote_url" in matching[0].message


def test_rule_allows_clone_local_without_remote_url_capture() -> None:
    """clone_local strategy without remote_url capture must not fire (B2)."""
    recipe = _make_recipe(
        {
            "clone": RecipeStep(
                tool="run_python",
                python=_CLONE_REPO,
                with_args={"strategy": "clone_local", "run_name": "t"},
                capture={"clone_path": "${{ result.clone_path }}"},
            ),
        }
    )
    findings = run_semantic_rules(recipe)
    matching = [f for f in findings if f.rule == _RULE]
    assert matching == []


def test_rule_allows_proceed_strategy_with_remote_url_capture() -> None:
    """proceed strategy + remote_url capture must not fire (B3)."""
    recipe = _make_recipe(
        {
            "clone": RecipeStep(
                tool="run_python",
                python=_CLONE_REPO,
                with_args={"strategy": "proceed", "run_name": "t"},
                capture={"remote_url": "${{ result.remote_url }}"},
            ),
        }
    )
    findings = run_semantic_rules(recipe)
    matching = [f for f in findings if f.rule == _RULE]
    assert matching == []


def test_rule_allows_absent_strategy_with_remote_url_capture() -> None:
    """Absent strategy (defaults to proceed) + remote_url capture must not fire (B3 variant)."""
    recipe = _make_recipe(
        {
            "clone": RecipeStep(
                tool="run_python",
                python=_CLONE_REPO,
                with_args={"run_name": "t"},
                capture={"remote_url": "${{ result.remote_url }}"},
            ),
        }
    )
    findings = run_semantic_rules(recipe)
    matching = [f for f in findings if f.rule == _RULE]
    assert matching == []


def test_rule_warns_on_context_templated_strategy_with_remote_url_capture() -> None:
    """Templated strategy + remote_url capture must fire WARNING (B4)."""
    recipe = _make_recipe(
        {
            "clone": RecipeStep(
                tool="run_python",
                python=_CLONE_REPO,
                with_args={
                    "strategy": "${{ context.cloning_mode }}",
                    "run_name": "t",
                },
                capture={"remote_url": "${{ result.remote_url }}"},
            ),
        }
    )
    findings = run_semantic_rules(recipe)
    matching = [f for f in findings if f.rule == _RULE]
    assert len(matching) == 1
    assert matching[0].severity == Severity.WARNING
    assert "statically" in matching[0].message or "templated" in matching[0].message


def test_rule_ignores_non_clone_repo_python_steps() -> None:
    """run_python step calling a different module must not fire (B5)."""
    recipe = _make_recipe(
        {
            "prep": RecipeStep(
                tool="run_python",
                python="autoskillit.smoke_utils.prepare",
                with_args={"strategy": "clone_local"},
                capture={"remote_url": "${{ result.remote_url }}"},
            ),
        }
    )
    findings = run_semantic_rules(recipe)
    matching = [f for f in findings if f.rule == _RULE]
    assert matching == []


def test_rule_fires_on_result_dot_remote_url_alias() -> None:
    """Template-expression aliasing: capture key != 'remote_url' must still fire (B6)."""
    recipe = _make_recipe(
        {
            "clone": RecipeStep(
                tool="run_python",
                python=_CLONE_REPO,
                with_args={"strategy": "clone_local", "run_name": "t"},
                capture={
                    # Key name is different but template reads result.remote_url — still dangerous.
                    "alt_url": "${{ result.remote_url }}",
                },
            ),
        }
    )
    findings = run_semantic_rules(recipe)
    matching = [f for f in findings if f.rule == _RULE]
    assert len(matching) == 1
    assert matching[0].severity == Severity.ERROR


def test_rule_does_not_fire_when_capture_reads_different_field() -> None:
    """Capture key named remote_url but reads result.clone_path — must not fire (B6 complement)."""
    recipe = _make_recipe(
        {
            "clone": RecipeStep(
                tool="run_python",
                python=_CLONE_REPO,
                with_args={"strategy": "clone_local", "run_name": "t"},
                # Key is named remote_url but expression does NOT read result.remote_url.
                capture={"remote_url": "${{ result.clone_path }}"},
            ),
        }
    )
    findings = run_semantic_rules(recipe)
    matching = [f for f in findings if f.rule == _RULE]
    assert matching == []


@pytest.mark.parametrize("recipe_path", list(builtin_recipes_dir().rglob("*.yaml")))
def test_bundled_recipes_pass_new_rule(recipe_path: Path) -> None:
    """Every bundled recipe YAML must emit zero clone-local-strategy findings (B7)."""
    recipe = load_recipe(recipe_path)
    findings = run_semantic_rules(recipe)
    matching = [f for f in findings if f.rule == _RULE]
    assert matching == [], f"{recipe_path}: {matching}"
