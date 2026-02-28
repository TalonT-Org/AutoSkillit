"""Tests for recipe_validator — structural validation, semantic rules, and contracts."""

from __future__ import annotations

import importlib
import textwrap
from pathlib import Path

import pytest
import yaml

from autoskillit.core.types import RETRY_RESPONSE_FIELDS, Severity
from autoskillit.recipe.contracts import (
    check_contract_staleness,
    compute_skill_hash,
    generate_recipe_card,
    load_bundled_manifest,
    load_recipe_card,
    validate_recipe_cards,
)
from autoskillit.recipe.io import (
    _parse_recipe,
    _parse_step,
    builtin_recipes_dir,
    list_recipes,
    load_recipe,
)
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep, StepResultRoute
from autoskillit.recipe.validator import (
    RuleFinding,
    analyze_dataflow,
    run_semantic_rules,
    validate_recipe,
)

# ---------------------------------------------------------------------------
# Importability assertions
# ---------------------------------------------------------------------------


def test_all_symbols_importable() -> None:
    """All expected symbols are importable from recipe.validator and recipe.contracts."""
    from autoskillit.core.types import Severity  # noqa: F401
    from autoskillit.recipe.contracts import (  # noqa: F401
        DataflowEntry,
        RecipeCard,
        SkillContract,
        SkillInput,
        SkillOutput,
        check_contract_staleness,
        compute_skill_hash,
        count_positional_args,
        extract_context_refs,
        extract_input_refs,
        generate_recipe_card,
        load_bundled_manifest,
        load_recipe_card,
        resolve_skill_name,
        validate_recipe_cards,
    )
    from autoskillit.recipe.validator import (  # noqa: F401
        _RULE_REGISTRY,
        _WORKTREE_CREATING_SKILLS,
        RuleFinding,
        RuleSpec,
        analyze_dataflow,
        run_semantic_rules,
        semantic_rule,
        validate_recipe,
    )


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
        from autoskillit.recipe.io import _parse_recipe

        data = {**VALID_RECIPE, "name": ""}
        wf = _parse_recipe(data)
        errors = validate_recipe(wf)
        assert any("name" in e.lower() for e in errors)

    def test_validate_recipe_is_callable(self) -> None:
        assert callable(validate_recipe)

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
        import dataclasses

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
        from autoskillit.recipe.io import _parse_recipe

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

    # CAP9
    def test_bundled_recipes_still_valid(self) -> None:
        bd = builtin_recipes_dir()
        for f in bd.glob("*.yaml"):
            wf = load_recipe(f)
            errors = validate_recipe(wf)
            assert errors == [], f"Regression in {f.name}: {errors}"


# ---------------------------------------------------------------------------
# TestAnalyzeDataflow — migrated from test_recipe_parser.py
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
        wf_dir = builtin_recipes_dir()
        yaml_files = list(wf_dir.glob("*.yaml")) + list(wf_dir.glob("*.yml"))
        assert len(yaml_files) > 0
        for yaml_file in yaml_files:
            wf = load_recipe(yaml_file)
            report = analyze_dataflow(wf)
            from autoskillit.recipe.schema import DataFlowReport

            assert isinstance(report, DataFlowReport)
            assert isinstance(report.warnings, list)


# ---------------------------------------------------------------------------
# Semantic rules — migrated from test_semantic_rules.py
# ---------------------------------------------------------------------------


def _make_workflow(steps: dict[str, dict]) -> Recipe:
    parsed_steps = {name: _parse_step(data) for name, data in steps.items()}
    return Recipe(name="test", description="test", steps=parsed_steps, kitchen_rules=["test"])


def test_registry_collects_rules() -> None:
    wf = _make_workflow(
        {
            "do_thing": {"tool": "run_cmd", "on_success": "done"},
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert isinstance(findings, list)
    assert all(isinstance(f, RuleFinding) for f in findings)


def test_unsatisfied_input_replaces_worktree_path_check() -> None:
    wf = _make_workflow(
        {
            "implement": {
                "tool": "run_skill",
                "with": {
                    "skill_command": (
                        "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                    ),
                },
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "on_success": "retry_step",
            },
            "retry_step": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": "/autoskillit:retry-worktree ${{ context.plan_path }}",
                },
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert any(f.rule == "missing-ingredient" and "worktree_path" in f.message for f in errors)


def test_unsatisfied_input_clean_when_provided() -> None:
    wf = _make_workflow(
        {
            "implement": {
                "tool": "run_skill",
                "with": {
                    "skill_command": (
                        "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                    ),
                },
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "on_success": "retry_step",
            },
            "retry_step": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": (
                        "/autoskillit:retry-worktree "
                        "${{ context.plan_path }} ${{ context.worktree_path }}"
                    ),
                },
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "missing-ingredient" for f in findings)


def test_unsatisfied_input_not_available() -> None:
    wf = _make_workflow(
        {
            "retry_step": {
                "tool": "run_skill_retry",
                "with": {"skill_command": "/autoskillit:retry-worktree ${{ context.plan_path }}"},
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    errors = [
        f for f in findings if f.rule == "missing-ingredient" and f.severity == Severity.ERROR
    ]
    assert any("worktree_path" in f.message for f in errors)


def test_unsatisfied_input_unknown_skill_ignored() -> None:
    wf = _make_workflow(
        {
            "step": {
                "tool": "run_skill",
                "with": {"skill_command": "/some-unknown-skill"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "missing-ingredient" for f in findings)


def test_unsatisfied_input_from_pipeline_inputs() -> None:
    wf = Recipe(
        name="test",
        description="test",
        ingredients={
            "plan_path": RecipeIngredient(description="Plan file", required=True),
            "worktree_path": RecipeIngredient(description="Worktree", required=True),
        },
        steps={
            "retry_step": _parse_step(
                {
                    "tool": "run_skill_retry",
                    "with": {
                        "skill_command": (
                            "/autoskillit:retry-worktree "
                            "${{ inputs.plan_path }} ${{ inputs.worktree_path }}"
                        ),
                    },
                    "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                    "on_success": "done",
                }
            ),
            "done": _parse_step({"action": "stop", "message": "Done."}),
        },
        kitchen_rules=["test"],
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "missing-ingredient" for f in findings)


def test_unsatisfied_input_inline_positional_args_skipped() -> None:
    wf = _make_workflow(
        {
            "investigate": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:investigate the test failures"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "missing-ingredient" for f in findings)


def test_retry_worktree_cwd_inputs_triggers_error() -> None:
    """retry-worktree step with cwd=inputs.* fires retry-worktree-cwd ERROR."""
    wf = _make_workflow(
        {
            "impl": {
                "tool": "run_skill_retry",
                "with": {"skill_command": "/autoskillit:implement-worktree-no-merge the plan"},
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "retry": {"on": "needs_retry", "max_attempts": 1, "on_exhausted": "retry_step"},
                "on_success": "done",
            },
            "retry_step": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": "/autoskillit:retry-worktree ${{ context.worktree_path }}",
                    "cwd": "${{ inputs.work_dir }}",
                },
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert any(f.rule == "retry-worktree-cwd" for f in errors)


def test_retry_worktree_cwd_context_clean() -> None:
    """retry-worktree step with cwd=context.worktree_path has no retry-worktree-cwd finding."""
    wf = _make_workflow(
        {
            "impl": {
                "tool": "run_skill_retry",
                "with": {"skill_command": "/autoskillit:implement-worktree-no-merge the plan"},
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "retry": {"on": "needs_retry", "max_attempts": 1, "on_exhausted": "retry_step"},
                "on_success": "done",
            },
            "retry_step": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": "/autoskillit:retry-worktree ${{ context.worktree_path }}",
                    "cwd": "${{ context.worktree_path }}",
                },
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "retry-worktree-cwd" for f in findings)


def test_retry_worktree_cwd_missing_triggers_error() -> None:
    """retry-worktree step with no cwd fires retry-worktree-cwd ERROR."""
    wf = _make_workflow(
        {
            "retry_step": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": "/autoskillit:retry-worktree ${{ context.worktree_path }}",
                },
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert any(f.rule == "retry-worktree-cwd" for f in errors)


def test_retry_worktree_cwd_non_skill_step_ignored() -> None:
    """retry-worktree-cwd rule only fires on skill steps, not run_cmd."""
    wf = _make_workflow(
        {
            "cmd": {
                "tool": "run_cmd",
                "with": {"cmd": "echo hello", "cwd": "${{ inputs.work_dir }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "retry-worktree-cwd" for f in findings)


def test_unreachable_steps_detects_orphan() -> None:
    wf = _make_workflow(
        {
            "start": {"tool": "run_cmd", "on_success": "done"},
            "orphan": {"tool": "run_cmd", "on_success": "done"},
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert any(f.rule == "unreachable-step" and "orphan" in f.message for f in findings)


def test_unreachable_steps_first_step_clean() -> None:
    wf = _make_workflow(
        {
            "start": {"tool": "run_cmd", "on_success": "done"},
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "unreachable-step" and "start" in f.step_name for f in findings)


def test_model_on_non_skill_triggers() -> None:
    wf = _make_workflow(
        {
            "check": {"tool": "test_check", "model": "sonnet", "on_success": "done"},
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert any(f.rule == "model-on-non-skill-step" for f in findings)


def test_model_on_non_skill_clean() -> None:
    wf = _make_workflow(
        {
            "do": {"tool": "run_skill", "model": "sonnet", "on_success": "done"},
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "model-on-non-skill-step" for f in findings)


def test_retry_without_capture_triggers() -> None:
    wf = _make_workflow(
        {
            "impl": {
                "tool": "run_skill_retry",
                "with": {"skill_command": "/implement"},
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "test",
            },
            "test": {
                "tool": "test_check",
                "with": {"worktree_path": "${{ context.worktree_path }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert any(f.rule == "retry-without-capture" for f in findings)


def test_retry_without_capture_clean_with_capture() -> None:
    wf = _make_workflow(
        {
            "impl": {
                "tool": "run_skill_retry",
                "with": {"skill_command": "/implement"},
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "on_success": "test",
            },
            "test": {
                "tool": "test_check",
                "with": {"worktree_path": "${{ context.worktree_path }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "retry-without-capture" for f in findings)


def test_rule_finding_to_dict() -> None:
    finding = RuleFinding(
        rule="test-rule",
        severity=Severity.WARNING,
        step_name="some_step",
        message="Something is wrong.",
    )
    d = finding.to_dict()
    assert d == {
        "rule": "test-rule",
        "severity": "warning",
        "step": "some_step",
        "message": "Something is wrong.",
    }


def test_old_rule_removed() -> None:
    from autoskillit.recipe.validator import _RULE_REGISTRY

    assert not any(r.name == "retry-without-worktree-path" for r in _RULE_REGISTRY)


def test_bundled_workflows_pass_semantic_rules() -> None:
    wf_dir = builtin_recipes_dir()
    yaml_files = list(wf_dir.glob("*.yaml"))
    assert yaml_files

    for path in yaml_files:
        wf = load_recipe(path)
        findings = run_semantic_rules(wf)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert not errors, (
            f"Bundled workflow {path.name} has error-severity semantic findings: {errors}"
        )
        undeclared_findings = [f for f in findings if f.rule == "undeclared-capture-key"]
        assert undeclared_findings == [], (
            f"Recipe '{wf.name}' has undeclared-capture-key findings: " + repr(undeclared_findings)
        )


class TestOutdatedScriptVersionRule:
    # MSR1
    def test_fires_when_version_below_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "0.2.0")
        wf = _make_workflow(
            {
                "do_thing": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        wf.version = "0.1.0"
        findings = run_semantic_rules(wf)
        assert len([f for f in findings if f.rule == "outdated-recipe-version"]) == 1

    # MSR2
    def test_does_not_fire_when_version_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "0.2.0")
        wf = _make_workflow(
            {
                "do_thing": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        wf.version = "0.2.0"
        findings = run_semantic_rules(wf)
        assert len([f for f in findings if f.rule == "outdated-recipe-version"]) == 0

    # MSR3
    def test_fires_when_version_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "0.2.0")
        wf = _make_workflow(
            {
                "do_thing": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        assert wf.version is None
        findings = run_semantic_rules(wf)
        assert len([f for f in findings if f.rule == "outdated-recipe-version"]) == 1

    # MSR4
    def test_finding_severity_is_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "0.2.0")
        wf = _make_workflow(
            {
                "do_thing": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        wf.version = "0.1.0"
        findings = run_semantic_rules(wf)
        version_findings = [f for f in findings if f.rule == "outdated-recipe-version"]
        assert len(version_findings) == 1
        assert version_findings[0].severity == Severity.WARNING


def test_worktree_retry_creates_new_triggers() -> None:
    wf = _make_workflow(
        {
            "implement": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": (
                        "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                    ),
                },
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "retry_wt"},
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "on_success": "done",
            },
            "retry_wt": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": (
                        "/autoskillit:retry-worktree "
                        "${{ context.plan_path }} ${{ context.worktree_path }}"
                    ),
                },
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert any(
        f.rule == "worktree-retry-creates-new" and "implement" in f.step_name for f in errors
    )


def test_worktree_retry_creates_new_clean_max_one() -> None:
    wf = _make_workflow(
        {
            "implement": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": (
                        "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                    ),
                },
                "retry": {"on": "needs_retry", "max_attempts": 1, "on_exhausted": "retry_wt"},
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "on_success": "done",
            },
            "retry_wt": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": (
                        "/autoskillit:retry-worktree "
                        "${{ context.plan_path }} ${{ context.worktree_path }}"
                    ),
                },
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "worktree-retry-creates-new" for f in findings)


class TestWeakConstraintRule:
    def _make_recipe_with_kitchen_rules(self, kitchen_rules: list[str]) -> Recipe:
        steps = {
            "run": _parse_step({"tool": "test_check", "on_success": "done"}),
            "done": _parse_step({"action": "stop", "message": "Done"}),
        }
        return Recipe(name="test", description="test", steps=steps, kitchen_rules=kitchen_rules)

    def test_weak_constraint_text_detected(self) -> None:
        wf = self._make_recipe_with_kitchen_rules(["Only use AutoSkillit MCP tools."])
        findings = run_semantic_rules(wf)
        weak = [f for f in findings if f.rule == "weak-constraint-text"]
        assert weak

    def test_detailed_constraints_pass(self) -> None:
        from autoskillit.core.types import PIPELINE_FORBIDDEN_TOOLS

        tool_list = ", ".join(PIPELINE_FORBIDDEN_TOOLS)
        constraint = f"NEVER use native tools ({tool_list}) from the orchestrator."
        wf = self._make_recipe_with_kitchen_rules([constraint])
        findings = run_semantic_rules(wf)
        weak = [f for f in findings if f.rule == "weak-constraint-text"]
        assert not weak


# ---------------------------------------------------------------------------
# Capture output coverage — undeclared-capture-key rule
# ---------------------------------------------------------------------------


class TestCaptureOutputCoverageRule:
    def test_capture_declared_output_key_no_warning(self) -> None:
        """A capture that references a key declared in the skill's outputs contract
        must not produce an undeclared-capture-key warning."""
        recipe_yaml = textwrap.dedent("""\
            name: capture-valid
            description: test
            steps:
              implement:
                tool: run_skill_retry
                with:
                  skill_command: /autoskillit:implement-worktree-no-merge ${{ inputs.plan }}
                capture:
                  worktree_path: "${{ result.worktree_path }}"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        undeclared = [f for f in findings if f.rule == "undeclared-capture-key"]
        assert undeclared == []

    def test_capture_undeclared_key_emits_warning(self) -> None:
        """A capture that references a key NOT listed in the skill's outputs contract
        must produce a Severity.WARNING finding with rule 'undeclared-capture-key'."""
        recipe_yaml = textwrap.dedent("""\
            name: capture-invalid-key
            description: test
            steps:
              implement:
                tool: run_skill_retry
                with:
                  skill_command: /autoskillit:implement-worktree-no-merge ${{ inputs.plan }}
                capture:
                  branch_name: "${{ result.branch_name }}"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        undeclared = [f for f in findings if f.rule == "undeclared-capture-key"]
        assert len(undeclared) == 1
        assert undeclared[0].severity == Severity.WARNING
        assert "branch_name" in undeclared[0].message
        assert "implement-worktree-no-merge" in undeclared[0].message

    def test_capture_from_skill_with_no_contract_emits_warning(self) -> None:
        """A capture step whose skill has no entry in skill_contracts.yaml at all
        must produce a Severity.WARNING finding with rule 'undeclared-capture-key'."""
        recipe_yaml = textwrap.dedent("""\
            name: capture-unknown-skill
            description: test
            steps:
              run_custom:
                tool: run_skill
                with:
                  skill_command: /autoskillit:not-a-real-skill some_arg
                capture:
                  result_key: "${{ result.some_key }}"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        undeclared = [f for f in findings if f.rule == "undeclared-capture-key"]
        assert len(undeclared) == 1
        assert undeclared[0].severity == Severity.WARNING
        assert "not-a-real-skill" in undeclared[0].message
        assert "no outputs contract entry" in undeclared[0].message

    def test_capture_key_from_empty_outputs_skill_emits_warning(self) -> None:
        """audit-friction has outputs: [] — any capture key from it is undeclared."""
        recipe_yaml = textwrap.dedent("""\
            name: capture-empty-outputs
            description: test
            steps:
              audit:
                tool: run_skill
                with:
                  skill_command: /autoskillit:audit-friction
                capture:
                  report_path: "${{ result.report_path }}"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        undeclared = [f for f in findings if f.rule == "undeclared-capture-key"]
        assert len(undeclared) == 1
        assert undeclared[0].severity == Severity.WARNING
        assert "report_path" in undeclared[0].message
        assert "audit-friction" in undeclared[0].message


# ---------------------------------------------------------------------------
# Contract validation — migrated from test_contract_validator.py
# ---------------------------------------------------------------------------


def test_load_bundled_manifest() -> None:
    manifest = load_bundled_manifest()
    assert manifest["version"] == "0.1.0"
    assert len(manifest["skills"]) == 17


def test_load_bundled_manifest_skill_inputs_typed() -> None:
    manifest = load_bundled_manifest()
    for skill_name, skill in manifest["skills"].items():
        assert "inputs" in skill
        assert "outputs" in skill
        for inp in skill["inputs"]:
            assert "name" in inp, f"{skill_name}: input missing 'name'"
            assert "type" in inp, f"{skill_name}: input {inp['name']} missing 'type'"
            assert "required" in inp, f"{skill_name}: input {inp['name']} missing 'required'"


def test_resolve_skill_name_standard() -> None:
    from autoskillit.recipe.contracts import resolve_skill_name

    assert (
        resolve_skill_name("/autoskillit:retry-worktree ${{ context.plan_path }}")
        == "retry-worktree"
    )


def test_resolve_skill_name_with_use_prefix() -> None:
    from autoskillit.recipe.contracts import resolve_skill_name

    assert (
        resolve_skill_name("Use /autoskillit:implement-worktree plan.md") == "implement-worktree"
    )


def test_resolve_skill_name_no_prefix() -> None:
    from autoskillit.recipe.contracts import resolve_skill_name

    assert resolve_skill_name("/do-stuff") is None


def test_resolve_skill_name_dynamic() -> None:
    from autoskillit.recipe.contracts import resolve_skill_name

    assert resolve_skill_name("/audit-${{ inputs.audit_type }}") is None


SAMPLE_PIPELINE_YAML = """\
name: test-pipeline
description: A test pipeline
summary: "Test flow"
inputs:
  plan_path:
    description: Plan file
    required: true
steps:
  implement:
    tool: run_skill
    with:
      skill_command: "/autoskillit:implement-worktree-no-merge ${{ inputs.plan_path }}"
    capture:
      worktree_path: "${{ result.worktree_path }}"
    on_success: test
  test:
    tool: test_check
    with:
      worktree_path: "${{ context.worktree_path }}"
    on_success: done
    on_failure: done
  done:
    action: stop
    message: "Done."
constraints:
  - test
"""


def test_generate_recipe_card(tmp_path: Path) -> None:
    recipes_dir = tmp_path / ".autoskillit" / "scripts"
    recipes_dir.mkdir(parents=True)
    pipeline = recipes_dir / "test-pipeline.yaml"
    pipeline.write_text(SAMPLE_PIPELINE_YAML)

    generate_recipe_card(pipeline, recipes_dir)

    contract_path = recipes_dir / "contracts" / "test-pipeline.yaml"
    assert contract_path.exists()
    contract = yaml.safe_load(contract_path.read_text())
    assert "generated_at" in contract
    assert "bundled_manifest_version" in contract
    assert "skill_hashes" in contract
    assert "skills" in contract
    assert "dataflow" in contract


def test_generate_recipe_card_returns_dict(tmp_path: Path) -> None:
    """generate_recipe_card returns a dict directly (not a Path)."""
    recipes_dir = tmp_path / ".autoskillit" / "scripts"
    recipes_dir.mkdir(parents=True)
    pipeline = recipes_dir / "test-dict-pipeline.yaml"
    pipeline.write_text(SAMPLE_PIPELINE_YAML)

    result = generate_recipe_card(pipeline, recipes_dir)

    assert isinstance(result, dict)
    assert "skill_hashes" in result


def test_generate_recipe_card_accepts_string_paths(tmp_path: Path) -> None:
    """generate_recipe_card must accept str paths (the JSON round-trip from run_python)."""
    recipes_dir = tmp_path / ".autoskillit" / "scripts"
    recipes_dir.mkdir(parents=True)
    pipeline = recipes_dir / "test-str-pipeline.yaml"
    pipeline.write_text(SAMPLE_PIPELINE_YAML)

    # Pass str arguments as run_python would after JSON round-trip
    result = generate_recipe_card(str(pipeline), str(recipes_dir))

    assert isinstance(result, dict)
    assert "skill_hashes" in result


def test_validate_recipe_uses_iter_steps_with_context_for_capture_refs(tmp_path: Path) -> None:
    """validate_recipe catches context refs not captured by preceding steps."""
    from autoskillit.recipe.io import iter_steps_with_context

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


def test_load_recipe_card(tmp_path: Path) -> None:
    recipes_dir = tmp_path / ".autoskillit" / "scripts"
    recipes_dir.mkdir(parents=True)
    pipeline = recipes_dir / "test-pipeline.yaml"
    pipeline.write_text(SAMPLE_PIPELINE_YAML)

    generate_recipe_card(pipeline, recipes_dir)

    contract = load_recipe_card("test-pipeline", recipes_dir)
    assert contract is not None
    assert contract["bundled_manifest_version"] == "0.1.0"


def test_load_recipe_card_missing() -> None:
    contract = load_recipe_card("nonexistent", Path("/tmp/no-scripts"))
    assert contract is None


def test_check_staleness_clean() -> None:
    contract = {
        "bundled_manifest_version": "0.1.0",
        "skill_hashes": {"investigate": compute_skill_hash("investigate")},
    }
    stale = check_contract_staleness(contract)
    assert len(stale) == 0


def test_check_staleness_version_mismatch() -> None:
    contract = {"bundled_manifest_version": "0.0.1", "skill_hashes": {}}
    stale = check_contract_staleness(contract)
    assert any(s.reason == "version_mismatch" for s in stale)


def test_check_staleness_hash_mismatch() -> None:
    contract = {
        "bundled_manifest_version": "0.1.0",
        "skill_hashes": {"investigate": "sha256:0000000000"},
    }
    stale = check_contract_staleness(contract)
    assert any(s.skill == "investigate" and s.reason == "hash_mismatch" for s in stale)


CLEAN_PIPELINE_YAML = """\
name: clean-pipeline
description: Pipeline with correct dataflow
summary: "Clean flow"
inputs:
  plan_path:
    description: Plan file
    required: true
steps:
  implement:
    tool: run_skill
    with:
      skill_command: "/autoskillit:implement-worktree-no-merge ${{ inputs.plan_path }}"
    capture:
      worktree_path: "${{ result.worktree_path }}"
    on_success: retry
  retry:
    tool: run_skill_retry
    with:
      skill_command: >-
        /autoskillit:retry-worktree
        ${{ inputs.plan_path }}
        ${{ context.worktree_path }}
    retry:
      on: needs_retry
      max_attempts: 3
      on_exhausted: done
    on_success: done
  done:
    action: stop
    message: "Done."
constraints:
  - test
"""

BAD_PIPELINE_YAML = """\
name: bad-pipeline
description: Pipeline with missing skill input
summary: "Bad flow"
inputs:
  plan_path:
    description: Plan file
    required: true
steps:
  implement:
    tool: run_skill
    with:
      skill_command: "/autoskillit:implement-worktree-no-merge ${{ inputs.plan_path }}"
    capture:
      worktree_path: "${{ result.worktree_path }}"
    on_success: retry
  retry:
    tool: run_skill_retry
    with:
      skill_command: "/autoskillit:retry-worktree ${{ inputs.plan_path }}"
    retry:
      on: needs_retry
      max_attempts: 3
      on_exhausted: done
    on_success: done
  done:
    action: stop
    message: "Done."
constraints:
  - test
"""


def test_validate_recipe_cards_clean(tmp_path: Path) -> None:
    recipes_dir = tmp_path / ".autoskillit" / "scripts"
    recipes_dir.mkdir(parents=True)
    pipeline = recipes_dir / "clean.yaml"
    pipeline.write_text(CLEAN_PIPELINE_YAML)

    contract = generate_recipe_card(pipeline, recipes_dir)

    findings = validate_recipe_cards(None, contract)
    assert len(findings) == 0


def test_validate_recipe_cards_missing_input(tmp_path: Path) -> None:
    recipes_dir = tmp_path / ".autoskillit" / "scripts"
    recipes_dir.mkdir(parents=True)
    pipeline = recipes_dir / "bad.yaml"
    pipeline.write_text(BAD_PIPELINE_YAML)

    contract = generate_recipe_card(pipeline, recipes_dir)

    findings = validate_recipe_cards(None, contract)
    assert len(findings) > 0
    assert any("worktree_path" in f["message"] for f in findings)


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
# dead-output semantic rule tests (T_DO1–T_DO3)
# ---------------------------------------------------------------------------


class TestDeadOutputRule:
    def test_do1_dead_output_rule_in_registry(self) -> None:
        """T_DO1: dead-output is in _RULE_REGISTRY."""
        from autoskillit.recipe.validator import _RULE_REGISTRY

        rule_names = [r.name for r in _RULE_REGISTRY]
        assert "dead-output" in rule_names

    def test_do2_fires_error_for_unconsumed_capture(self) -> None:
        """T_DO2: dead-output fires ERROR when a captured key is never consumed downstream."""
        steps = {
            "plan": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:make-plan do the task"},
                "capture": {"plan_path": "${{ result.plan_path }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        findings = run_semantic_rules(recipe)
        dead = [f for f in findings if f.rule == "dead-output"]
        assert len(dead) >= 1
        assert any(f.severity == Severity.ERROR and f.step_name == "plan" for f in dead)

    def test_do3_does_not_fire_for_on_result_self_consumption(self) -> None:
        """T_DO3: dead-output does NOT fire when on_result.field equals the captured key."""
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
        findings = run_semantic_rules(recipe)
        dead = [f for f in findings if f.rule == "dead-output" and f.step_name == "audit_impl"]
        assert dead == []

    def test_do4_cleanup_succeeded_from_merge_worktree_not_dead_output(self) -> None:
        """T_DO4: dead-output does NOT fire for cleanup_succeeded captured from merge_worktree.

        cleanup_succeeded is an observability capture required by merge-cleanup-uncaptured.
        It has no downstream consumer by design — the exemption prevents the two rules
        from conflicting.
        """
        recipe = _build_merge_worktree_recipe(
            capture={"cleanup_succeeded": "${{ result.cleanup_succeeded }}"}
        )
        findings = run_semantic_rules(recipe)
        dead = [f for f in findings if f.rule == "dead-output" and f.step_name == "merge"]
        assert dead == []


# ---------------------------------------------------------------------------
# implicit-handoff semantic rule tests (T_IH1–T_IH5)
# ---------------------------------------------------------------------------


class TestImplicitHandoffRule:
    def test_ih1_implicit_handoff_rule_in_registry(self) -> None:
        """T_IH1: implicit-handoff is in _RULE_REGISTRY."""
        from autoskillit.recipe.validator import _RULE_REGISTRY

        rule_names = [r.name for r in _RULE_REGISTRY]
        assert "implicit-handoff" in rule_names

    def test_ih2_fires_error_for_skill_with_outputs_and_no_capture(self) -> None:
        """T_IH2: implicit-handoff fires ERROR when skill has outputs but step has no capture."""
        steps = {
            "plan": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:make-plan do the task"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        findings = run_semantic_rules(recipe)
        ih = [f for f in findings if f.rule == "implicit-handoff"]
        assert len(ih) >= 1
        assert any(f.severity == Severity.ERROR and f.step_name == "plan" for f in ih)

    def test_ih3_does_not_fire_when_capture_block_present(self) -> None:
        """T_IH3: implicit-handoff does NOT fire when the step has a capture: block."""
        steps = {
            "plan": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:make-plan do the task"},
                "capture": {"plan_path": "${{ result.plan_path }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        findings = run_semantic_rules(recipe)
        ih = [f for f in findings if f.rule == "implicit-handoff" and f.step_name == "plan"]
        assert ih == []

    def test_ih4_does_not_fire_for_skill_with_empty_outputs(self) -> None:
        """T_IH4: implicit-handoff does NOT fire for a skill with outputs: []."""
        steps = {
            "assess": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:assess-and-merge worktree plan main"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        findings = run_semantic_rules(recipe)
        ih = [f for f in findings if f.rule == "implicit-handoff" and f.step_name == "assess"]
        assert ih == []

    def test_ih5_does_not_fire_for_unknown_skill(self) -> None:
        """T_IH5: implicit-handoff does NOT fire for a skill with no contract entry."""
        steps = {
            "step": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:not-a-real-skill something"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        findings = run_semantic_rules(recipe)
        ih = [f for f in findings if f.rule == "implicit-handoff" and f.step_name == "step"]
        assert ih == []


# ---------------------------------------------------------------------------
# Contract tests (T_SC1–T_SC5; T_SC6 is covered by test_load_bundled_manifest)
# ---------------------------------------------------------------------------


def test_sc1_audit_impl_has_real_inputs_and_outputs() -> None:
    """T_SC1: audit-impl has non-empty inputs and declares verdict/remediation_path outputs."""
    manifest = load_bundled_manifest()
    audit_impl = manifest["skills"]["audit-impl"]
    assert audit_impl["inputs"], "audit-impl should have non-empty inputs"
    output_names = {o["name"] for o in audit_impl["outputs"]}
    assert "verdict" in output_names
    assert "remediation_path" in output_names
    verdict_out = next(o for o in audit_impl["outputs"] if o["name"] == "verdict")
    assert verdict_out["type"] == "string"
    remediation_out = next(o for o in audit_impl["outputs"] if o["name"] == "remediation_path")
    assert remediation_out["type"] == "file_path"


def test_sc2_assess_and_merge_has_empty_outputs() -> None:
    """T_SC2: assess-and-merge has outputs: []."""
    manifest = load_bundled_manifest()
    assert manifest["skills"]["assess-and-merge"]["outputs"] == []


def test_sc3_dry_walkthrough_has_empty_outputs() -> None:
    """T_SC3: dry-walkthrough has outputs: []."""
    manifest = load_bundled_manifest()
    assert manifest["skills"]["dry-walkthrough"]["outputs"] == []


def test_sc4_investigate_has_empty_outputs() -> None:
    """T_SC4: investigate has outputs: []."""
    manifest = load_bundled_manifest()
    assert manifest["skills"]["investigate"]["outputs"] == []


def test_sc5_make_groups_outputs_include_group_files() -> None:
    """T_SC5: make-groups outputs list contains an entry with name='group_files'."""
    manifest = load_bundled_manifest()
    output_names = {o["name"] for o in manifest["skills"]["make-groups"]["outputs"]}
    assert "group_files" in output_names


# ---------------------------------------------------------------------------
# Recipe structural tests — implementation-pipeline.yaml (T_IP1–T_IP5)
# ---------------------------------------------------------------------------


class TestImplementationPipelineStructure:
    def setup_method(self) -> None:
        self.recipe = load_recipe(builtin_recipes_dir() / "implementation-pipeline.yaml")

    def test_ip1_group_step_captures_group_files(self) -> None:
        """T_IP1: group step has capture containing key group_files (not groups_path)."""
        assert "group_files" in self.recipe.steps["group"].capture
        assert "groups_path" not in self.recipe.steps["group"].capture

    def test_ip2_review_step_captures_review_path(self) -> None:
        """T_IP2: review step has capture containing key review_path."""
        assert "review_path" in self.recipe.steps["review"].capture

    def test_ip3_audit_impl_has_verdict_and_remediation_capture_and_on_result(
        self,
    ) -> None:
        """T_IP3: audit_impl captures verdict+remediation_path and routes via on_result."""
        step = self.recipe.steps["audit_impl"]
        assert "verdict" in step.capture
        assert "remediation_path" in step.capture
        assert step.on_result is not None
        assert step.on_result.field == "verdict"

    def test_ip4_verify_step_references_context_review_path(self) -> None:
        """T_IP4: verify step with_args contains a reference to context.review_path."""
        verify_with = self.recipe.steps["verify"].with_args
        assert any("context.review_path" in str(v) for v in verify_with.values())

    def test_ip5_audit_impl_has_no_on_success_or_on_failure(self) -> None:
        """T_IP5: audit_impl step has no on_success or on_failure (replaced by on_result)."""
        step = self.recipe.steps["audit_impl"]
        assert step.on_success is None
        assert step.on_failure is None

    def test_ip6_plan_step_note_contains_glob_pattern(self) -> None:
        """T_IP6: plan step note must contain *_part_*.md glob pattern for multi-part discovery."""
        note = self.recipe.steps["plan"].note or ""
        assert "*_part_*.md" in note, (
            "plan step note must contain glob pattern for multi-part discovery; "
            "if removed, agents will not discover part files"
        )

    def test_ip7_verify_step_note_sequential_constraint(self) -> None:
        """T_IP7: verify step note must contain sequential execution constraint."""
        note = self.recipe.steps["verify"].note or ""
        assert "SEQUENTIAL EXECUTION" in note or "full cycle" in note.lower(), (
            "verify step note must contain sequential constraint; "
            "without it agents may batch-verify all parts before implementing any"
        )

    def test_ip8_next_or_done_routes_more_parts_to_verify(self) -> None:
        """T_IP8: next_or_done routes more_parts back to verify for sequential processing."""
        step = self.recipe.steps["next_or_done"]
        assert step.on_result is not None
        assert step.on_result.routes.get("more_parts") == "verify", (
            "next_or_done must route more_parts back to verify for sequential part processing"
        )

    def test_ip9_next_or_done_routes_all_done_to_audit_impl(self) -> None:
        """T_IP9: next_or_done must route all_done to audit_impl."""
        step = self.recipe.steps["next_or_done"]
        assert step.on_result is not None
        assert step.on_result.routes.get("all_done") == "audit_impl"


# ---------------------------------------------------------------------------
# Recipe structural tests — bugfix-loop.yaml (T_BL1–T_BL2)
# ---------------------------------------------------------------------------


class TestBugfixLoopStructure:
    def setup_method(self) -> None:
        self.recipe = load_recipe(builtin_recipes_dir() / "bugfix-loop.yaml")

    def test_bl1_audit_impl_has_verdict_and_remediation_capture_and_on_result(
        self,
    ) -> None:
        """T_BL1: audit_impl captures verdict+remediation_path and routes via on_result."""
        step = self.recipe.steps["audit_impl"]
        assert "verdict" in step.capture
        assert "remediation_path" in step.capture
        assert step.on_result is not None
        assert step.on_result.field == "verdict"

    def test_bl2_remediate_step_exists_with_on_success_plan(self) -> None:
        """T_BL2: a step named remediate exists with on_success == 'plan'."""
        assert "remediate" in self.recipe.steps
        assert self.recipe.steps["remediate"].on_success == "plan"


# ---------------------------------------------------------------------------
# Recipe structural tests — investigate-first.yaml (T_IF1–T_IF2)
# ---------------------------------------------------------------------------


class TestInvestigateFirstStructure:
    def setup_method(self) -> None:
        self.recipe = load_recipe(builtin_recipes_dir() / "investigate-first.yaml")

    def test_if1_audit_impl_has_verdict_and_remediation_capture_and_on_result(
        self,
    ) -> None:
        """T_IF1: audit_impl captures verdict+remediation_path and routes via on_result."""
        step = self.recipe.steps["audit_impl"]
        assert "verdict" in step.capture
        assert "remediation_path" in step.capture
        assert step.on_result is not None
        assert step.on_result.field == "verdict"

    def test_if2_remediate_step_exists_with_on_success_rectify(self) -> None:
        """T_IF2: a step named remediate exists with on_success == 'rectify'."""
        assert "remediate" in self.recipe.steps
        assert self.recipe.steps["remediate"].on_success == "rectify"


# ---------------------------------------------------------------------------
# Semantic rule tests — multipart iteration conventions (T_MI1–T_MI2)
# ---------------------------------------------------------------------------


class TestMultipartIterationRule:
    def test_mi1_multipart_rule_warns_on_missing_glob_note(self) -> None:
        """T_MI1: multipart-glob-note fires when make-plan step has no *_part_*.md in note."""
        recipe = Recipe(
            name="test-recipe",
            description="test",
            ingredients={},
            steps={
                "plan": RecipeStep(
                    tool="run_skill_retry",
                    with_args={"skill_command": "/autoskillit:make-plan inputs.task"},
                    on_success="verify",
                    note="Produces a plan file.",
                ),
                "verify": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:dry-walkthrough context.plan_path"},
                    on_success="done",
                ),
                "done": RecipeStep(action="stop", message="Done"),
            },
            kitchen_rules=[],
        )
        warnings = run_semantic_rules(recipe)
        rule_names = [w.rule for w in warnings]
        assert "multipart-glob-note" in rule_names

    def test_mi2_multipart_rule_passes_compliant_recipe(self) -> None:
        """T_MI2: Validator emits no multipart warnings when all conventions are present."""
        recipe = Recipe(
            name="test-recipe",
            description="test",
            ingredients={},
            steps={
                "plan": RecipeStep(
                    tool="run_skill_retry",
                    with_args={"skill_command": "/autoskillit:make-plan inputs.task"},
                    on_success="verify",
                    note="Glob plan_dir for *_part_*.md or single plan file.",
                ),
                "verify": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:dry-walkthrough context.plan_path"},
                    on_success="next_or_done",
                ),
                "next_or_done": RecipeStep(
                    action="route",
                    on_result=StepResultRoute(
                        field="next", routes={"more_parts": "verify", "all_done": "done"}
                    ),
                ),
                "done": RecipeStep(action="stop", message="Done"),
            },
            kitchen_rules=["SEQUENTIAL EXECUTION: complete full cycle per part before advancing."],
        )
        warnings = run_semantic_rules(recipe)
        rule_names = [w.rule for w in warnings]
        assert "multipart-glob-note" not in rule_names
        assert "multipart-sequential-kitchen-rule" not in rule_names
        assert "multipart-route-back" not in rule_names


# ---------------------------------------------------------------------------
# Smoke-test YAML structural tests (T_ST1–T_ST5)
# ---------------------------------------------------------------------------


class TestSmokeTestStructure:
    """Structural assertions for the smoke-test.yaml recipe steps."""

    @pytest.fixture()
    def smoke_yaml(self) -> dict:
        recipe_path = builtin_recipes_dir() / "smoke-test.yaml"
        return yaml.safe_load(recipe_path.read_text())

    # T_ST1
    def test_create_branch_is_run_cmd(self, smoke_yaml: dict) -> None:
        """create_branch step has tool == "run_cmd" (not action == "route")."""
        assert smoke_yaml["steps"]["create_branch"]["tool"] == "run_cmd"

    # T_ST2
    def test_create_branch_captures_feature_branch(self, smoke_yaml: dict) -> None:
        """create_branch step has capture containing key feature_branch."""
        assert "feature_branch" in smoke_yaml["steps"]["create_branch"]["capture"]

    # T_ST3
    def test_check_summary_is_run_python(self, smoke_yaml: dict) -> None:
        """check_summary step has python discriminator (not action == "route")."""
        assert (
            smoke_yaml["steps"]["check_summary"]["python"]
            == "autoskillit.smoke_utils.check_bug_report_non_empty"
        )

    # T_ST4
    def test_check_summary_on_result_routes(self, smoke_yaml: dict) -> None:
        """check_summary step has on_result with field non_empty and routes true/false."""
        on_result = smoke_yaml["steps"]["check_summary"]["on_result"]
        assert on_result["field"] == "non_empty"
        assert "true" in on_result["routes"]
        assert "false" in on_result["routes"]

    # T_ST5
    def test_merge_references_context_feature_branch(self, smoke_yaml: dict) -> None:
        """merge step with_args references context.feature_branch."""
        base_branch = smoke_yaml["steps"]["merge"]["with"]["base_branch"]
        assert "context.feature_branch" in base_branch


# ---------------------------------------------------------------------------
# Contract tests — plan_parts output (D4–D5)
# ---------------------------------------------------------------------------


def test_make_plan_contract_declares_plan_parts_output() -> None:
    """D4: make-plan contract must declare plan_parts as an output."""
    manifest = load_bundled_manifest()
    make_plan = manifest.get("skills", {}).get("make-plan", {})
    output_names = [o["name"] for o in make_plan.get("outputs", [])]
    assert "plan_parts" in output_names, (
        "make-plan contract must declare plan_parts as an output "
        "so capture_list coverage validation can enforce it"
    )


def test_rectify_contract_declares_plan_parts_output() -> None:
    """D5: rectify contract must declare plan_parts as an output."""
    manifest = load_bundled_manifest()
    rectify = manifest.get("skills", {}).get("rectify", {})
    output_names = [o["name"] for o in rectify.get("outputs", [])]
    assert "plan_parts" in output_names


# ---------------------------------------------------------------------------
# Semantic rule tests — multipart plan_parts capture (D6–D7)
# ---------------------------------------------------------------------------


@pytest.fixture
def compliant_multipart_recipe_no_list() -> Recipe:
    """Recipe with make-plan step but no capture_list for plan_parts."""
    return Recipe(
        name="test",
        description="test",
        ingredients={},
        steps={
            "plan": RecipeStep(
                tool="run_skill_retry",
                with_args={"skill_command": "/autoskillit:make-plan inputs.task"},
                capture={"plan_path": "${{ result.plan_path }}"},
                note="Glob plan_dir for *_part_*.md or single plan file. Sort into plan_parts[].",
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="Done"),
        },
        kitchen_rules=["SEQUENTIAL EXECUTION: complete full cycle per part."],
    )


@pytest.fixture
def compliant_multipart_recipe_with_list() -> Recipe:
    """Recipe with make-plan step and correct capture_list for plan_parts."""
    return Recipe(
        name="test",
        description="test",
        ingredients={},
        steps={
            "plan": RecipeStep(
                tool="run_skill_retry",
                with_args={"skill_command": "/autoskillit:make-plan inputs.task"},
                capture={"plan_path": "${{ result.plan_path }}"},
                capture_list={"plan_parts": "${{ result.plan_parts }}"},
                note="Glob plan_dir for *_part_*.md or single plan file. Sort into plan_parts[].",
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="Done"),
        },
        kitchen_rules=["SEQUENTIAL EXECUTION: complete full cycle per part."],
    )


def test_validator_warns_when_plan_parts_not_captured(
    compliant_multipart_recipe_no_list: Recipe,
) -> None:
    """D6: Validator warns when make-plan step lacks capture_list for plan_parts."""
    warnings = run_semantic_rules(compliant_multipart_recipe_no_list)
    rule_names = [w.rule for w in warnings]
    assert "multipart-plan-parts-not-captured" in rule_names


def test_validator_passes_when_plan_parts_captured(
    compliant_multipart_recipe_with_list: Recipe,
) -> None:
    """D7: Validator passes when make-plan step has capture_list for plan_parts."""
    warnings = run_semantic_rules(compliant_multipart_recipe_with_list)
    rule_names = [w.rule for w in warnings]
    assert "multipart-plan-parts-not-captured" not in rule_names


# ---------------------------------------------------------------------------
# N12: merge-cleanup-uncaptured semantic rule
# ---------------------------------------------------------------------------


def _build_merge_worktree_recipe(capture: dict) -> Recipe:
    """Helper: build a minimal Recipe with a merge_worktree step and the given capture dict."""
    return Recipe(
        name="test-merge",
        description="Test merge recipe",
        summary="merge > done",
        steps={
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "${{ context.worktree_path }}", "base_branch": "main"},
                capture=capture,
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="Done"),
        },
    )


def test_semantic_rule_warns_merge_worktree_without_cleanup_capture() -> None:
    """N12: merge_worktree step without cleanup_succeeded captured emits warning."""
    recipe = _build_merge_worktree_recipe(capture={})
    findings = run_semantic_rules(recipe)
    assert any(f.rule == "merge-cleanup-uncaptured" for f in findings)


def test_semantic_rule_warns_merge_worktree_with_unrelated_capture() -> None:
    """N12: merge_worktree step capturing only merge_succeeded still warns about cleanup."""
    recipe = _build_merge_worktree_recipe(capture={"merged": "${{ result.merge_succeeded }}"})
    findings = run_semantic_rules(recipe)
    assert any(f.rule == "merge-cleanup-uncaptured" for f in findings)


def test_semantic_rule_passes_when_cleanup_captured() -> None:
    """N12: No merge-cleanup-uncaptured warning when cleanup_succeeded is captured."""
    recipe = _build_merge_worktree_recipe(
        capture={"cleanup_ok": "${{ result.cleanup_succeeded }}"}
    )
    findings = run_semantic_rules(recipe)
    assert not any(f.rule == "merge-cleanup-uncaptured" for f in findings)


def test_merge_cleanup_uncaptured_rule_not_triggered_on_non_merge_step() -> None:
    """N12: The rule does not fire on non-merge_worktree steps."""
    recipe = Recipe(
        name="test-non-merge",
        description="Test recipe without merge_worktree",
        summary="run > done",
        steps={
            "run": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo hi", "cwd": "/tmp"},
                capture={},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="Done"),
        },
    )
    findings = run_semantic_rules(recipe)
    assert not any(f.rule == "merge-cleanup-uncaptured" for f in findings)


def test_bundled_recipes_capture_cleanup_succeeded() -> None:
    """N12: All bundled recipes with merge_worktree steps must capture cleanup_succeeded."""
    wf_dir = builtin_recipes_dir()
    yaml_files = list(wf_dir.glob("*.yaml"))
    assert yaml_files

    for path in yaml_files:
        wf = load_recipe(path)
        findings = run_semantic_rules(wf)
        uncaptured = [f for f in findings if f.rule == "merge-cleanup-uncaptured"]
        assert not uncaptured, (
            f"Bundled recipe {path.name} emits merge-cleanup-uncaptured: {uncaptured}"
        )
