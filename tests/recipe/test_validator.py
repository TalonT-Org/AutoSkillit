"""Tests for recipe structural validation and dataflow analysis."""

from __future__ import annotations

import dataclasses
import importlib
from pathlib import Path

import pytest
import yaml

from autoskillit.core.types import RETRY_RESPONSE_FIELDS
from autoskillit.recipe.io import (
    _parse_recipe,
    _parse_step,
    builtin_recipes_dir,
    iter_steps_with_context,
    list_recipes,
    load_recipe,
)
from autoskillit.recipe.schema import (
    Recipe,
    RecipeStep,
)
from autoskillit.recipe.validator import (
    analyze_dataflow,
    validate_recipe,
)

# ---------------------------------------------------------------------------
# Importability assertions
# ---------------------------------------------------------------------------


def test_semantic_rules_module_no_longer_exists() -> None:
    """semantic_rules module must be gone — ModuleNotFoundError expected."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("autoskillit.semantic_rules")


def test_contract_validator_module_no_longer_exists() -> None:
    """contract_validator module must be gone — ModuleNotFoundError expected."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("autoskillit.contract_validator")


# ---------------------------------------------------------------------------
# VALID_RECIPE fixture data
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# TestValidateRecipe — migrated from test_recipe_parser.py
# ---------------------------------------------------------------------------


class TestValidateRecipe:
    def test_valid_recipe_no_errors(self, tmp_path: Path) -> None:
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", VALID_RECIPE))
        errors = validate_recipe(wf)
        assert errors == []

    def test_missing_name_produces_error(self) -> None:
        data = {**VALID_RECIPE, "name": ""}
        wf = _parse_recipe(data)
        errors = validate_recipe(wf)
        assert any("name" in e.lower() for e in errors)

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

    def test_retry_on_unknown_field_fails_validation(self, tmp_path: Path) -> None:
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
                    "on_result": [
                        {"when": "${{ result.restart_scope }} == full_restart", "route": "done"},
                        {"route": "done"},
                    ],
                    "on_success": "done",
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
                    "on_result": [
                        {
                            "when": "${{ result.restart_scope }} == full_restart",
                            "route": "done",
                        },
                        {"route": "done"},
                    ],
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

    def test_retry_on_field_is_valid_response_key(self, tmp_path: Path) -> None:
        for wf_info in list_recipes(tmp_path).items:
            wf = load_recipe(wf_info.path)
            for step_name, step in wf.steps.items():
                if step.retry and step.retry.on:
                    assert step.retry.on in RETRY_RESPONSE_FIELDS, (
                        f"Recipe '{wf.name}' step '{step_name}' retry.on='{step.retry.on}' "
                        f"is not a known response field: {RETRY_RESPONSE_FIELDS}"
                    )


# ---------------------------------------------------------------------------
# TestDataFlowQuality — migrated from test_recipe_parser.py
# ---------------------------------------------------------------------------


class TestDataFlowQuality:
    """Tests for data-flow quality analysis (DFQ prefix)."""

    def _make_recipe(self, steps: dict[str, dict]) -> Recipe:
        parsed_steps = {name: _parse_step(data) for name, data in steps.items()}
        return Recipe(
            name="test",
            description="test",
            steps=parsed_steps,
            kitchen_rules=["test"],
        )

    # DFQ1
    def test_analyze_dataflow_returns_report(self) -> None:
        from autoskillit.recipe.schema import DataFlowReport

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

    # DFQ5
    def test_implicit_handoff_detected(self) -> None:
        wf = self._make_recipe(
            {
                "impl": {"tool": "run_skill", "on_success": "done"},
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        implicit = [w for w in report.warnings if w.code == "IMPLICIT_HANDOFF"]
        assert len(implicit) == 1
        assert implicit[0].step_name == "impl"

    # DFQ6
    def test_non_skill_step_no_implicit_handoff(self) -> None:
        wf = self._make_recipe(
            {
                "test": {"tool": "test_check", "on_success": "done"},
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        implicit = [w for w in report.warnings if w.code == "IMPLICIT_HANDOFF"]
        assert len(implicit) == 0

    # DFQ11
    def test_summary_reports_counts(self) -> None:
        wf = self._make_recipe(
            {
                "impl": {
                    "tool": "run_skill",
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_success": "run",
                },
                "run": {"tool": "run_skill", "on_success": "done"},
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        assert "2 data-flow warnings" in report.summary

    # DFQ13
    def test_bundled_recipes_produce_reports(self) -> None:
        from autoskillit.recipe.schema import DataFlowReport

        wf_dir = builtin_recipes_dir()
        yaml_files = list(wf_dir.glob("*.yaml")) + list(wf_dir.glob("*.yml"))
        assert len(yaml_files) > 0
        for yaml_file in yaml_files:
            wf = load_recipe(yaml_file)
            report = analyze_dataflow(wf)
            assert isinstance(report, DataFlowReport)
            assert isinstance(report.warnings, list)


# ---------------------------------------------------------------------------
# iter_steps_with_context integration
# ---------------------------------------------------------------------------


def test_validate_recipe_uses_iter_steps_with_context_for_capture_refs(tmp_path: Path) -> None:
    """validate_recipe catches context refs not captured by preceding steps."""
    data = {
        "name": "ctx-test",
        "description": "Context validation test",
        "kitchen_rules": ["test"],
        "steps": {
            "step1": {
                "tool": "run_cmd",
                "with": {"cmd": "echo hello"},
                "on_success": "step2",
            },
            "step2": {
                "tool": "test_check",
                "with": {"worktree_path": "${{ context.worktree_path }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "ok"},
        },
    }
    wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
    # step1 has no captures, so step2 should see empty context
    steps = list(iter_steps_with_context(wf))
    assert steps[1][2] == frozenset()
    # validate_recipe should catch the unsatisfied context reference
    errors = validate_recipe(wf)
    assert any("worktree_path" in e for e in errors)


# ---------------------------------------------------------------------------
# isinstance guard tests (T_GD1, T_GV1)
# ---------------------------------------------------------------------------


class TestIsInstanceGuards:
    def test_gd1_analyze_dataflow_no_raise_on_bool_with_arg(self) -> None:
        """T_GD1: _detect_dead_outputs does not raise TypeError for boolean with_args."""
        data = {
            "name": "bool-guard",
            "description": "test",
            "kitchen_rules": ["test"],
            "steps": {
                "plan": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/autoskillit:make-plan do the task"},
                    "capture": {"plan_path": "${{ result.plan_path }}"},
                    "on_success": "downstream",
                },
                "downstream": {
                    "tool": "run_cmd",
                    "with": {"worktree_path": True},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            },
        }
        recipe = _parse_recipe(data)
        report = analyze_dataflow(recipe)
        assert report is not None

    def test_gv1_validate_recipe_no_raise_on_bool_with_arg(self) -> None:
        """T_GV1: validate_recipe does not raise TypeError for boolean with_args."""
        data = {
            "name": "bool-guard-validate",
            "description": "test",
            "kitchen_rules": ["test"],
            "steps": {
                "step1": {
                    "tool": "run_cmd",
                    "with": {"flag": True},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            },
        }
        recipe = _parse_recipe(data)
        result = validate_recipe(recipe)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# on_result self-consumption tests (T_OR1, T_OR2)
# ---------------------------------------------------------------------------


def _make_workflow(steps: dict[str, dict]) -> Recipe:
    parsed_steps = {name: _parse_step(data) for name, data in steps.items()}
    return Recipe(name="test", description="test", steps=parsed_steps, kitchen_rules=["test"])


class TestOnResultConsumption:
    def test_or1_on_result_field_is_not_dead_output(self) -> None:
        """T_OR1: verdict captured and referenced in downstream step is NOT flagged DEAD_OUTPUT."""
        steps = {
            "audit_impl": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:audit-impl plan.md myref main",
                },
                "capture": {"verdict": "${{ result.verdict }}"},
                "on_result": [
                    {
                        "when": "${{ result.verdict }} == GO",
                        "route": "report",
                    },
                    {"route": "done"},
                ],
            },
            "report": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:pipeline-summary",
                    "cwd": "/tmp",
                    "verdict": "${{ context.verdict }}",
                },
                "on_success": "done",
                "on_failure": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        report = analyze_dataflow(recipe)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT" and w.field == "verdict"]
        assert dead == []

    def test_or2_different_on_result_field_flags_dead_output(self) -> None:
        """T_OR2: verdict is flagged DEAD_OUTPUT when no downstream step uses context.verdict."""
        steps = {
            "audit_impl": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:audit-impl plan.md myref main",
                },
                "capture": {"verdict": "${{ result.verdict }}"},
                "on_result": [
                    {
                        "when": "${{ result.restart_scope }} == full_restart",
                        "route": "done",
                    },
                    {"route": "done"},
                ],
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        report = analyze_dataflow(recipe)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT" and w.field == "verdict"]
        assert len(dead) == 1


# ---------------------------------------------------------------------------
# skip_when_false bypass edge tests
# ---------------------------------------------------------------------------


def test_build_step_graph_adds_bypass_edges_for_skip_when_false() -> None:
    """When a step has skip_when_false, predecessors get a direct edge to its on_success."""
    from autoskillit.recipe.schema import RecipeIngredient
    from autoskillit.recipe.validator import _build_step_graph

    # Recipe: entry → optional_step (skip_when_false) → final_step
    # Expected bypass edge: entry → final_step
    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="optional_step"),
            "optional_step": RecipeStep(
                tool="run_skill",
                on_success="final_step",
                skip_when_false="inputs.flag",
            ),
            "final_step": RecipeStep(action="stop", message="done"),
        },
        ingredients={"flag": RecipeIngredient(description="", required=False, default="true")},
        kitchen_rules=["test"],
    )
    graph = _build_step_graph(recipe)
    # Bypass edge: entry can reach final_step directly (skip of optional_step)
    assert "final_step" in graph["entry"]


def test_build_step_graph_bypass_does_not_remove_normal_edge() -> None:
    """The bypass edge is additional, not a replacement for the normal edge."""
    from autoskillit.recipe.schema import RecipeIngredient
    from autoskillit.recipe.validator import _build_step_graph

    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="optional_step"),
            "optional_step": RecipeStep(
                tool="run_skill",
                on_success="final_step",
                skip_when_false="inputs.flag",
            ),
            "final_step": RecipeStep(action="stop", message="done"),
        },
        ingredients={"flag": RecipeIngredient(description="", required=False, default="true")},
        kitchen_rules=["test"],
    )
    graph = _build_step_graph(recipe)
    # Normal edge still present
    assert "optional_step" in graph["entry"]
    # Bypass edge also present
    assert "final_step" in graph["entry"]


# ---------------------------------------------------------------------------
# _build_step_graph predicate on_result tests
# ---------------------------------------------------------------------------


class TestPredicateBuildStepGraph:
    """_build_step_graph includes condition.route edges."""

    def test_build_step_graph_includes_condition_routes(self) -> None:
        """_build_step_graph produces edges for condition.route targets."""
        from autoskillit.recipe.validator import _build_step_graph

        wf = _make_workflow(
            {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_result": [
                        {"when": "result.failed_step == 'test_gate'", "route": "assess"},
                        {"when": "result.error", "route": "cleanup"},
                        {"route": "push"},
                    ],
                    "capture": {"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                },
                "assess": {"action": "stop", "message": "Assess."},
                "cleanup": {"action": "stop", "message": "Cleanup."},
                "push": {"action": "stop", "message": "Push."},
            }
        )
        graph = _build_step_graph(wf)
        assert "assess" in graph["merge"]
        assert "cleanup" in graph["merge"]
        assert "push" in graph["merge"]
