"""Tests for recipe YAML parsing and validation."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from autoskillit.recipe_parser import (
    DataFlowReport,
    Recipe,
    RecipeStep,
    StepResultRoute,
    _build_step_graph,
    _parse_recipe,
    _parse_step,
    analyze_dataflow,
    builtin_recipes_dir,
    list_recipes,
    load_recipe,
    validate_recipe,
)
from autoskillit.types import RETRY_RESPONSE_FIELDS, RecipeSource

VALID_RECIPE = {
    "name": "test-recipe",
    "description": "A test recipe",
    "ingredients": {
        "test_dir": {"description": "Dir to test", "required": True},
        "branch": {"description": "Branch", "default": "main"},
    },
    "kitchen_rules": ["NEVER use native tools"],
    "steps": {
        "run_tests": {
            "tool": "test_check",
            "with": {"worktree_path": "${{ inputs.test_dir }}"},
            "on_success": "done",
            "on_failure": "escalate",
        },
        "done": {"action": "stop", "message": "Tests passed."},
        "escalate": {"action": "stop", "message": "Need help."},
    },
}


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.dump(data, default_flow_style=False))
    return path


class TestRecipeParser:
    # WF1
    def test_load_valid_recipe(self, tmp_path: Path) -> None:
        f = _write_yaml(tmp_path / "recipe.yaml", VALID_RECIPE)
        wf = load_recipe(f)
        assert wf.name == "test-recipe"
        assert wf.description == "A test recipe"
        assert "test_dir" in wf.ingredients
        assert wf.ingredients["test_dir"].required is True
        assert wf.ingredients["branch"].default == "main"
        assert "run_tests" in wf.steps
        assert wf.steps["run_tests"].tool == "test_check"
        assert wf.steps["run_tests"].with_args["worktree_path"] == "${{ inputs.test_dir }}"
        assert wf.steps["done"].action == "stop"

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

    # WF4
    def test_ingredient_defaults_applied(self, tmp_path: Path) -> None:
        f = _write_yaml(tmp_path / "recipe.yaml", VALID_RECIPE)
        wf = load_recipe(f)
        assert wf.ingredients["branch"].default == "main"
        assert wf.ingredients["branch"].required is False

    # WF5
    def test_goto_targets_validated(self, tmp_path: Path) -> None:
        data = {
            "name": "bad-goto",
            "description": "Invalid goto",
            "kitchen_rules": ["test"],
            "steps": {
                "start": {
                    "tool": "run_cmd",
                    "on_success": "nonexistent",
                },
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

    # WF7
    def test_list_recipes_finds_builtins(self, tmp_path: Path) -> None:
        recipes = list_recipes(tmp_path).items
        names = {w.name for w in recipes}
        assert "bugfix-loop" in names
        assert "implementation-pipeline" in names
        assert "audit-and-fix" in names
        assert "investigate-first" in names

    # WF8
    def test_project_recipe_overrides_builtin(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / ".autoskillit" / "recipes"
        wf_dir.mkdir(parents=True)
        override = {**VALID_RECIPE, "name": "bugfix-loop", "description": "Custom override"}
        _write_yaml(wf_dir / "bugfix-loop.yaml", override)

        recipes = list_recipes(tmp_path).items
        match = next(w for w in recipes if w.name == "bugfix-loop")
        assert match.source == RecipeSource.PROJECT
        assert match.description == "Custom override"

    # WF9
    def test_step_with_retry_parsed(self, tmp_path: Path) -> None:
        data = {
            "name": "retry-recipe",
            "description": "Has retry",
            "kitchen_rules": ["test"],
            "steps": {
                "impl": {
                    "tool": "run_skill_retry",
                    "retry": {"max_attempts": 5, "on": "needs_retry", "on_exhausted": "fail"},
                },
                "fail": {"action": "stop", "message": "Failed."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        assert wf.steps["impl"].retry is not None
        assert wf.steps["impl"].retry.max_attempts == 5
        assert wf.steps["impl"].retry.on == "needs_retry"
        assert wf.steps["impl"].retry.on_exhausted == "fail"

    # WF10
    def test_terminal_step_has_message(self, tmp_path: Path) -> None:
        data = {
            "name": "no-msg",
            "description": "Terminal without message",
            "kitchen_rules": ["test"],
            "steps": {
                "end": {"action": "stop"},
            },
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
                "run": {
                    "tool": "run_cmd",
                    "with": {"cmd": "${{ inputs.missing_input }}"},
                },
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        errors = validate_recipe(wf)
        assert any("missing_input" in e for e in errors)

    def test_load_recipe_rejects_non_dict(self, tmp_path: Path) -> None:
        """YAML that parses to a non-dict must raise ValueError."""
        path = tmp_path / "list.yaml"
        path.write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="YAML mapping"):
            load_recipe(path)

    def test_list_recipes_reports_malformed_files(self, tmp_path: Path) -> None:
        """Malformed recipe files must produce error reports."""
        wf_dir = tmp_path / ".autoskillit" / "recipes"
        wf_dir.mkdir(parents=True)
        (wf_dir / "broken.yaml").write_text("{invalid: [unclosed\n")
        result = list_recipes(tmp_path)
        assert len(result.errors) >= 1

    # WF_SUM1
    def test_recipe_summary_defaults_to_empty(self) -> None:
        """Recipe dataclass has summary field defaulting to empty string."""
        wf = Recipe(name="test", description="desc")
        assert wf.summary == ""

    # WF_SUM2
    def test_parse_recipe_extracts_summary(self, tmp_path: Path) -> None:
        """_parse_recipe extracts summary from YAML data."""
        data = {**VALID_RECIPE, "summary": "run tests then merge"}
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        assert wf.summary == "run tests then merge"

    # WF_SUM3
    def test_builtin_recipes_summary_is_str(self) -> None:
        """All builtin recipes have summary as str (empty string when absent)."""
        bd = builtin_recipes_dir()
        for f in bd.glob("*.yaml"):
            wf = load_recipe(f)
            assert isinstance(wf.summary, str), f"{f.name}: summary is not str"

    def test_retry_on_field_is_valid_response_key(self, tmp_path: Path) -> None:
        """retry.on must reference a field that run_skill_retry actually returns."""
        for wf_info in list_recipes(tmp_path).items:
            wf = load_recipe(wf_info.path)
            for step_name, step in wf.steps.items():
                if step.retry and step.retry.on:
                    assert step.retry.on in RETRY_RESPONSE_FIELDS, (
                        f"Recipe '{wf.name}' step '{step_name}' retry.on='{step.retry.on}' "
                        f"is not a known response field: {RETRY_RESPONSE_FIELDS}"
                    )

    def test_retry_on_unknown_field_fails_validation(self, tmp_path: Path) -> None:
        """validate_recipe rejects retry.on that references unknown response field."""
        data = {
            "name": "bad-retry-on",
            "description": "Unknown retry.on field",
            "kitchen_rules": ["test"],
            "steps": {
                "impl": {
                    "tool": "run_skill_retry",
                    "retry": {
                        "max_attempts": 3,
                        "on": "nonexistent_field",
                        "on_exhausted": "fail",
                    },
                },
                "fail": {"action": "stop", "message": "Failed."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        errors = validate_recipe(wf)
        assert any("nonexistent_field" in e for e in errors)

    def test_python_step_parsed(self, tmp_path: Path) -> None:
        """RecipeStep.python is populated from YAML data."""
        data = {
            "name": "py-recipe",
            "description": "Has python step",
            "kitchen_rules": ["test"],
            "steps": {
                "check": {
                    "python": "mymod.check_fn",
                    "on_success": "done",
                    "on_failure": "fail",
                },
                "done": {"action": "stop", "message": "OK"},
                "fail": {"action": "stop", "message": "Failed"},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        assert wf.steps["check"].python == "mymod.check_fn"
        assert wf.steps["check"].tool is None
        assert wf.steps["check"].action is None

    def test_step_rejects_both_python_and_tool(self, tmp_path: Path) -> None:
        """Step with both python and tool is invalid."""
        data = {
            "name": "bad",
            "description": "Both python and tool",
            "kitchen_rules": ["test"],
            "steps": {"run": {"python": "mod.fn", "tool": "run_cmd"}},
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        assert any("python" in e and "tool" in e for e in errors)

    def test_step_accepts_python_alone(self, tmp_path: Path) -> None:
        """Step with only python discriminator is valid."""
        data = {
            "name": "ok",
            "description": "Python only",
            "kitchen_rules": ["test"],
            "steps": {
                "check": {"python": "mod.fn", "on_success": "done"},
                "done": {"action": "stop", "message": "OK"},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        assert errors == []

    def test_python_step_requires_dotted_path(self, tmp_path: Path) -> None:
        """python: value must contain at least one dot (module.function)."""
        data = {
            "name": "bad-path",
            "description": "No dot",
            "kitchen_rules": ["test"],
            "steps": {"check": {"python": "bare_name"}},
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        assert any("dotted" in e.lower() or "module" in e.lower() for e in errors)

    def test_python_step_with_args_validated(self, tmp_path: Path) -> None:
        """python step's with: args have input references validated."""
        data = {
            "name": "ref-recipe",
            "description": "Python with refs",
            "kitchen_rules": ["test"],
            "ingredients": {"plan_id": {"description": "Plan ID"}},
            "steps": {
                "check": {
                    "python": "mod.fn",
                    "with": {"plan_id": "${{ inputs.plan_id }}"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "OK"},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        assert errors == []

    # CAP1
    def test_capture_field_parsed(self, tmp_path: Path) -> None:
        """CAP1: capture dict is parsed from step YAML."""
        data = {
            "name": "cap-recipe",
            "description": "Capture test",
            "kitchen_rules": ["test"],
            "steps": {
                "run": {
                    "tool": "run_skill",
                    "with": {"cwd": "/tmp"},
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                },
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        assert wf.steps["run"].capture == {"worktree_path": "${{ result.worktree_path }}"}

    # CAP2
    def test_capture_defaults_empty(self, tmp_path: Path) -> None:
        """CAP2: step without capture has empty capture dict."""
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", VALID_RECIPE))
        for step in wf.steps.values():
            assert step.capture == {}

    # CAP3
    def test_capture_result_refs_valid(self, tmp_path: Path) -> None:
        """CAP3: capture values using result.* namespace produce no errors."""
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
        """CAP4: capture values must use result.* namespace."""
        data = {
            "name": "cap-bad-ns",
            "description": "Bad namespace",
            "kitchen_rules": ["test"],
            "steps": {
                "run": {
                    "tool": "run_cmd",
                    "capture": {"foo": "${{ inputs.bar }}"},
                },
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        assert any("result" in e and "capture" in e for e in errors)

        # Also reject context.* namespace in capture values
        data["steps"]["run"]["capture"] = {"foo": "${{ context.bar }}"}
        wf = load_recipe(_write_yaml(tmp_path / "recipe2.yaml", data))
        errors = validate_recipe(wf)
        assert any("result" in e and "capture" in e for e in errors)

    # CAP5
    def test_capture_literal_value_rejected(self, tmp_path: Path) -> None:
        """CAP5: capture values must contain ${{ result.X }} expression."""
        data = {
            "name": "cap-literal",
            "description": "Literal capture",
            "kitchen_rules": ["test"],
            "steps": {
                "run": {
                    "tool": "run_cmd",
                    "capture": {"foo": "literal string"},
                },
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        assert any("capture" in e and "result" in e for e in errors)

    # CAP6
    def test_context_ref_to_captured_var_valid(self, tmp_path: Path) -> None:
        """CAP6: ${{ context.X }} referencing a preceding capture is valid."""
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
        """CAP7: ${{ context.X }} where X is never captured is an error."""
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
        """CAP8: ${{ context.X }} referencing a variable captured by a later step is an error."""
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

    # CAP9
    def test_bundled_recipes_still_valid(self) -> None:
        """CAP9: existing bundled recipes pass validation with new capture rules."""
        bd = builtin_recipes_dir()
        for f in bd.glob("*.yaml"):
            wf = load_recipe(f)
            errors = validate_recipe(wf)
            assert errors == [], f"Regression in {f.name}: {errors}"

    # CAP10
    def test_multiple_captures_cumulative(self, tmp_path: Path) -> None:
        """CAP10: context.X can reference captures from any preceding step."""
        data = {
            "name": "cumulative",
            "description": "Multi-capture",
            "kitchen_rules": ["test"],
            "steps": {
                "step_a": {
                    "tool": "run_skill",
                    "capture": {"var_a": "${{ result.a }}"},
                    "on_success": "step_b",
                },
                "step_b": {
                    "tool": "run_skill",
                    "capture": {"var_b": "${{ result.b }}"},
                    "on_success": "step_c",
                },
                "step_c": {
                    "tool": "run_cmd",
                    "with": {
                        "cmd": "${{ context.var_a }} ${{ context.var_b }}",
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        assert not any("context" in e for e in errors)

    # CAP11
    def test_capture_dotted_result_path_valid(self, tmp_path: Path) -> None:
        """CAP11: result.nested.path in capture values is valid."""
        data = {
            "name": "dotted",
            "description": "Dotted result path",
            "kitchen_rules": ["test"],
            "steps": {
                "run": {
                    "tool": "run_cmd",
                    "capture": {"foo": "${{ result.data.worktree_path }}"},
                },
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        assert not any("capture" in e for e in errors)

    # T4
    def test_recipe_skill_commands_are_namespaced(self) -> None:
        """All skill_command values in recipe YAMLs use /autoskillit: namespace."""
        import autoskillit

        wf_dir = Path(autoskillit.__file__).parent / "recipes"
        for wf_path in wf_dir.glob("*.yaml"):
            content = wf_path.read_text()
            for match in re.finditer(r'skill_command:\s*"(/\S+)', content):
                ref = match.group(1)
                if "${{" in ref:
                    continue
                assert ref.startswith("/autoskillit:"), (
                    f"{wf_path.name}: {ref} should use /autoskillit: namespace"
                )

    # T_OR1
    def test_on_result_parsed(self, tmp_path: Path) -> None:
        """on_result block is parsed into StepResultRoute."""
        data = {
            "name": "result-recipe",
            "description": "Has on_result",
            "kitchen_rules": ["test"],
            "steps": {
                "classify": {
                    "tool": "classify_fix",
                    "on_result": {
                        "field": "restart_scope",
                        "routes": {
                            "full_restart": "investigate",
                            "partial_restart": "implement",
                        },
                    },
                    "on_failure": "escalate",
                },
                "investigate": {"action": "stop", "message": "Investigating."},
                "implement": {"action": "stop", "message": "Implementing."},
                "escalate": {"action": "stop", "message": "Escalating."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        assert wf.steps["classify"].on_result is not None
        assert isinstance(wf.steps["classify"].on_result, StepResultRoute)
        assert wf.steps["classify"].on_result.field == "restart_scope"
        assert wf.steps["classify"].on_result.routes == {
            "full_restart": "investigate",
            "partial_restart": "implement",
        }

    # T_OR2
    def test_on_result_and_on_success_mutually_exclusive(self, tmp_path: Path) -> None:
        """Having both on_result and on_success is a validation error."""
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

    # T_OR3
    def test_on_result_empty_field_rejected(self, tmp_path: Path) -> None:
        """on_result.field must be non-empty."""
        data = {
            "name": "empty-field-recipe",
            "description": "Empty on_result field",
            "kitchen_rules": ["test"],
            "steps": {
                "classify": {
                    "tool": "classify_fix",
                    "on_result": {
                        "field": "",
                        "routes": {"a": "done"},
                    },
                },
                "done": {"action": "stop", "message": "Done."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        errors = validate_recipe(wf)
        assert any("field" in e.lower() for e in errors)

    # T_OR4
    def test_on_result_empty_routes_rejected(self, tmp_path: Path) -> None:
        """on_result.routes must be non-empty."""
        data = {
            "name": "empty-routes-recipe",
            "description": "Empty on_result routes",
            "kitchen_rules": ["test"],
            "steps": {
                "classify": {
                    "tool": "classify_fix",
                    "on_result": {
                        "field": "restart_scope",
                        "routes": {},
                    },
                },
                "done": {"action": "stop", "message": "Done."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        errors = validate_recipe(wf)
        assert any("routes" in e.lower() for e in errors)

    # T_OR5
    def test_on_result_route_targets_validated(self, tmp_path: Path) -> None:
        """on_result route targets must reference existing steps or 'done'."""
        data = {
            "name": "bad-route-recipe",
            "description": "Bad on_result route target",
            "kitchen_rules": ["test"],
            "steps": {
                "classify": {
                    "tool": "classify_fix",
                    "on_result": {
                        "field": "restart_scope",
                        "routes": {
                            "full_restart": "nonexistent",
                            "partial_restart": "done",
                        },
                    },
                },
                "escalate": {"action": "stop", "message": "Escalating."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        errors = validate_recipe(wf)
        assert any("nonexistent" in e for e in errors)

    # T_OR6
    def test_on_result_route_done_is_valid(self, tmp_path: Path) -> None:
        """on_result route target 'done' is accepted."""
        data = {
            "name": "done-route-recipe",
            "description": "Route to done",
            "kitchen_rules": ["test"],
            "steps": {
                "classify": {
                    "tool": "classify_fix",
                    "on_result": {
                        "field": "restart_scope",
                        "routes": {
                            "full_restart": "done",
                            "partial_restart": "done",
                        },
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

    # T_OR7
    def test_on_result_with_on_failure_valid(self, tmp_path: Path) -> None:
        """on_result + on_failure together is valid (on_failure is the fallback)."""
        data = {
            "name": "valid-combo-recipe",
            "description": "on_result with on_failure",
            "kitchen_rules": ["test"],
            "steps": {
                "classify": {
                    "tool": "classify_fix",
                    "on_result": {
                        "field": "restart_scope",
                        "routes": {
                            "full_restart": "investigate",
                            "partial_restart": "implement",
                        },
                    },
                    "on_failure": "escalate",
                },
                "investigate": {"action": "stop", "message": "Investigating."},
                "implement": {"action": "stop", "message": "Implementing."},
                "escalate": {"action": "stop", "message": "Escalating."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        errors = validate_recipe(wf)
        assert errors == []

    # T_OR9
    def test_on_result_defaults_to_none(self, tmp_path: Path) -> None:
        """Steps without on_result have on_result=None."""
        f = _write_yaml(tmp_path / "recipe.yaml", VALID_RECIPE)
        wf = load_recipe(f)
        assert wf.steps["run_tests"].on_result is None

    # CON1
    def test_recipe_schema_supports_kitchen_rules(self) -> None:
        """Recipe dataclass must have a kitchen_rules field."""
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(Recipe)}
        assert "kitchen_rules" in field_names, (
            "Recipe dataclass must have a 'kitchen_rules' field "
            "for pipeline orchestrator discipline"
        )

    # CON2
    def test_parse_recipe_extracts_kitchen_rules(self, tmp_path: Path) -> None:
        """_parse_recipe must extract kitchen_rules from YAML."""
        data = {
            **VALID_RECIPE,
            "kitchen_rules": [
                "ONLY use AutoSkillit MCP tools",
                "NEVER use Edit, Write, Read",
            ],
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        assert wf.kitchen_rules == [
            "ONLY use AutoSkillit MCP tools",
            "NEVER use Edit, Write, Read",
        ]

    # CON3
    def test_validate_recipe_warns_missing_kitchen_rules(self, tmp_path: Path) -> None:
        """validate_recipe should warn when kitchen_rules are empty."""
        data = {**VALID_RECIPE}
        data.pop("kitchen_rules", None)
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        errors = validate_recipe(wf)
        warnings = [e for e in errors if "kitchen_rules" in e.lower()]
        assert warnings, "validate_recipe must warn when kitchen_rules are empty"

    # CON4
    def test_bundled_recipes_have_kitchen_rules(self) -> None:
        """All bundled recipes must have a non-empty kitchen_rules field."""
        wf_dir = builtin_recipes_dir()
        failures = []
        for path in sorted(wf_dir.glob("*.yaml")):
            wf = load_recipe(path)
            if not wf.kitchen_rules:
                failures.append(f"{path.name}: missing kitchen_rules")
        assert not failures, "Bundled recipes missing kitchen_rules:\n" + "\n".join(
            f"  - {f}" for f in failures
        )

    # OPT1
    def test_recipe_step_has_optional_field(self) -> None:
        """RecipeStep must have an optional field of type bool defaulting to False."""
        import dataclasses

        fields = {f.name: f for f in dataclasses.fields(RecipeStep)}
        assert "optional" in fields, "RecipeStep must have an 'optional' field"
        assert fields["optional"].default is False, "RecipeStep.optional must default to False"

    # OPT2
    def test_parse_step_preserves_optional(self) -> None:
        """_parse_step must preserve optional=True and default to False."""
        step_with = _parse_step({"tool": "test_check", "optional": True})
        assert step_with.optional is True, "_parse_step must preserve optional=True"

        step_without = _parse_step({"tool": "test_check"})
        assert step_without.optional is False, "_parse_step must default optional to False"

    # MOD1
    def test_step_model_field_defaults_to_none(self) -> None:
        step = RecipeStep(tool="run_skill")
        assert step.model is None

    # MOD2
    def test_parse_step_extracts_model(self) -> None:
        step = _parse_step({"tool": "run_skill", "model": "sonnet"})
        assert step.model == "sonnet"

    # MOD3
    def test_parse_step_model_absent(self) -> None:
        step = _parse_step({"tool": "run_skill"})
        assert step.model is None

    # MOD4
    def test_bundled_assess_steps_use_sonnet(self) -> None:
        bd = builtin_recipes_dir()
        for f in bd.glob("*.yaml"):
            wf = load_recipe(f)
            for step_name, step in wf.steps.items():
                if (
                    step.with_args.get("skill_command")
                    and "assess-and-merge" in step.with_args["skill_command"]
                ):
                    assert step.model == "sonnet", (
                        f"{f.name} step '{step_name}' should have model='sonnet'"
                    )


class TestListRecipes:
    """TestListRecipes: discovery from project and builtin sources."""

    # WF7 variant
    def test_finds_builtins(self, tmp_path: Path) -> None:
        recipes = list_recipes(tmp_path).items
        names = {w.name for w in recipes}
        assert "bugfix-loop" in names
        assert "implementation-pipeline" in names


class TestBuiltinRecipesDir:
    """Tests for builtin_recipes_dir() function."""

    def test_returns_existing_directory(self) -> None:
        """builtin_recipes_dir() returns a directory that exists."""
        d = builtin_recipes_dir()
        assert d.is_dir(), f"builtin_recipes_dir() {d} is not a directory"

    def test_points_to_recipes(self) -> None:
        """builtin_recipes_dir() points to 'recipes' subdirectory."""
        d = builtin_recipes_dir()
        assert d.name == "recipes", (
            f"builtin_recipes_dir() should point to 'recipes', got '{d.name}'"
        )

    def test_contains_yaml_files(self) -> None:
        """builtin_recipes_dir() contains at least one YAML file."""
        d = builtin_recipes_dir()
        yaml_files = list(d.glob("*.yaml"))
        assert len(yaml_files) > 0, "builtin_recipes_dir() contains no YAML files"


class TestRecipeValidation:
    """Tests for validate_recipe function."""

    def test_valid_recipe_no_errors(self, tmp_path: Path) -> None:
        """validate_recipe returns empty list for a valid recipe."""
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", VALID_RECIPE))
        errors = validate_recipe(wf)
        assert errors == []

    def test_missing_name_produces_error(self) -> None:
        """validate_recipe returns error when name is empty."""
        data = {**VALID_RECIPE, "name": ""}
        wf = _parse_recipe(data)
        errors = validate_recipe(wf)
        assert any("name" in e.lower() for e in errors)

    def test_validate_recipe_function_exists(self) -> None:
        """validate_recipe function is importable from recipe_parser."""
        from autoskillit.recipe_parser import validate_recipe as vr

        assert callable(vr)


class TestDataFlowQuality:
    """Tests for data-flow quality analysis (DFQ prefix)."""

    def _make_recipe(self, steps: dict[str, dict]) -> Recipe:
        """Build a minimal Recipe from step dicts."""
        parsed_steps = {name: _parse_step(data) for name, data in steps.items()}
        return Recipe(
            name="test",
            description="test",
            steps=parsed_steps,
            kitchen_rules=["test"],
        )

    # DFQ1
    def test_analyze_dataflow_returns_report(self) -> None:
        """analyze_dataflow returns a DataFlowReport with warnings list and summary str."""
        wf = self._make_recipe(
            {
                "run": {"tool": "test_check", "on_success": "done"},
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        assert isinstance(report, DataFlowReport)
        assert isinstance(report.warnings, list)
        assert isinstance(report.summary, str)

    # DFQ2
    def test_dead_output_detected(self) -> None:
        """Captured var with no downstream context.X consumer triggers DEAD_OUTPUT."""
        wf = self._make_recipe(
            {
                "impl": {
                    "tool": "run_skill",
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_success": "finish",
                },
                "finish": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT"]
        assert len(dead) == 1
        assert dead[0].step_name == "impl"
        assert dead[0].field == "worktree_path"

    # DFQ3
    def test_consumed_output_not_flagged(self) -> None:
        """Captured var consumed by downstream step should not trigger DEAD_OUTPUT."""
        wf = self._make_recipe(
            {
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
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT"]
        assert len(dead) == 0

    # DFQ4
    def test_dead_output_on_any_path_not_flagged(self) -> None:
        """Var consumed on one path but not another should NOT trigger DEAD_OUTPUT."""
        wf = self._make_recipe(
            {
                "impl": {
                    "tool": "run_skill",
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_success": "merge",
                    "on_failure": "escalate",
                },
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "${{ context.worktree_path }}"},
                    "on_success": "done",
                },
                "escalate": {"action": "stop", "message": "Failed"},
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT"]
        assert len(dead) == 0

    # DFQ5
    def test_implicit_handoff_detected(self) -> None:
        """run_skill step without capture triggers IMPLICIT_HANDOFF."""
        wf = self._make_recipe(
            {
                "impl": {
                    "tool": "run_skill",
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        implicit = [w for w in report.warnings if w.code == "IMPLICIT_HANDOFF"]
        assert len(implicit) == 1
        assert implicit[0].step_name == "impl"

    # DFQ6
    def test_non_skill_step_no_implicit_handoff(self) -> None:
        """test_check step without capture should NOT trigger IMPLICIT_HANDOFF."""
        wf = self._make_recipe(
            {
                "test": {
                    "tool": "test_check",
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        implicit = [w for w in report.warnings if w.code == "IMPLICIT_HANDOFF"]
        assert len(implicit) == 0

    # DFQ7
    def test_skill_step_with_capture_no_implicit_handoff(self) -> None:
        """run_skill step with capture should NOT trigger IMPLICIT_HANDOFF."""
        wf = self._make_recipe(
            {
                "impl": {
                    "tool": "run_skill",
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        implicit = [w for w in report.warnings if w.code == "IMPLICIT_HANDOFF"]
        assert len(implicit) == 0

    # DFQ8
    def test_terminal_step_no_implicit_handoff(self) -> None:
        """action: stop steps should NOT trigger IMPLICIT_HANDOFF."""
        wf = self._make_recipe(
            {
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        implicit = [w for w in report.warnings if w.code == "IMPLICIT_HANDOFF"]
        assert len(implicit) == 0

    # DFQ9
    def test_graph_construction_follows_all_routing_edges(self) -> None:
        """_build_step_graph follows on_success, on_failure, on_result, retry edges."""
        wf = self._make_recipe(
            {
                "start": {
                    "tool": "run_skill",
                    "on_success": "check",
                    "on_failure": "fix",
                    "retry": {"max_attempts": 3, "on": "needs_retry", "on_exhausted": "escalate"},
                },
                "check": {
                    "tool": "test_check",
                    "on_result": {
                        "field": "passed",
                        "routes": {"true": "done", "false": "fix"},
                    },
                },
                "fix": {"tool": "run_skill", "on_success": "start"},
                "escalate": {"action": "stop", "message": "Exhausted"},
                "done": {"action": "stop", "message": "Done"},
            }
        )
        graph = _build_step_graph(wf)
        assert graph["start"] == {"check", "fix", "escalate"}
        assert graph["check"] == {"done", "fix"}
        assert graph["fix"] == {"start"}
        assert graph["escalate"] == set()
        assert graph["done"] == set()

    # DFQ10
    def test_dead_output_via_on_result_route(self) -> None:
        """Dead output detection works with on_result routing."""
        wf = self._make_recipe(
            {
                "impl": {
                    "tool": "run_skill",
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_result": {
                        "field": "success",
                        "routes": {"true": "merge", "false": "escalate"},
                    },
                },
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "${{ context.worktree_path }}"},
                    "on_success": "done",
                },
                "escalate": {"action": "stop", "message": "Failed"},
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT"]
        assert len(dead) == 0

        wf2 = self._make_recipe(
            {
                "impl": {
                    "tool": "run_skill",
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_result": {
                        "field": "success",
                        "routes": {"true": "done", "false": "escalate"},
                    },
                },
                "done": {"action": "stop", "message": "Done"},
                "escalate": {"action": "stop", "message": "Failed"},
            }
        )
        report2 = analyze_dataflow(wf2)
        dead2 = [w for w in report2.warnings if w.code == "DEAD_OUTPUT"]
        assert len(dead2) == 1
        assert dead2[0].field == "worktree_path"

    # DFQ11
    def test_summary_reports_counts(self) -> None:
        """Summary includes warning count when warnings exist."""
        wf = self._make_recipe(
            {
                "impl": {
                    "tool": "run_skill",
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_success": "run",
                },
                "run": {
                    "tool": "run_skill",
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        assert "2 data-flow warnings" in report.summary

    # DFQ12
    def test_clean_recipe_summary(self) -> None:
        """Clean recipe summary says no warnings."""
        wf = self._make_recipe(
            {
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
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        assert "No data-flow warnings" in report.summary

    # DFQ13
    def test_bundled_recipes_produce_reports(self) -> None:
        """analyze_dataflow runs cleanly on all bundled recipe YAMLs."""
        wf_dir = builtin_recipes_dir()
        assert wf_dir.is_dir(), f"Bundled recipes dir not found: {wf_dir}"
        yaml_files = list(wf_dir.glob("*.yaml")) + list(wf_dir.glob("*.yml"))
        assert len(yaml_files) > 0, "No bundled recipe files found"
        for yaml_file in yaml_files:
            wf = load_recipe(yaml_file)
            report = analyze_dataflow(wf)
            assert isinstance(report, DataFlowReport)
            assert isinstance(report.warnings, list)

    # DFQ15
    def test_multiple_dead_outputs_all_reported(self) -> None:
        """Multiple dead captures each get their own DEAD_OUTPUT warning."""
        wf = self._make_recipe(
            {
                "impl": {
                    "tool": "run_skill",
                    "capture": {
                        "a": "${{ result.a }}",
                        "b": "${{ result.b }}",
                        "c": "${{ result.c }}",
                    },
                    "on_success": "test",
                },
                "test": {
                    "tool": "test_check",
                    "with": {"val": "${{ context.a }}"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT"]
        assert len(dead) == 2
        dead_fields = {w.field for w in dead}
        assert dead_fields == {"b", "c"}


# ---------------------------------------------------------------------------
# TestVersionField: autoskillit_version field on Recipe dataclass
# ---------------------------------------------------------------------------

_VALID_RECIPE_DATA: dict = {
    "name": "version-test-recipe",
    "description": "A recipe for testing the version field",
    "kitchen_rules": ["Only use AutoSkillit MCP tools during pipeline execution"],
    "steps": {
        "do_it": {
            "tool": "run_cmd",
            "on_success": "done",
        },
        "done": {"action": "stop", "message": "Done."},
    },
}


class TestVersionField:
    """autoskillit_version field on Recipe dataclass."""

    # VER1: Recipe without autoskillit_version has version=None
    def test_version_none_when_absent(self) -> None:
        """_parse_recipe sets version=None when autoskillit_version is absent."""
        data = dict(_VALID_RECIPE_DATA)
        wf = _parse_recipe(data)
        assert wf.version is None

    # VER2: Recipe with autoskillit_version="0.2.0" parses correctly
    def test_version_set_when_present(self) -> None:
        """_parse_recipe reads autoskillit_version and stores it as version."""
        data = dict(_VALID_RECIPE_DATA)
        data["autoskillit_version"] = "0.2.0"
        wf = _parse_recipe(data)
        assert wf.version == "0.2.0"

    # VER3: autoskillit_version does not cause validation errors
    def test_version_does_not_cause_validation_errors(self) -> None:
        """A recipe with autoskillit_version passes validate_recipe with no errors."""
        data = dict(_VALID_RECIPE_DATA)
        data["autoskillit_version"] = "0.2.0"
        wf = _parse_recipe(data)
        errors = validate_recipe(wf)
        assert errors == []

    # VER4: autoskillit_version is preserved in round-trip (parse -> access)
    def test_version_preserved_in_round_trip(self, tmp_path: Path) -> None:
        """version attribute survives a full write-to-disk and load_recipe round-trip."""
        data = dict(_VALID_RECIPE_DATA)
        data["autoskillit_version"] = "1.3.0"
        path = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(path)
        assert wf.version == "1.3.0"


class TestWeakConstraintRule:
    """Tests for the weak-constraint-text semantic rule."""

    def _make_recipe_with_kitchen_rules(self, kitchen_rules: list[str]) -> Recipe:
        steps = {
            "run": _parse_step({"tool": "test_check", "on_success": "done"}),
            "done": _parse_step({"action": "stop", "message": "Done"}),
        }
        return Recipe(
            name="test",
            description="test",
            steps=steps,
            kitchen_rules=kitchen_rules,
        )

    def test_weak_constraint_text_detected(self) -> None:
        """Generic one-liner kitchen_rules should trigger weak-constraint-text warning."""
        from autoskillit.semantic_rules import run_semantic_rules

        wf = self._make_recipe_with_kitchen_rules(["Only use AutoSkillit MCP tools."])
        findings = run_semantic_rules(wf)
        weak = [f for f in findings if f.rule == "weak-constraint-text"]
        assert weak, "Generic constraint should trigger weak-constraint-text warning"

    def test_detailed_constraints_pass(self) -> None:
        """kitchen_rules naming core forbidden tools should not trigger the warning."""
        from autoskillit.semantic_rules import run_semantic_rules
        from autoskillit.types import PIPELINE_FORBIDDEN_TOOLS

        tool_list = ", ".join(PIPELINE_FORBIDDEN_TOOLS)
        constraint = f"NEVER use native tools ({tool_list}) from the orchestrator."
        wf = self._make_recipe_with_kitchen_rules([constraint])
        findings = run_semantic_rules(wf)
        weak = [f for f in findings if f.rule == "weak-constraint-text"]
        assert not weak, "Detailed constraint should not trigger weak-constraint-text"


# ---------------------------------------------------------------------------
# New tests from the plan
# ---------------------------------------------------------------------------


def test_recipe_replaces_workflow_class() -> None:
    """Recipe class is the new name for Workflow."""
    wf = Recipe(name="test", description="test")
    assert isinstance(wf, Recipe)


def test_recipe_step_replaces_workflow_step() -> None:
    """RecipeStep is the new name for WorkflowStep."""
    step = RecipeStep(tool="run_skill")
    assert isinstance(step, RecipeStep)


def test_recipe_ingredient_replaces_workflow_input() -> None:
    """RecipeIngredient is the new name for WorkflowInput."""
    from autoskillit.recipe_parser import RecipeIngredient

    ing = RecipeIngredient(description="test")
    assert isinstance(ing, RecipeIngredient)


def test_bundled_recipes_use_ingredients_field() -> None:
    """All bundled recipes use 'ingredients' field (not 'inputs')."""
    bd = builtin_recipes_dir()
    for path in bd.glob("*.yaml"):
        data = yaml.safe_load(path.read_text())
        assert isinstance(data, dict)
        if "inputs" in data and "ingredients" not in data:
            assert False, f"{path.name} still uses 'inputs' field instead of 'ingredients'"


def test_bundled_recipes_use_kitchen_rules_field() -> None:
    """All bundled recipes use 'kitchen_rules' field (not 'constraints')."""
    bd = builtin_recipes_dir()
    for path in bd.glob("*.yaml"):
        data = yaml.safe_load(path.read_text())
        assert isinstance(data, dict)
        if "constraints" in data and "kitchen_rules" not in data:
            assert False, f"{path.name} still uses 'constraints' field instead of 'kitchen_rules'"


def test_validate_recipe_function_exists() -> None:
    """validate_recipe function is importable from recipe_parser."""
    from autoskillit.recipe_parser import validate_recipe as vr

    assert callable(vr)


def test_builtin_recipes_dir_points_to_recipes() -> None:
    """builtin_recipes_dir() returns a path ending in 'recipes'."""
    d = builtin_recipes_dir()
    assert d.name == "recipes"


def test_recipe_source_enum_values() -> None:
    """RecipeSource enum has PROJECT and BUILTIN values."""
    from autoskillit.types import RecipeSource

    assert hasattr(RecipeSource, "PROJECT")
    assert hasattr(RecipeSource, "BUILTIN")


# RP-REG1
def test_context_ref_re_is_compiled_pattern():
    from autoskillit.recipe_parser import _CONTEXT_REF_RE

    assert isinstance(_CONTEXT_REF_RE, re.Pattern)
    assert _CONTEXT_REF_RE.findall("${{ context.foo }}") == ["foo"]


# RP-REG2
def test_input_ref_re_is_compiled_pattern():
    from autoskillit.recipe_parser import _INPUT_REF_RE

    assert isinstance(_INPUT_REF_RE, re.Pattern)
    assert _INPUT_REF_RE.findall("${{ inputs.bar }}") == ["bar"]


# RP-REG3
def test_extract_refs_function_removed():
    import autoskillit.recipe_parser as rp

    assert not hasattr(rp, "_extract_refs")


# RP-GEN1
def test_iter_steps_with_context_is_importable():
    from autoskillit.recipe_parser import iter_steps_with_context

    assert callable(iter_steps_with_context)


# RP-GEN2
def test_iter_steps_with_context_first_step_empty_context():
    from autoskillit.recipe_parser import iter_steps_with_context

    recipe = _parse_recipe(
        {
            "name": "test",
            "description": "d",
            "steps": {
                "step_a": {
                    "tool": "run_cmd",
                    "with": {"cmd": "echo hi"},
                    "on_success": "done",
                    "on_failure": "done",
                },
            },
        }
    )
    tuples = list(iter_steps_with_context(recipe))
    assert len(tuples) == 1
    name, step, ctx = tuples[0]
    assert name == "step_a"
    assert ctx == frozenset()


# RP-GEN3
def test_iter_steps_with_context_accumulates_captures():
    from autoskillit.recipe_parser import iter_steps_with_context

    recipe = _parse_recipe(
        {
            "name": "test",
            "description": "d",
            "steps": {
                "step_a": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/autoskillit:investigate q"},
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_success": "step_b",
                    "on_failure": "done",
                },
                "step_b": {
                    "tool": "run_cmd",
                    "with": {"cmd": "echo hi"},
                    "on_success": "done",
                    "on_failure": "done",
                },
            },
        }
    )
    tuples = list(iter_steps_with_context(recipe))
    _, _, ctx_a = tuples[0]
    _, _, ctx_b = tuples[1]
    assert "worktree_path" not in ctx_a
    assert "worktree_path" in ctx_b


# RP-GEN4
def test_iter_steps_with_context_yields_frozenset():
    from autoskillit.recipe_parser import iter_steps_with_context

    recipe = _parse_recipe(
        {
            "name": "test",
            "description": "d",
            "steps": {
                "only": {
                    "tool": "run_cmd",
                    "with": {"cmd": "x"},
                    "on_success": "done",
                    "on_failure": "done",
                },
            },
        }
    )
    _, _, ctx = next(iter_steps_with_context(recipe))
    assert isinstance(ctx, frozenset)


# RP-FIND1
def test_find_recipe_by_name_returns_match(tmp_path):
    from autoskillit.recipe_parser import find_recipe_by_name

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "my-flow.yaml").write_text("name: my-flow\ndescription: test\nsteps: {}\n")
    result = find_recipe_by_name("my-flow", tmp_path)
    assert result is not None
    assert result.name == "my-flow"


# RP-FIND2
def test_find_recipe_by_name_returns_none_for_unknown(tmp_path):
    from autoskillit.recipe_parser import find_recipe_by_name

    result = find_recipe_by_name("does-not-exist", tmp_path)
    assert result is None


# RP-FIND3
def test_find_recipe_by_name_finds_builtins():
    from autoskillit.recipe_parser import find_recipe_by_name

    result = find_recipe_by_name("bugfix-loop", Path.cwd())
    assert result is not None


# RP-VK1
def test_autoskillit_version_key_constant_exists():
    from autoskillit.recipe_parser import AUTOSKILLIT_VERSION_KEY

    assert AUTOSKILLIT_VERSION_KEY == "autoskillit_version"


# RP-VK2
def test_version_field_uses_key_constant(tmp_path):
    from autoskillit.recipe_parser import AUTOSKILLIT_VERSION_KEY, load_recipe

    p = tmp_path / "r.yaml"
    p.write_text(f"name: r\ndescription: d\n{AUTOSKILLIT_VERSION_KEY}: 0.9.0\nsteps: {{}}\n")
    recipe = load_recipe(p)
    assert recipe.version == "0.9.0"


# ---------------------------------------------------------------------------
# RP-IP1-IP3: implementation-pipeline.yaml structural capture tests
# ---------------------------------------------------------------------------


def test_implementation_pipeline_group_step_captures_group_files():
    """group step capture must use group_files, not groups_path."""
    wf = load_recipe(builtin_recipes_dir() / "implementation-pipeline.yaml")
    group_step = wf.steps["group"]
    assert "group_files" in group_step.capture, (
        f"group step capture keys: {list(group_step.capture.keys())}"
    )
    assert "groups_path" not in group_step.capture, (
        "group step must not capture the dead output groups_path"
    )


def test_implementation_pipeline_review_step_captures_review_path():
    """review step must have a capture block with review_path."""
    wf = load_recipe(builtin_recipes_dir() / "implementation-pipeline.yaml")
    review_step = wf.steps["review"]
    assert "review_path" in review_step.capture, (
        f"review step capture keys: {list(review_step.capture.keys())}"
    )


def test_implementation_pipeline_plan_step_consumes_group_files():
    """plan step with_args must reference context.group_files."""
    wf = load_recipe(builtin_recipes_dir() / "implementation-pipeline.yaml")
    plan_step = wf.steps["plan"]
    all_values = " ".join(str(v) for v in plan_step.with_args.values())
    assert "context.group_files" in all_values, (
        f"plan step with_args values: {list(plan_step.with_args.values())}"
    )


# ---------------------------------------------------------------------------
# RP-IP4: implementation-pipeline.yaml audit_impl capture
# ---------------------------------------------------------------------------


def test_implementation_pipeline_audit_impl_captures_remediation_path_and_verdict():
    """audit_impl step must have a capture block with remediation_path and verdict."""
    wf = load_recipe(builtin_recipes_dir() / "implementation-pipeline.yaml")
    audit_impl_step = wf.steps["audit_impl"]
    assert "remediation_path" in audit_impl_step.capture, (
        f"audit_impl step capture keys: {list(audit_impl_step.capture.keys())}"
    )
    assert "verdict" in audit_impl_step.capture, (
        f"audit_impl step capture keys: {list(audit_impl_step.capture.keys())}"
    )


# ---------------------------------------------------------------------------
# RP-ST1-ST4: smoke-test.yaml structural tests
# ---------------------------------------------------------------------------


def test_smoke_test_create_branch_is_not_action_route():
    """create_branch step must not be an action: route step."""
    wf = load_recipe(builtin_recipes_dir() / "smoke-test.yaml")
    create_branch = wf.steps["create_branch"]
    assert create_branch.action != "route", "create_branch must be a tool step, not action: route"


def test_smoke_test_create_branch_captures_feature_branch():
    """create_branch step must capture feature_branch."""
    wf = load_recipe(builtin_recipes_dir() / "smoke-test.yaml")
    create_branch = wf.steps["create_branch"]
    assert "feature_branch" in create_branch.capture, (
        f"create_branch capture keys: {list(create_branch.capture.keys())}"
    )


def test_smoke_test_merge_steps_use_context_feature_branch():
    """All merge_worktree steps must use context.feature_branch as base_branch."""
    wf = load_recipe(builtin_recipes_dir() / "smoke-test.yaml")
    for name, step in wf.steps.items():
        if step.tool == "merge_worktree":
            with_values = " ".join(str(v) for v in step.with_args.values())
            assert "context.feature_branch" in with_values, (
                f"merge step '{name}' must use context.feature_branch, got: {with_values}"
            )


def test_smoke_test_check_summary_is_not_action_route():
    """check_summary step must not be an action: route step."""
    wf = load_recipe(builtin_recipes_dir() / "smoke-test.yaml")
    check_summary = wf.steps["check_summary"]
    assert check_summary.action != "route", "check_summary must be a tool step, not action: route"
