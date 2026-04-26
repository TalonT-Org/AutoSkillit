"""Tests: validate_recipe errors include /autoskillit:write-recipe skill hints."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.validator import validate_recipe
from tests.recipe.conftest import VALID_RECIPE, _write_yaml

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

_HINT = "write-recipe"


class TestValidatorSkillHints:
    def test_missing_name_error_includes_hint(self, tmp_path: Path) -> None:
        """'Recipe must have a name' error includes write-recipe hint."""
        data = {**VALID_RECIPE, "name": ""}
        wf = load_recipe(_write_yaml(tmp_path / "r.yaml", data))
        errors = validate_recipe(wf)
        name_errors = [e for e in errors if "name" in e.lower() and "must have" in e.lower()]
        assert name_errors, f"Expected a name-required error, got: {errors}"
        assert any(_HINT in e for e in name_errors), (
            f"Expected write-recipe hint in name error, got: {name_errors}"
        )

    def test_missing_steps_error_includes_hint(self, tmp_path: Path) -> None:
        """'Recipe must have at least one step' error includes write-recipe hint."""
        data = {"name": "no-steps", "description": "test", "kitchen_rules": ["rule"]}
        wf = load_recipe(_write_yaml(tmp_path / "r.yaml", data))
        errors = validate_recipe(wf)
        step_errors = [e for e in errors if "step" in e.lower() and "at least" in e.lower()]
        assert step_errors, f"Expected a steps-required error, got: {errors}"
        assert any(_HINT in e for e in step_errors), (
            f"Expected write-recipe hint in steps error, got: {step_errors}"
        )

    def test_missing_kitchen_rules_error_includes_hint(self, tmp_path: Path) -> None:
        """'Recipe has no kitchen_rules' error includes write-recipe hint."""
        data = {k: v for k, v in VALID_RECIPE.items() if k != "kitchen_rules"}
        wf = load_recipe(_write_yaml(tmp_path / "r.yaml", data))
        errors = validate_recipe(wf)
        kr_errors = [e for e in errors if "kitchen_rules" in e]
        assert kr_errors, f"Expected a kitchen_rules error, got: {errors}"
        assert any(_HINT in e for e in kr_errors), (
            f"Expected write-recipe hint in kitchen_rules error, got: {kr_errors}"
        )

    def test_capture_no_template_error_includes_hint(self, tmp_path: Path) -> None:
        """Capture value with no result template expression includes write-recipe hint."""
        data = {
            **VALID_RECIPE,
            "steps": {
                "step1": {
                    "tool": "run_cmd",
                    "with": {"cmd": "echo hi"},
                    "capture": {"my_output": "no_template_here"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "r.yaml", data))
        errors = validate_recipe(wf)
        cap_errors = [e for e in errors if "capture" in e and "result" in e]
        assert cap_errors, f"Expected a capture expression error, got: {errors}"
        assert any(_HINT in e for e in cap_errors), (
            f"Expected write-recipe hint in capture error, got: {cap_errors}"
        )

    def test_undeclared_input_error_includes_hint(self, tmp_path: Path) -> None:
        """Undeclared input reference error includes write-recipe hint."""
        data = {
            "name": "test",
            "description": "test",
            "kitchen_rules": ["rule"],
            "steps": {
                "step1": {
                    "tool": "run_cmd",
                    "with": {"cmd": "${{ inputs.nonexistent_input }}"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "r.yaml", data))
        errors = validate_recipe(wf)
        input_errors = [e for e in errors if "undeclared input" in e]
        assert input_errors, f"Expected an undeclared-input error, got: {errors}"
        assert any(_HINT in e for e in input_errors), (
            f"Expected write-recipe hint in undeclared-input error, got: {input_errors}"
        )

    def test_valid_recipe_has_no_hint_errors(self, tmp_path: Path) -> None:
        """A valid recipe produces zero validation errors (sanity check)."""
        wf = load_recipe(_write_yaml(tmp_path / "r.yaml", VALID_RECIPE))
        errors = validate_recipe(wf)
        assert errors == [], f"Valid recipe should have no errors, got: {errors}"
