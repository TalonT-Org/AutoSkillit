"""Tests for recipe structural validation — TestValidateRecipe."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from autoskillit.recipe.io import (
    _parse_recipe,
    builtin_recipes_dir,
    load_recipe,
)
from autoskillit.recipe.schema import Recipe, RecipeStep
from autoskillit.recipe.validator import validate_recipe
from tests.recipe.conftest import VALID_RECIPE, _write_yaml

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


# ---------------------------------------------------------------------------
# TestValidateRecipe — migrated from test_recipe_parser.py
# ---------------------------------------------------------------------------


class TestValidateRecipe:
    def test_valid_recipe_no_errors(self, tmp_path: Path) -> None:
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", VALID_RECIPE))
        errors = validate_recipe(wf)
        assert errors == []

    # WF2
    def test_recipe_requires_name(self, tmp_path: Path) -> None:
        data = {**VALID_RECIPE, "name": ""}
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        errors = validate_recipe(wf)
        assert any("name" in e.lower() for e in errors)

    # WF3
    def test_recipe_requires_steps(self, tmp_path: Path) -> None:
        data = {"name": "no-steps", "description": "Missing steps", "kitchen_rules": ["test"]}
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        errors = validate_recipe(wf)
        assert any("step" in e.lower() for e in errors)

    # WF5
    def test_goto_targets_validated(self, tmp_path: Path) -> None:
        data = {
            "name": "bad-goto",
            "description": "Invalid goto",
            "kitchen_rules": ["test"],
            "steps": {
                "start": {"tool": "run_cmd", "on_success": "nonexistent"},
                "end": {"action": "stop", "message": "Done."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        errors = validate_recipe(wf)
        assert any("nonexistent" in e for e in errors)

    # WF6
    def test_builtin_recipes_valid(self) -> None:
        bd = builtin_recipes_dir()
        yamls = list(bd.glob("*.yaml"))
        assert len(yamls) >= 4
        for f in yamls:
            wf = load_recipe(f)
            errors = validate_recipe(wf)
            assert errors == [], f"Validation errors in {f.name}: {errors}"

    # WF10
    def test_terminal_step_has_message(self, tmp_path: Path) -> None:
        data = {
            "name": "no-msg",
            "description": "Terminal without message",
            "kitchen_rules": ["test"],
            "steps": {"end": {"action": "stop"}},
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        errors = validate_recipe(wf)
        assert any("message" in e.lower() for e in errors)

    def test_step_needs_tool_or_action(self, tmp_path: Path) -> None:
        data = {
            "name": "bad-step",
            "description": "Neither tool nor action",
            "kitchen_rules": ["test"],
            "steps": {"empty": {"note": "just a note"}},
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        errors = validate_recipe(wf)
        assert any("tool" in e and "action" in e for e in errors)

    def test_input_reference_validation(self, tmp_path: Path) -> None:
        data = {
            "name": "bad-ref",
            "description": "References undeclared input",
            "kitchen_rules": ["test"],
            "steps": {
                "run": {"tool": "run_cmd", "with": {"cmd": "${{ inputs.missing_input }}"}},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        errors = validate_recipe(wf)
        assert any("missing_input" in e for e in errors)

    def test_retry_block_raises_on_load(self, tmp_path: Path) -> None:
        """The old retry: block is no longer supported — loading raises ValueError."""
        import pytest

        data = {
            "name": "bad-retry-block",
            "description": "Old retry block is unsupported",
            "kitchen_rules": ["test"],
            "steps": {
                "impl": {
                    "tool": "run_skill",
                    "retry": {
                        "max_attempts": 3,
                        "on": "needs_retry",
                        "on_exhausted": "fail",
                    },
                },
                "fail": {"action": "stop", "message": "Failed."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        with pytest.raises(ValueError, match="retry.*no longer supported"):
            load_recipe(f)

    def test_step_rejects_both_python_and_tool(self, tmp_path: Path) -> None:
        data = {
            "name": "bad",
            "description": "Both python and tool",
            "kitchen_rules": ["test"],
            "steps": {"run": {"python": "mod.fn", "tool": "run_cmd"}},
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        assert any("python" in e and "tool" in e for e in errors)

    def test_python_step_requires_dotted_path(self, tmp_path: Path) -> None:
        data = {
            "name": "bad-path",
            "description": "No dot",
            "kitchen_rules": ["test"],
            "steps": {"check": {"python": "bare_name"}},
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        assert any("dotted" in e.lower() or "module" in e.lower() for e in errors)

    # CAP3
    def test_capture_result_refs_valid(self, tmp_path: Path) -> None:
        data = {
            "name": "cap-valid",
            "description": "Valid captures",
            "kitchen_rules": ["test"],
            "steps": {
                "run": {
                    "tool": "run_skill",
                    "capture": {
                        "wp": "${{ result.worktree_path }}",
                        "ctx": "${{ result.failure_context }}",
                    },
                },
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        assert not any("capture" in e for e in errors)

    # CAP4
    def test_capture_non_result_namespace_rejected(self, tmp_path: Path) -> None:
        data = {
            "name": "cap-bad-ns",
            "description": "Bad namespace",
            "kitchen_rules": ["test"],
            "steps": {
                "run": {"tool": "run_cmd", "capture": {"foo": "${{ inputs.bar }}"}},
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        assert any("result" in e and "capture" in e for e in errors)

    # CAP5
    def test_capture_literal_value_rejected(self, tmp_path: Path) -> None:
        data = {
            "name": "cap-literal",
            "description": "Literal capture",
            "kitchen_rules": ["test"],
            "steps": {
                "run": {"tool": "run_cmd", "capture": {"foo": "literal string"}},
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        assert any("capture" in e and "result" in e for e in errors)

    # CAP6
    def test_context_ref_to_captured_var_valid(self, tmp_path: Path) -> None:
        data = {
            "name": "ctx-valid",
            "description": "Valid context ref",
            "kitchen_rules": ["test"],
            "steps": {
                "impl": {
                    "tool": "run_skill",
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_success": "test",
                },
                "test": {
                    "tool": "test_check",
                    "with": {"worktree_path": "${{ context.worktree_path }}"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        assert not any("context" in e for e in errors)

    # CAP7
    def test_context_ref_to_uncaptured_var_rejected(self, tmp_path: Path) -> None:
        data = {
            "name": "ctx-bad",
            "description": "Uncaptured ref",
            "kitchen_rules": ["test"],
            "steps": {
                "test": {
                    "tool": "test_check",
                    "with": {"worktree_path": "${{ context.nonexistent }}"},
                },
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        assert any("nonexistent" in e and "context" in e for e in errors)

    # CAP8
    def test_context_forward_reference_rejected(self, tmp_path: Path) -> None:
        data = {
            "name": "ctx-fwd",
            "description": "Forward ref",
            "kitchen_rules": ["test"],
            "steps": {
                "check": {
                    "tool": "test_check",
                    "with": {"worktree_path": "${{ context.wp }}"},
                    "on_success": "done",
                },
                "produce": {
                    "tool": "run_skill",
                    "capture": {"wp": "${{ result.worktree_path }}"},
                },
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        assert any("wp" in e and "context" in e for e in errors)

    # CON1
    def test_recipe_schema_supports_kitchen_rules(self) -> None:
        field_names = {f.name for f in dataclasses.fields(Recipe)}
        assert "kitchen_rules" in field_names

    # CON3
    def test_validate_recipe_warns_missing_kitchen_rules(self, tmp_path: Path) -> None:
        data = {**VALID_RECIPE}
        data.pop("kitchen_rules", None)
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        warnings = [e for e in errors if "kitchen_rules" in e.lower()]
        assert warnings

    # CON4
    def test_bundled_recipes_have_kitchen_rules(self) -> None:
        wf_dir = builtin_recipes_dir()
        failures = []
        for path in sorted(wf_dir.glob("*.yaml")):
            wf = load_recipe(path)
            if not wf.kitchen_rules:
                failures.append(f"{path.name}: missing kitchen_rules")
        assert not failures

    # T_OR2
    def test_on_result_and_on_success_mutually_exclusive(self, tmp_path: Path) -> None:
        data = {
            "name": "conflict-recipe",
            "description": "Both on_result and on_success",
            "kitchen_rules": ["test"],
            "steps": {
                "classify": {
                    "tool": "classify_fix",
                    "on_result": {
                        "field": "restart_scope",
                        "routes": {"full_restart": "done"},
                    },
                    "on_success": "done",
                    "on_failure": "escalate",
                },
                "done": {"action": "stop", "message": "Done."},
                "escalate": {"action": "stop", "message": "Escalating."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        errors = validate_recipe(wf)
        assert any("on_result" in e and "on_success" in e for e in errors)

    # T_OR6
    def test_on_result_route_done_is_valid(self, tmp_path: Path) -> None:
        data = {
            "name": "done-route-recipe",
            "description": "Route to done",
            "kitchen_rules": ["test"],
            "steps": {
                "classify": {
                    "tool": "classify_fix",
                    "on_result": {
                        "field": "restart_scope",
                        "routes": {"full_restart": "done", "partial_restart": "done"},
                    },
                    "on_failure": "escalate",
                },
                "escalate": {"action": "stop", "message": "Escalating."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        errors = validate_recipe(wf)
        assert errors == []

    # VER3
    def test_version_does_not_cause_validation_errors(self) -> None:
        data = {
            "name": "version-test-recipe",
            "description": "A recipe for testing the version field",
            "kitchen_rules": ["Only use AutoSkillit MCP tools during pipeline execution"],
            "steps": {
                "do_it": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            },
            "autoskillit_version": "0.2.0",
        }
        wf = _parse_recipe(data)
        errors = validate_recipe(wf)
        assert errors == []

    def test_validator_rejects_zero_stale_threshold(self) -> None:
        recipe = Recipe(
            name="test",
            description="test",
            steps={"s": RecipeStep(tool="run_skill", on_success="done", stale_threshold=0)},
            kitchen_rules=["test"],
        )
        errors = validate_recipe(recipe)
        assert any("stale_threshold" in e for e in errors)

    def test_validator_rejects_negative_stale_threshold(self) -> None:
        recipe = Recipe(
            name="test",
            description="test",
            steps={"s": RecipeStep(tool="run_skill", on_success="done", stale_threshold=-1)},
            kitchen_rules=["test"],
        )
        errors = validate_recipe(recipe)
        assert any("stale_threshold" in e for e in errors)

    def test_validator_accepts_positive_stale_threshold(self) -> None:
        recipe = Recipe(
            name="test",
            description="test",
            steps={"s": RecipeStep(tool="run_skill", on_success="done", stale_threshold=2400)},
            kitchen_rules=["test"],
        )
        errors = validate_recipe(recipe)
        assert not any("stale_threshold" in e for e in errors)

    def test_validator_rejects_negative_idle_output_timeout(self) -> None:
        recipe = Recipe(
            name="test",
            description="test",
            steps={"s": RecipeStep(tool="run_skill", on_success="done", idle_output_timeout=-1)},
            kitchen_rules=["test"],
        )
        errors = validate_recipe(recipe)
        assert any("idle_output_timeout" in e for e in errors)

    def test_validator_accepts_zero_idle_output_timeout(self) -> None:
        # 0 = disabled, must NOT be rejected
        recipe = Recipe(
            name="test",
            description="test",
            steps={"s": RecipeStep(tool="run_skill", on_success="done", idle_output_timeout=0)},
            kitchen_rules=["test"],
        )
        errors = validate_recipe(recipe)
        assert not any("idle_output_timeout" in e for e in errors)

    def test_validator_accepts_positive_idle_output_timeout(self) -> None:
        recipe = Recipe(
            name="test",
            description="test",
            steps={"s": RecipeStep(tool="run_skill", on_success="done", idle_output_timeout=120)},
            kitchen_rules=["test"],
        )
        errors = validate_recipe(recipe)
        assert not any("idle_output_timeout" in e for e in errors)
