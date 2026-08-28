"""Structural validation coverage for server-authoritative step guards."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import Recipe, RecipeStep
from autoskillit.recipe.validator import run_semantic_rules, validate_recipe_structure

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _recipe(guard: RecipeStep) -> Recipe:
    return Recipe(
        name="guarded",
        description="guard test",
        steps={
            "guard": guard,
            "next": RecipeStep(action="stop", message="done"),
        },
    )


@pytest.mark.parametrize("value", ["is_silent_type", "${{ context.value }}"])
def test_skip_when_true_requires_a_context_reference(value: str) -> None:
    errors = validate_recipe_structure(_recipe(RecipeStep(tool="run_skill", skip_when_true=value)))
    assert any("skip_when_true must use 'context.<name>' format" in error for error in errors)


def test_skip_when_true_requires_an_on_success_bypass() -> None:
    errors = validate_recipe_structure(
        _recipe(RecipeStep(tool="run_skill", skip_when_true="context.is_silent_type"))
    )
    assert any("missing required on_success bypass target" in error for error in errors)


def test_skip_when_true_rejects_ambiguous_false_guard() -> None:
    errors = validate_recipe_structure(
        _recipe(
            RecipeStep(
                tool="run_skill",
                skip_when_true="context.is_silent_type",
                skip_when_false="inputs.enabled",
                on_skip="next",
                on_success="next",
            )
        )
    )
    assert any("guards are ambiguous" in error for error in errors)


def test_valid_skip_when_true_has_no_guard_errors() -> None:
    errors = validate_recipe_structure(
        _recipe(
            RecipeStep(
                tool="run_skill",
                skip_when_true="context.is_silent_type",
                on_success="next",
            )
        )
    )
    assert not [error for error in errors if "skip_when_true" in error]


def _capture_consumer_findings(recipe: Recipe):
    return [
        finding
        for finding in run_semantic_rules(recipe)
        if finding.rule == "skip-when-true-capture-consumers"
    ]


def test_bundled_research_design_passes_skip_when_true_capture_consumers() -> None:
    recipe = load_recipe(builtin_recipes_dir() / "research-design.yaml")
    assert _capture_consumer_findings(recipe) == []


def test_skip_when_true_capture_consumers_rejects_missing_optional_context_refs() -> None:
    recipe = Recipe(
        name="missing-optional-ref",
        description="guarded capture consumer",
        steps={
            "produce_guard": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "printf true"},
                capture={"is_silent_type": "${{ result.stdout }}"},
                on_success="guarded",
            ),
            "guarded": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:investigate"},
                skip_when_true="context.is_silent_type",
                capture={"artifact_path": "${{ result.artifact_path }}"},
                on_success="consumer",
            ),
            "consumer": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "test -f '${{ context.artifact_path }}'"},
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
    )

    findings = _capture_consumer_findings(recipe)
    assert len(findings) == 1
    assert findings[0].severity is Severity.ERROR
    assert findings[0].step_name == "consumer"
    assert "artifact_path" in findings[0].message


@pytest.mark.parametrize(
    "consumer",
    [
        RecipeStep(
            tool="run_skill",
            with_args={
                "skill_command": "/autoskillit:investigate",
                "skill_inputs": {"artifact_path": "${{ context.artifact_path }}"},
            },
            on_success="done",
        ),
        RecipeStep(action="stop", message="artifact: ${{ context.artifact_path }}"),
    ],
    ids=["nested-with-args", "message"],
)
def test_skip_when_true_capture_consumers_scan_nested_and_message_fields(
    consumer: RecipeStep,
) -> None:
    recipe = Recipe(
        name="nested-guarded-consumer",
        description="guarded capture consumer",
        steps={
            "guarded": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:investigate"},
                skip_when_true="context.should_skip",
                capture={"artifact_path": "${{ result.artifact_path }}"},
                on_success="consumer",
            ),
            "consumer": consumer,
            "done": RecipeStep(action="stop", message="done"),
        },
    )

    findings = _capture_consumer_findings(recipe)

    assert len(findings) == 1
    assert findings[0].step_name == "consumer"
    assert "artifact_path" in findings[0].message
