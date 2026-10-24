"""Tests for step-identity-mismatch semantic validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import Severity
from autoskillit.recipe import make_validation_context
from autoskillit.recipe._api import load_and_validate
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.rules.rules_tools import STEP_IDENTITY_MISMATCH_RULE
from autoskillit.recipe.schema import Recipe, RecipeStep
from tests.recipe.conftest import identity_suggestions as _identity_suggestions

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RUN_SKILL_KEY = "decompose_phases"


def _make_recipe(
    step_name: str,
    tool: str,
    with_args: dict[str, Any],
    declared_with_args: dict[str, Any],
) -> Recipe:
    return Recipe(
        name="step-identity-test",
        description="Test recipe for step identity validation.",
        version="0.2.0",
        kitchen_rules="test",
        steps={
            step_name: RecipeStep(
                tool=tool,
                with_args=with_args,
                declared_with_args=declared_with_args,
            ),
        },
    )


def _identity_findings(recipe: Recipe):
    ctx = make_validation_context(recipe)
    return [
        finding
        for finding in run_semantic_rules(ctx)
        if finding.rule == STEP_IDENTITY_MISMATCH_RULE
    ]


@pytest.mark.parametrize(
    ("effective_value", "declared_value", "map_terms", "bad_values"),
    [
        ("decompose", "decompose", (("with", "effective"), ("declared",)), ("decompose",)),
        (
            "decompose",
            _RUN_SKILL_KEY,
            (("with", "effective"),),
            ("decompose",),
        ),
        (
            _RUN_SKILL_KEY,
            "decompose",
            (("declared",),),
            ("decompose",),
        ),
    ],
)
def test_divergent_run_skill_identity_is_an_error(
    effective_value: str,
    declared_value: str,
    map_terms: tuple[tuple[str, ...], ...],
    bad_values: tuple[str, ...],
) -> None:
    base_args = {
        "skill_command": "/autoskillit:make-groups",
        "skill_inputs": {"source_doc": "/tmp/doc.md"},
        "cwd": "/tmp/ws",
    }
    findings = _identity_findings(
        _make_recipe(
            _RUN_SKILL_KEY,
            "run_skill",
            {**base_args, "step_name": effective_value},
            {**base_args, "step_name": declared_value},
        )
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity is Severity.ERROR
    assert finding.step_name == _RUN_SKILL_KEY
    assert _RUN_SKILL_KEY in finding.message
    for terms in map_terms:
        assert any(term in finding.message for term in terms)
    for value in bad_values:
        assert value in finding.message


def test_divergent_non_skill_tool_identity_is_an_error() -> None:
    args = {"step_name": "bootstrap_clone"}
    findings = _identity_findings(_make_recipe("clone", "bootstrap_clone", args, dict(args)))

    assert len(findings) == 1
    assert findings[0].severity is Severity.ERROR
    assert findings[0].step_name == "clone"


def test_target_step_reference_is_not_identity() -> None:
    args = {"step_name": "some_other_step"}
    recipe = _make_recipe("record_result", "record_pipeline_step", args, dict(args))

    assert _identity_findings(recipe) == []


def _identity_recipe_yaml(step_identity: str, *, guarded: bool) -> str:
    ingredient_block = (
        "ingredients:\n"
        "  enabled:\n"
        "    description: Run the optional step.\n"
        '    default: "false"\n'
        "    type: boolean\n"
        if guarded
        else ""
    )
    guard_fields = (
        "    optional: true\n    skip_when_false: inputs.enabled\n    on_skip: done_step\n"
        if guarded
        else ""
    )
    return (
        "name: step-identity-pair\n"
        "description: Paired control for step identity validation.\n"
        "kitchen_rules:\n  - Use declared routes.\n"
        f"{ingredient_block}"
        "steps:\n"
        "  run_step:\n"
        "    tool: run_cmd\n"
        "    with:\n"
        "      cmd: echo hi\n"
        "      cwd: /tmp\n"
        f"      step_name: {step_identity}\n"
        f"{guard_fields}"
        "    on_success: done_step\n"
        "    on_failure: escalate\n"
        "  done_step:\n"
        "    action: stop\n"
        "    message: Emit the L3 sentinel block with success=true and reason=Completed.\n"
    )


def _stage_recipe_pair(tmp_path: Path, *, guarded: bool) -> tuple[Path, Path]:
    project_dirs = (tmp_path / "control", tmp_path / "divergent")
    for project_dir, identity in zip(project_dirs, ("run_step", "other_step"), strict=True):
        recipes_dir = project_dir / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "step-identity-pair.yaml").write_text(
            _identity_recipe_yaml(identity, guarded=guarded)
        )
    return project_dirs


def test_identity_mismatch_alone_invalidates_a_recipe(tmp_path: Path) -> None:
    control_project, divergent_project = _stage_recipe_pair(tmp_path, guarded=False)

    control = load_and_validate("step-identity-pair", project_dir=control_project)
    divergent = load_and_validate("step-identity-pair", project_dir=divergent_project)

    assert control["valid"] is True, control.get("suggestions")
    assert divergent["valid"] is False
    assert any(finding.get("severity") == "error" for finding in _identity_suggestions(divergent))


@pytest.mark.parametrize("enabled", [False, True], ids=["pruned", "retained"])
def test_gated_identity_mismatch_is_rejected_before_pruning(tmp_path: Path, enabled: bool) -> None:
    control_project, divergent_project = _stage_recipe_pair(tmp_path, guarded=True)
    overrides = {"enabled": str(enabled).lower()}
    control = load_and_validate(
        "step-identity-pair", project_dir=control_project, ingredient_overrides=overrides
    )
    divergent = load_and_validate(
        "step-identity-pair", project_dir=divergent_project, ingredient_overrides=overrides
    )
    identity_findings = _identity_suggestions(divergent)

    assert control["valid"] is True, control.get("suggestions")
    assert divergent["valid"] is False
    assert len(identity_findings) == 1
    assert identity_findings[0]["step"] == "run_step"
    assert identity_findings[0]["severity"] == "error"
