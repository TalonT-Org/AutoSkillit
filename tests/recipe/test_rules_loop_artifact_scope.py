"""Tests for recipe/rules_loop_artifact_scope.py semantic rule (1c)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(
        name="test-loop-artifact-scope",
        description="Test recipe for loop artifact scope rule.",
        version="0.2.0",
        kitchen_rules=["test"],
        steps=steps,
    )


def _load_resolved_loop_recipe(
    tmp_path: Path,
    *,
    output_dir: str,
    temp_root: str,
    prefer_json: bool,
) -> Recipe:
    yaml_path = tmp_path / "loop.yaml"
    parsed = {
        "name": "resolved-loop-artifact-scope",
        "description": "Resolved loop artifact scope fixture.",
        "version": "0.2.0",
        "kitchen_rules": ["test"],
        "steps": {
            "start": {
                "tool": "run_cmd",
                "with": {"cmd": "echo start"},
                "on_success": "review",
            },
            "review": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:review-approach branch",
                    "output_dir": output_dir,
                },
                "capture": {"verdict": "${{ result.verdict }}"},
                "on_success": "check_loop",
            },
            "check_loop": {
                "tool": "run_cmd",
                "with": {"cmd": "echo loop"},
                "on_success": "review",
            },
        },
    }
    yaml_path.write_text(
        f"""
name: resolved-loop-artifact-scope
description: Resolved loop artifact scope fixture.
version: 0.2.0
kitchen_rules: [test]
steps:
  start:
    tool: run_cmd
    with:
      cmd: echo start
    on_success: review
  review:
    tool: run_skill
    with:
      skill_command: /autoskillit:review-approach branch
      output_dir: "{output_dir}"
    capture:
      verdict: "${{{{ result.verdict }}}}"
    on_success: check_loop
  check_loop:
    tool: run_cmd
    with:
      cmd: echo loop
    on_success: review
""",
        encoding="utf-8",
    )
    if prefer_json:
        json_path = yaml_path.with_suffix(".json")
        json_path.write_text(json.dumps(parsed), encoding="utf-8")
        newer = yaml_path.stat().st_mtime_ns + 1_000_000
        os.utime(json_path, ns=(newer, newer))
    return load_recipe(yaml_path, temp_dir_relpath=temp_root)


def test_rule_fires_when_run_skill_in_cycle_has_static_output_dir() -> None:
    """Rule fires ERROR when run_skill step in a cycle has a static output_dir."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo start"},
                on_success="review",
            ),
            "review": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:review-approach branch",
                    "output_dir": "{{AUTOSKILLIT_TEMP}}/review-pr",
                },
                capture={"verdict": "${{ result.verdict }}"},
                on_success="check_loop",
            ),
            "check_loop": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo loop"},
                on_success="review",
            ),
        }
    )
    findings = run_semantic_rules(recipe)
    loop_findings = [
        f for f in findings if f.rule == "loop-iterated-step-requires-iteration-scoped-output"
    ]
    assert len(loop_findings) == 1
    assert loop_findings[0].severity == Severity.ERROR
    assert loop_findings[0].step_name == "review"


def test_rule_does_not_fire_when_output_dir_is_iter_scoped() -> None:
    """Rule does NOT fire when output_dir includes a ${{ context. variable reference."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo start"},
                on_success="review",
            ),
            "review": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:review-approach branch",
                    "output_dir": "{{AUTOSKILLIT_TEMP}}/review-pr/iter_${{ context.loop_count }}",
                },
                capture={"verdict": "${{ result.verdict }}"},
                on_success="check_loop",
            ),
            "check_loop": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo loop"},
                on_success="review",
            ),
        }
    )
    findings = run_semantic_rules(recipe)
    loop_findings = [
        f for f in findings if f.rule == "loop-iterated-step-requires-iteration-scoped-output"
    ]
    assert len(loop_findings) == 0


@pytest.mark.parametrize("prefer_json", [False, True], ids=["yaml", "json"])
@pytest.mark.parametrize("temp_root", [".autoskillit/temp", "/custom temp root"])
def test_rule_uses_bound_origin_after_temp_root_resolution(
    tmp_path: Path,
    *,
    prefer_json: bool,
    temp_root: str,
) -> None:
    recipe = _load_resolved_loop_recipe(
        tmp_path,
        output_dir="{{AUTOSKILLIT_TEMP}}/review-pr",
        temp_root=temp_root,
        prefer_json=prefer_json,
    )

    finding = next(
        finding
        for finding in run_semantic_rules(recipe)
        if finding.rule == "loop-iterated-step-requires-iteration-scoped-output"
    )

    assert f"{temp_root}/review-pr" in finding.message
    assert "{{AUTOSKILLIT_TEMP}}" not in finding.message


@pytest.mark.parametrize("prefer_json", [False, True], ids=["yaml", "json"])
def test_resolved_iteration_dependency_survives_yaml_and_json_loading(
    tmp_path: Path,
    *,
    prefer_json: bool,
) -> None:
    recipe = _load_resolved_loop_recipe(
        tmp_path,
        output_dir=("{{AUTOSKILLIT_TEMP}}/review-pr/iter_${{ context.review_loop_count }}"),
        temp_root="/custom temp root",
        prefer_json=prefer_json,
    )

    findings = run_semantic_rules(recipe)

    assert not any(
        finding.rule == "loop-iterated-step-requires-iteration-scoped-output"
        for finding in findings
    )


def test_rule_does_not_fire_for_run_python_in_cycle_with_static_output_dir() -> None:
    """Rule does NOT fire for run_python steps (run_python is not in SKILL_TOOLS)."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(
                tool="run_python",
                with_args={
                    "callable": "autoskillit.smoke_utils.check_review_loop",
                    "output_dir": "{{AUTOSKILLIT_TEMP}}/review-pr",
                },
                on_success="loop_back",
            ),
            "loop_back": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo back"},
                on_success="start",
            ),
        }
    )
    findings = run_semantic_rules(recipe)
    loop_findings = [
        f for f in findings if f.rule == "loop-iterated-step-requires-iteration-scoped-output"
    ]
    assert len(loop_findings) == 0


def test_rule_does_not_fire_for_run_skill_not_in_cycle() -> None:
    """Rule does NOT fire for run_skill steps that are not in any loop cycle."""
    recipe = _make_recipe(
        {
            "start": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo start"},
                on_success="review",
            ),
            "review": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:review-pr branch",
                    "output_dir": "{{AUTOSKILLIT_TEMP}}/review-pr",
                },
                capture={"verdict": "${{ result.verdict }}"},
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="Done"),
        }
    )
    findings = run_semantic_rules(recipe)
    loop_findings = [
        f for f in findings if f.rule == "loop-iterated-step-requires-iteration-scoped-output"
    ]
    assert len(loop_findings) == 0
