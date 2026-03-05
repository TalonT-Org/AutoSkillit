"""Tests for recipe structural validation and dataflow analysis."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import yaml

from autoskillit.recipe.io import (
    _parse_recipe,
    _parse_step,
    builtin_recipes_dir,
    iter_steps_with_context,
    load_recipe,
)
from autoskillit.recipe.schema import (
    Recipe,
    RecipeStep,
)
from autoskillit.recipe.validator import (
    analyze_dataflow,
    run_semantic_rules,
    validate_recipe,
)

# ---------------------------------------------------------------------------
# Importability assertions
# ---------------------------------------------------------------------------


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
        assert report.warnings == []
        assert report.summary is not None
        assert len(report.summary) > 0

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
        """T_OR1: verdict captured and used as on_result.field is NOT flagged DEAD_OUTPUT."""
        steps = {
            "audit_impl": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:audit-impl plan.md myref main",
                },
                "capture": {"verdict": "${{ result.verdict }}"},
                "on_result": {
                    "field": "verdict",
                    "routes": {"GO": "done", "NO GO": "done"},
                },
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        report = analyze_dataflow(recipe)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT" and w.field == "verdict"]
        assert dead == []

    def test_or2_different_on_result_field_flags_dead_output(self) -> None:
        """T_OR2: verdict is flagged DEAD_OUTPUT when on_result.field is a different key."""
        steps = {
            "audit_impl": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:audit-impl plan.md myref main",
                },
                "capture": {"verdict": "${{ result.verdict }}"},
                "on_result": {
                    "field": "restart_scope",
                    "routes": {"full_restart": "done", "partial_restart": "done"},
                },
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


# ---------------------------------------------------------------------------
# ValidationContext tests
# ---------------------------------------------------------------------------


def test_run_semantic_rules_builds_step_graph_exactly_once(monkeypatch):
    """_build_step_graph is called only once regardless of how many rules need it."""
    from autoskillit.recipe import _analysis

    call_count = []
    real_fn = _analysis._build_step_graph

    def counting_fn(recipe):
        call_count.append(1)
        return real_fn(recipe)

    monkeypatch.setattr(_analysis, "_build_step_graph", counting_fn)

    recipe = _parse_recipe(
        {
            "name": "test",
            "description": "test",
            "autoskillit_version": "0.2.0",
            "kitchen_rules": ["Never use native tools"],
            "steps": {"stop": {"action": "stop", "message": "done"}},
        }
    )
    run_semantic_rules(recipe)

    assert len(call_count) == 1


def test_run_semantic_rules_calls_analyze_dataflow_exactly_once(monkeypatch):
    """analyze_dataflow is called only once regardless of how many rules consume it."""
    from autoskillit.recipe import _analysis

    call_count = []
    real_fn = _analysis.analyze_dataflow

    def counting_fn(recipe, **kwargs):
        call_count.append(1)
        return real_fn(recipe, **kwargs)

    monkeypatch.setattr(_analysis, "analyze_dataflow", counting_fn)

    recipe = _parse_recipe(
        {
            "name": "test",
            "description": "test",
            "autoskillit_version": "0.2.0",
            "kitchen_rules": ["Never use native tools"],
            "steps": {"stop": {"action": "stop", "message": "done"}},
        }
    )
    run_semantic_rules(recipe)

    assert len(call_count) == 1


def test_validation_context_exposes_recipe_step_graph_and_dataflow():
    """ValidationContext contains recipe, step_graph, and dataflow attributes."""
    from autoskillit.recipe._analysis import ValidationContext, make_validation_context

    recipe = _parse_recipe(
        {
            "name": "test",
            "description": "test",
            "autoskillit_version": "0.2.0",
            "kitchen_rules": ["Never use native tools"],
            "steps": {"stop": {"action": "stop", "message": "done"}},
        }
    )
    ctx = make_validation_context(recipe)

    assert isinstance(ctx, ValidationContext)
    assert ctx.recipe is recipe
    assert isinstance(ctx.step_graph, dict)
    assert hasattr(ctx.dataflow, "warnings")


def test_analyze_dataflow_accepts_prebuilt_step_graph():
    """analyze_dataflow can reuse a pre-built step_graph to avoid duplicate computation."""
    from autoskillit.recipe._analysis import _build_step_graph, analyze_dataflow

    recipe = _parse_recipe(
        {
            "name": "test",
            "description": "test",
            "autoskillit_version": "0.2.0",
            "kitchen_rules": ["Never use native tools"],
            "steps": {
                "run": {
                    "tool": "run_skill",
                    "capture": {"wp": "${{ result.worktree_path }}"},
                    "on_success": "use_it",
                },
                "use_it": {
                    "tool": "test_check",
                    "with": {"worktree_path": "${{ context.wp }}"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "done"},
            },
        }
    )
    graph = _build_step_graph(recipe)
    report1 = analyze_dataflow(recipe, step_graph=graph)
    report2 = analyze_dataflow(recipe)
    assert report1.warnings == report2.warnings
