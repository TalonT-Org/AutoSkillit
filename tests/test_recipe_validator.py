"""Tests for recipe_validator — structural validation, semantic rules, and contracts."""

from __future__ import annotations

import importlib
import textwrap
from pathlib import Path

import pytest
import yaml

from autoskillit.recipe_io import (
    _parse_recipe,
    _parse_step,
    builtin_recipes_dir,
    list_recipes,
    load_recipe,
)
from autoskillit.recipe_schema import Recipe, RecipeIngredient
from autoskillit.recipe_validator import (
    RuleFinding,
    Severity,
    analyze_dataflow,
    check_contract_staleness,
    compute_skill_hash,
    generate_recipe_card,
    load_bundled_manifest,
    load_recipe_card,
    run_semantic_rules,
    validate_recipe,
    validate_recipe_cards,
)
from autoskillit.types import RETRY_RESPONSE_FIELDS

# ---------------------------------------------------------------------------
# Importability assertions
# ---------------------------------------------------------------------------


def test_all_symbols_importable() -> None:
    """All expected symbols are importable from recipe_validator."""
    from autoskillit.recipe_validator import (  # noqa: F401
        _RULE_REGISTRY,
        _WORKTREE_CREATING_SKILLS,
        DataflowEntry,
        RecipeCard,
        RuleFinding,
        RuleSpec,
        Severity,
        SkillContract,
        SkillInput,
        SkillOutput,
        analyze_dataflow,
        check_contract_staleness,
        compute_skill_hash,
        count_positional_args,
        extract_context_refs,
        extract_input_refs,
        generate_recipe_card,
        load_bundled_manifest,
        load_recipe_card,
        resolve_skill_name,
        run_semantic_rules,
        semantic_rule,
        validate_recipe,
        validate_recipe_cards,
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
        from autoskillit.recipe_io import _parse_recipe

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
        from autoskillit.recipe_io import _parse_recipe

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
        from autoskillit.recipe_schema import DataFlowReport

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
            from autoskillit.recipe_schema import DataFlowReport

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
    from autoskillit.recipe_validator import _RULE_REGISTRY

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
        from autoskillit.types import PIPELINE_FORBIDDEN_TOOLS

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
                  skill_command: /autoskillit:audit-friction ${{ inputs.plan }}
                capture:
                  verdict: "${{ result.verdict }}"
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
        assert "verdict" in undeclared[0].message
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
    from autoskillit.recipe_validator import resolve_skill_name

    assert (
        resolve_skill_name("/autoskillit:retry-worktree ${{ context.plan_path }}")
        == "retry-worktree"
    )


def test_resolve_skill_name_with_use_prefix() -> None:
    from autoskillit.recipe_validator import resolve_skill_name

    assert (
        resolve_skill_name("Use /autoskillit:implement-worktree plan.md") == "implement-worktree"
    )


def test_resolve_skill_name_no_prefix() -> None:
    from autoskillit.recipe_validator import resolve_skill_name

    assert resolve_skill_name("/do-stuff") is None


def test_resolve_skill_name_dynamic() -> None:
    from autoskillit.recipe_validator import resolve_skill_name

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


def test_validate_recipe_uses_iter_steps_with_context_for_capture_refs(tmp_path: Path) -> None:
    """validate_recipe catches context refs not captured by preceding steps."""
    from autoskillit.recipe_io import iter_steps_with_context

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


class TestIsInstanceGuards:
    """T_GD1, T_GV1 — isinstance guards prevent TypeError on non-string with_args."""

    def test_detect_dead_outputs_no_raise_with_boolean_with_arg(self) -> None:
        """T_GD1: _detect_dead_outputs does not raise TypeError for boolean with_args."""
        recipe_yaml = textwrap.dedent("""\
            name: guard-test
            description: test
            kitchen_rules: ["No native tools."]
            steps:
              plan:
                tool: run_skill
                with:
                  skill_command: "/autoskillit:make-plan the task"
                  flag: true
                capture:
                  plan_path: "${{ result.plan_path }}"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        # Must not raise TypeError
        report = analyze_dataflow(recipe)
        assert isinstance(report.warnings, list)

    def test_validate_recipe_no_raise_with_boolean_with_arg(self) -> None:
        """T_GV1: validate_recipe does not raise TypeError for boolean with_args."""
        recipe_yaml = textwrap.dedent("""\
            name: guard-test
            description: test
            kitchen_rules: ["No native tools."]
            steps:
              plan:
                tool: run_skill
                with:
                  skill_command: "/autoskillit:make-plan the task"
                  flag: true
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        # Must return a list, not raise
        result = validate_recipe(recipe)
        assert isinstance(result, list)


class TestOnResultConsumption:
    """T_OR1, T_OR2 — on_result.field counts as consumption in _detect_dead_outputs."""

    def test_on_result_field_match_not_flagged_as_dead_output(self) -> None:
        """T_OR1: verdict captured and routed via on_result.field is NOT dead."""
        recipe_yaml = textwrap.dedent("""\
            name: or-test
            description: test
            kitchen_rules: ["No native tools."]
            steps:
              audit_impl:
                tool: run_skill
                with:
                  skill_command: "/autoskillit:audit-impl the plan impl main"
                capture:
                  verdict: "${{ result.verdict }}"
                  remediation_path: "${{ result.remediation_path }}"
                on_result:
                  field: verdict
                  routes:
                    GO: done
                    NO GO: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        report = analyze_dataflow(recipe)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT" and w.field == "verdict"]
        assert dead == [], f"verdict should not be flagged as dead: {dead}"

    def test_on_result_different_field_still_flags_dead_output(self) -> None:
        """T_OR2: verdict is flagged DEAD_OUTPUT when on_result.field is a different key."""
        recipe_yaml = textwrap.dedent("""\
            name: or-test
            description: test
            kitchen_rules: ["No native tools."]
            steps:
              audit_impl:
                tool: run_skill
                with:
                  skill_command: "/autoskillit:audit-impl the plan impl main"
                capture:
                  verdict: "${{ result.verdict }}"
                  remediation_path: "${{ result.remediation_path }}"
                on_result:
                  field: restart_scope
                  routes:
                    full_restart: done
                    partial_restart: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        report = analyze_dataflow(recipe)
        dead_fields = {w.field for w in report.warnings if w.code == "DEAD_OUTPUT"}
        assert "verdict" in dead_fields


class TestDeadOutputRule:
    """T_DO1–T_DO3 — dead-output semantic rule."""

    def test_dead_output_rule_in_registry(self) -> None:
        """T_DO1: dead-output rule exists in _RULE_REGISTRY."""
        from autoskillit.recipe_validator import _RULE_REGISTRY

        rule_names = [spec.name for spec in _RULE_REGISTRY]
        assert "dead-output" in rule_names

    def test_dead_output_fires_when_captured_key_unconsumed(self) -> None:
        """T_DO2: ERROR when captured key is never consumed downstream."""
        recipe_yaml = textwrap.dedent("""\
            name: dead-test
            description: test
            kitchen_rules: ["No native tools."]
            steps:
              plan:
                tool: run_skill
                with:
                  skill_command: "/autoskillit:make-plan the task"
                capture:
                  plan_path: "${{ result.plan_path }}"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        dead = [f for f in findings if f.rule == "dead-output"]
        assert len(dead) >= 1
        match = next(f for f in dead if f.step_name == "plan")
        assert match.severity == Severity.ERROR

    def test_dead_output_does_not_fire_when_on_result_self_consumes(self) -> None:
        """T_DO3: dead-output does NOT fire when on_result.field equals captured key."""
        recipe_yaml = textwrap.dedent("""\
            name: or-self-test
            description: test
            kitchen_rules: ["No native tools."]
            steps:
              audit_impl:
                tool: run_skill
                with:
                  skill_command: "/autoskillit:audit-impl the plan impl main"
                capture:
                  verdict: "${{ result.verdict }}"
                  remediation_path: "${{ result.remediation_path }}"
                on_result:
                  field: verdict
                  routes:
                    GO: done
                    NO GO: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        dead_verdict = [f for f in findings if f.rule == "dead-output" and "verdict" in f.message]
        assert dead_verdict == [], f"verdict must not be flagged: {dead_verdict}"


class TestImplicitHandoffRule:
    """T_IH1–T_IH5 — implicit-handoff semantic rule."""

    def test_implicit_handoff_rule_in_registry(self) -> None:
        """T_IH1: implicit-handoff rule exists in _RULE_REGISTRY."""
        from autoskillit.recipe_validator import _RULE_REGISTRY

        rule_names = [spec.name for spec in _RULE_REGISTRY]
        assert "implicit-handoff" in rule_names

    def test_implicit_handoff_fires_when_outputs_declared_and_no_capture(self) -> None:
        """T_IH2: ERROR when make-plan step has declared outputs and no capture block."""
        recipe_yaml = textwrap.dedent("""\
            name: ih-test
            description: test
            kitchen_rules: ["No native tools."]
            steps:
              plan:
                tool: run_skill
                with:
                  skill_command: "/autoskillit:make-plan the task"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        ih = [f for f in findings if f.rule == "implicit-handoff"]
        assert len(ih) >= 1
        assert ih[0].severity == Severity.ERROR
        assert ih[0].step_name == "plan"

    def test_implicit_handoff_does_not_fire_when_capture_present(self) -> None:
        """T_IH3: no implicit-handoff when capture: block is present."""
        recipe_yaml = textwrap.dedent("""\
            name: ih-capture-test
            description: test
            kitchen_rules: ["No native tools."]
            steps:
              plan:
                tool: run_skill
                with:
                  skill_command: "/autoskillit:make-plan the task"
                capture:
                  plan_path: "${{ result.plan_path }}"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        ih = [f for f in findings if f.rule == "implicit-handoff" and f.step_name == "plan"]
        assert ih == []

    def test_implicit_handoff_does_not_fire_for_empty_outputs_skill(self) -> None:
        """T_IH4: no implicit-handoff when skill has outputs: []."""
        recipe_yaml = textwrap.dedent("""\
            name: ih-empty-test
            description: test
            kitchen_rules: ["No native tools."]
            steps:
              assess:
                tool: run_skill
                with:
                  skill_command: "/autoskillit:assess-and-merge worktree plan main"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        ih = [f for f in findings if f.rule == "implicit-handoff"]
        assert ih == []

    def test_implicit_handoff_does_not_fire_for_unknown_skill(self) -> None:
        """T_IH5: no implicit-handoff for skill with no contract entry."""
        recipe_yaml = textwrap.dedent("""\
            name: ih-unknown-test
            description: test
            kitchen_rules: ["No native tools."]
            steps:
              custom:
                tool: run_skill
                with:
                  skill_command: "/my-custom-skill do something"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        ih = [f for f in findings if f.rule == "implicit-handoff"]
        assert ih == []


# ---------------------------------------------------------------------------
# Contract tests (T_SC1–T_SC6)
# ---------------------------------------------------------------------------


def test_sc1_audit_impl_has_verdict_and_remediation_path_outputs() -> None:
    """T_SC1: audit-impl declares verdict and remediation_path outputs."""
    manifest = load_bundled_manifest()
    contract = manifest["skills"]["audit-impl"]
    assert contract["inputs"], "audit-impl must have non-empty inputs"
    output_names = {o["name"] for o in contract["outputs"]}
    assert "verdict" in output_names
    assert "remediation_path" in output_names


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


def test_sc5_make_groups_has_group_files_output() -> None:
    """T_SC5: make-groups outputs list contains group_files."""
    manifest = load_bundled_manifest()
    output_names = {o["name"] for o in manifest["skills"]["make-groups"]["outputs"]}
    assert "group_files" in output_names


def test_sc6_bundled_manifest_skill_count_unchanged() -> None:
    """T_SC6: skill count in manifest is still 17 (no net change)."""
    manifest = load_bundled_manifest()
    assert len(manifest["skills"]) == 17


class TestImplementationPipelineStructure:
    """T_IP1–T_IP5 — structural assertions for implementation-pipeline.yaml."""

    @pytest.fixture()
    def impl_yaml(self) -> dict:
        recipe_path = builtin_recipes_dir() / "implementation-pipeline.yaml"
        return yaml.safe_load(recipe_path.read_text())

    def test_group_step_captures_group_files(self, impl_yaml: dict) -> None:
        """T_IP1: group step capture contains key group_files (not groups_path)."""
        assert "group_files" in impl_yaml["steps"]["group"]["capture"]
        assert "groups_path" not in impl_yaml["steps"]["group"]["capture"]

    def test_review_step_captures_review_path(self, impl_yaml: dict) -> None:
        """T_IP2: review step capture contains key review_path."""
        assert "review_path" in impl_yaml["steps"]["review"]["capture"]

    def test_audit_impl_captures_verdict_and_remediation_path(self, impl_yaml: dict) -> None:
        """T_IP3: audit_impl step captures verdict and remediation_path, routes via on_result."""
        capture = impl_yaml["steps"]["audit_impl"]["capture"]
        assert "verdict" in capture
        assert "remediation_path" in capture
        on_result = impl_yaml["steps"]["audit_impl"]["on_result"]
        assert on_result["field"] == "verdict"

    def test_verify_step_with_has_review_path(self, impl_yaml: dict) -> None:
        """T_IP4: verify step with_args references context.review_path."""
        with_args = impl_yaml["steps"]["verify"]["with"]
        review_path_val = with_args.get("review_path", "")
        assert "context.review_path" in review_path_val

    def test_audit_impl_has_no_on_success_or_on_failure(self, impl_yaml: dict) -> None:
        """T_IP5: audit_impl step has no on_success or on_failure (replaced by on_result)."""
        step = impl_yaml["steps"]["audit_impl"]
        assert "on_success" not in step
        assert "on_failure" not in step


class TestBugfixLoopStructure:
    """T_BL1–T_BL2 — structural assertions for bugfix-loop.yaml."""

    @pytest.fixture()
    def bl_yaml(self) -> dict:
        recipe_path = builtin_recipes_dir() / "bugfix-loop.yaml"
        return yaml.safe_load(recipe_path.read_text())

    def test_audit_impl_captures_verdict_and_remediation_and_on_result(
        self, bl_yaml: dict
    ) -> None:
        """T_BL1: audit_impl step captures verdict + remediation_path, routes via on_result."""
        capture = bl_yaml["steps"]["audit_impl"]["capture"]
        assert "verdict" in capture
        assert "remediation_path" in capture
        on_result = bl_yaml["steps"]["audit_impl"]["on_result"]
        assert on_result["field"] == "verdict"

    def test_remediate_step_exists_with_on_success_plan(self, bl_yaml: dict) -> None:
        """T_BL2: remediate step exists with on_success == 'plan'."""
        assert "remediate" in bl_yaml["steps"]
        assert bl_yaml["steps"]["remediate"]["on_success"] == "plan"


class TestInvestigateFirstStructure:
    """T_IF1–T_IF2 — structural assertions for investigate-first.yaml."""

    @pytest.fixture()
    def if_yaml(self) -> dict:
        recipe_path = builtin_recipes_dir() / "investigate-first.yaml"
        return yaml.safe_load(recipe_path.read_text())

    def test_audit_impl_captures_verdict_and_remediation_and_on_result(
        self, if_yaml: dict
    ) -> None:
        """T_IF1: audit_impl step captures verdict + remediation_path, routes via on_result."""
        capture = if_yaml["steps"]["audit_impl"]["capture"]
        assert "verdict" in capture
        assert "remediation_path" in capture
        on_result = if_yaml["steps"]["audit_impl"]["on_result"]
        assert on_result["field"] == "verdict"

    def test_remediate_step_exists_with_on_success_rectify(self, if_yaml: dict) -> None:
        """T_IF2: remediate step exists with on_success == 'rectify'."""
        assert "remediate" in if_yaml["steps"]
        assert if_yaml["steps"]["remediate"]["on_success"] == "rectify"
