"""Tests for recipe semantic rules — all semantic rule validations."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from autoskillit.core.types import Severity
from autoskillit.recipe.io import (
    _parse_recipe,
    _parse_step,
    builtin_recipes_dir,
    load_recipe,
)
from autoskillit.recipe.schema import (
    Recipe,
    RecipeIngredient,
    RecipeStep,
    StepResultRoute,
    StepRetry,
)
from autoskillit.recipe.validator import (
    RuleFinding,
    run_semantic_rules,
)


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def _make_workflow(steps: dict[str, dict]) -> Recipe:
    parsed_steps = {name: _parse_step(data) for name, data in steps.items()}
    return Recipe(name="test", description="test", steps=parsed_steps, kitchen_rules=["test"])


# ---------------------------------------------------------------------------
# Module-level semantic rule tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# TestOutdatedScriptVersionRule
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Module-level worktree-retry tests
# ---------------------------------------------------------------------------


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
    # Original assertion: no worktree-retry-creates-new violations (still valid)
    assert not any(f.rule == "worktree-retry-creates-new" for f in findings)
    # New assertion: the needs-retry-no-restart rule DOES fire for this pattern
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert any(
        f.rule == "needs-retry-no-restart" and "implement" in f.step_name for f in errors
    ), "Expected needs-retry-no-restart ERROR — max_attempts:1 with needs_retry is forbidden"


def test_needs_retry_on_worktree_creating_skill_with_attempts_is_error() -> None:
    """max_attempts >= 1 AND on=needs_retry on a worktree-creating skill must be ERROR."""
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
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert any(
        f.rule == "needs-retry-no-restart" and "implement" in f.step_name for f in errors
    ), f"Expected needs-retry-no-restart ERROR on implement step, got: {findings}"


def test_needs_retry_worktree_creating_skill_max_attempts_zero_is_clean() -> None:
    """max_attempts: 0 with on=needs_retry on worktree-creating skill must pass."""
    wf = _make_workflow(
        {
            "implement": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": (
                        "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                    ),
                },
                "retry": {"on": "needs_retry", "max_attempts": 0, "on_exhausted": "retry_wt"},
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
    assert not any(f.rule == "needs-retry-no-restart" for f in errors), (
        f"Unexpected needs-retry-no-restart ERROR with max_attempts=0: {findings}"
    )


# ---------------------------------------------------------------------------
# TestWeakConstraintRule
# ---------------------------------------------------------------------------


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
# TestCaptureOutputCoverageRule
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
                  nonexistent_output: "${{ result.nonexistent_output }}"
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
        assert "nonexistent_output" in undeclared[0].message
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
# TestDeadOutputRule
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
# TestImplicitHandoffRule
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
                "with": {"skill_command": "/autoskillit:resolve-failures worktree plan main"},
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
# TestCloneRootAsWorktreeRule
# ---------------------------------------------------------------------------


class TestCloneRootAsWorktreeRule:
    def test_crw1_rule_in_registry(self) -> None:
        """T_CRW1: clone-root-as-worktree is registered in _RULE_REGISTRY."""
        from autoskillit.recipe.validator import _RULE_REGISTRY

        assert "clone-root-as-worktree" in {r.name for r in _RULE_REGISTRY}

    def _bad_recipe_test_check(self) -> Recipe:
        """Helper: recipe where work_dir is captured from clone_path, test_check uses it."""
        return _parse_recipe(
            {
                "name": "bad-recipe",
                "description": "test",
                "kitchen_rules": ["NEVER use native tools"],
                "steps": {
                    "clone": {
                        "python": "autoskillit.workspace.clone.clone_repo",
                        "with": {"source_dir": "/src", "run_name": "r"},
                        "capture": {"work_dir": "${{ result.clone_path }}"},
                        "on_success": "test",
                        "on_failure": "stop_err",
                    },
                    "test": {
                        "tool": "test_check",
                        "with": {"worktree_path": "${{ context.work_dir }}"},
                        "on_success": "stop_ok",
                        "on_failure": "stop_err",
                    },
                    "stop_ok": {"action": "stop", "message": "ok"},
                    "stop_err": {"action": "stop", "message": "err"},
                },
            }
        )

    def test_crw2_rule_fires_for_test_check(self) -> None:
        """T_CRW2: ERROR when test_check passes work_dir (from clone_path) as worktree_path."""
        recipe = self._bad_recipe_test_check()
        findings = run_semantic_rules(recipe)
        crw = [f for f in findings if f.rule == "clone-root-as-worktree"]
        assert len(crw) >= 1
        assert crw[0].severity == Severity.ERROR
        assert crw[0].step_name == "test"

    def test_crw3_rule_fires_for_merge_worktree(self) -> None:
        """T_CRW3: ERROR when merge_worktree passes work_dir (from clone_path) as worktree_path."""
        recipe = _parse_recipe(
            {
                "name": "bad-merge",
                "description": "test",
                "kitchen_rules": ["NEVER use native tools"],
                "steps": {
                    "clone": {
                        "python": "autoskillit.workspace.clone.clone_repo",
                        "with": {"source_dir": "/src", "run_name": "r"},
                        "capture": {"work_dir": "${{ result.clone_path }}"},
                        "on_success": "merge",
                        "on_failure": "stop_err",
                    },
                    "merge": {
                        "tool": "merge_worktree",
                        "with": {
                            "worktree_path": "${{ context.work_dir }}",
                            "base_branch": "main",
                        },
                        "capture": {"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                        "on_success": "stop_ok",
                        "on_failure": "stop_err",
                    },
                    "stop_ok": {"action": "stop", "message": "ok"},
                    "stop_err": {"action": "stop", "message": "err"},
                },
            }
        )
        findings = run_semantic_rules(recipe)
        crw = [f for f in findings if f.rule == "clone-root-as-worktree"]
        assert len(crw) >= 1
        assert crw[0].severity == Severity.ERROR
        assert crw[0].step_name == "merge"

    def test_crw4_passes_for_worktree_from_result_worktree_path(self) -> None:
        """T_CRW4: no finding when worktree_path captured from result.worktree_path (correct)."""
        recipe = _parse_recipe(
            {
                "name": "good-recipe",
                "description": "test",
                "kitchen_rules": ["NEVER use native tools"],
                "steps": {
                    "implement": {
                        "tool": "run_skill_retry",
                        "with": {
                            "skill_command": "/autoskillit:implement-worktree-no-merge plan.md"
                        },
                        "capture": {"implementation_ref": "${{ result.worktree_path }}"},
                        "on_success": "test",
                        "on_failure": "stop_err",
                    },
                    "test": {
                        "tool": "test_check",
                        "with": {"worktree_path": "${{ context.implementation_ref }}"},
                        "on_success": "stop_ok",
                        "on_failure": "stop_err",
                    },
                    "stop_ok": {"action": "stop", "message": "ok"},
                    "stop_err": {"action": "stop", "message": "err"},
                },
            }
        )
        findings = run_semantic_rules(recipe)
        crw = [f for f in findings if f.rule == "clone-root-as-worktree"]
        assert crw == []

    def test_crw5_bundled_recipes_pass_clone_root_rule(self) -> None:
        """T_CRW5: no bundled recipe triggers clone-root-as-worktree."""
        bd = builtin_recipes_dir()
        for yaml_path in sorted(bd.glob("*.yaml")):
            recipe = load_recipe(yaml_path)
            findings = run_semantic_rules(recipe)
            crw = [f for f in findings if f.rule == "clone-root-as-worktree"]
            assert crw == [], f"{yaml_path.name} triggered clone-root-as-worktree: {crw}"


# ---------------------------------------------------------------------------
# TestMultipartIterationRule
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
# merge-cleanup-uncaptured and plan-parts rule tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# TestOnResultMissingFailureRoute
# ---------------------------------------------------------------------------


class TestOnResultMissingFailureRoute:
    """RCA: on-result-missing-failure-route semantic rule."""

    def test_RCA1_tool_step_on_result_no_on_failure_fires_error(self) -> None:
        """RCA1: run_skill step with on_result but no on_failure → Severity.ERROR finding."""
        wf = _make_workflow(
            {
                "audit": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/autoskillit:audit-impl plan.md impl main"},
                    "capture": {"verdict": "${{ result.verdict }}"},
                    "on_result": {"field": "verdict", "routes": {"GO": "done", "NO GO": "fix"}},
                    # no on_failure — the gap
                },
                "fix": {"action": "stop", "message": "Fix needed."},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        rule_names = [f.rule for f in errors]
        assert any(
            f.rule == "on-result-missing-failure-route" and f.step_name == "audit" for f in errors
        ), f"Expected on-result-missing-failure-route ERROR on 'audit'. Got: {rule_names}"

    def test_RCA2_python_step_on_result_no_on_failure_fires_error(self) -> None:
        """RCA2: python step with on_result but no on_failure → Severity.ERROR."""
        wf = _make_workflow(
            {
                "check": {
                    "python": "mymod.check_result",
                    "on_result": {"field": "status", "routes": {"ok": "done", "fail": "fix"}},
                    # no on_failure
                },
                "fix": {"action": "stop", "message": "Fix."},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert any(f.rule == "on-result-missing-failure-route" for f in errors)

    def test_RCA3_on_result_with_on_failure_no_finding(self) -> None:
        """RCA3: on_result + on_failure present → rule does not fire."""
        wf = _make_workflow(
            {
                "audit": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/autoskillit:audit-impl plan.md impl main"},
                    "capture": {"verdict": "${{ result.verdict }}"},
                    "on_result": {"field": "verdict", "routes": {"GO": "done", "NO GO": "fix"}},
                    "on_failure": "done",
                },
                "fix": {"action": "stop", "message": "Fix needed."},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        assert not any(f.rule == "on-result-missing-failure-route" for f in findings)

    def test_RCA4_action_route_on_result_no_on_failure_not_an_error(self) -> None:
        """RCA4: action:route with on_result but no on_failure → NOT flagged.
        Agent routing decisions are not MCP tool invocations; they cannot fail
        the same way and are exempt from this rule.
        """
        wf = _make_workflow(
            {
                "decide": {
                    "action": "route",
                    "on_result": {
                        "field": "parts",
                        "routes": {"more": "implement", "done": "finish"},
                    },
                    # no on_failure — intentional for action:route
                },
                "implement": {"action": "stop", "message": "Implement."},
                "finish": {"action": "stop", "message": "Finish."},
            }
        )
        findings = run_semantic_rules(wf)
        assert not any(f.rule == "on-result-missing-failure-route" for f in findings)

    def test_RCA5_optional_true_plus_on_result_no_on_failure_fires(self) -> None:
        """RCA5: optional:true does not exempt a step from needing on_failure."""
        wf = _make_workflow(
            {
                "audit": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/autoskillit:audit-impl plan.md impl main"},
                    "capture": {"verdict": "${{ result.verdict }}"},
                    "on_result": {"field": "verdict", "routes": {"GO": "done", "NO GO": "fix"}},
                    "optional": True,
                    # no on_failure
                },
                "fix": {"action": "stop", "message": "Fix needed."},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert any(f.rule == "on-result-missing-failure-route" for f in errors)


# ---------------------------------------------------------------------------
# TestStaleRefAfterMerge
# ---------------------------------------------------------------------------


def _make_stale_worktree_path_recipe() -> Recipe:
    """Return the stale-worktree-path recipe used by tests B1 and B6."""
    return Recipe(
        name="test-stale-path",
        description="test",
        steps={
            "implement": RecipeStep(
                tool="run_skill_retry",
                with_args={
                    "skill_command": "/autoskillit:implement-worktree-no-merge plan.md",
                },
                capture={"worktree_path": "${{ result.worktree_path }}"},
                on_success="test",
            ),
            "test": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "${{ context.worktree_path }}"},
                on_success="merge",
            ),
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={
                    "worktree_path": "${{ context.worktree_path }}",
                    "base_branch": "main",
                },
                capture={"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                on_success="audit",
            ),
            "audit": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": (
                        "/autoskillit:audit-impl plan.md ${{ context.worktree_path }} main"
                    ),
                },
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
    )


@pytest.fixture
def all_bundled_recipes() -> list[tuple[str, Recipe]]:
    """Load all bundled recipe YAML files and return as (name, Recipe) pairs."""
    result = []
    for yaml_file in builtin_recipes_dir().glob("*.yaml"):
        result.append((yaml_file.stem, load_recipe(yaml_file)))
    return result


class TestStaleRefAfterMerge:
    """Part B: stale-ref-after-merge semantic rule and _detect_ref_invalidations()."""

    def test_B1_stale_ref_after_merge_fires_for_worktree_path(self) -> None:
        """B1: Rule fires when a worktree_path capture is consumed after merge_worktree."""
        recipe = _make_stale_worktree_path_recipe()
        findings = run_semantic_rules(recipe)
        stale_findings = [f for f in findings if f.rule == "stale-ref-after-merge"]
        assert stale_findings, (
            "Expected stale-ref-after-merge finding for worktree_path used after merge"
        )
        assert any(f.step_name == "audit" for f in stale_findings)

    def test_B2_stale_ref_after_merge_fires_for_branch_name(self) -> None:
        """B2: Rule fires when a branch_name capture is consumed after merge_worktree."""
        recipe = Recipe(
            name="test-stale-branch",
            description="test",
            steps={
                "implement": RecipeStep(
                    tool="run_skill_retry",
                    with_args={
                        "skill_command": "/autoskillit:implement-worktree-no-merge plan.md",
                    },
                    capture={"branch_name": "${{ result.branch_name }}"},
                    on_success="merge",
                ),
                "merge": RecipeStep(
                    tool="merge_worktree",
                    with_args={"worktree_path": "../worktrees/wt", "base_branch": "main"},
                    capture={"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                    on_success="audit",
                ),
                "audit": RecipeStep(
                    tool="run_skill",
                    with_args={
                        "skill_command": (
                            "/autoskillit:audit-impl plan.md ${{ context.branch_name }} main"
                        ),
                    },
                    on_success="done",
                ),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        findings = run_semantic_rules(recipe)
        stale = [f for f in findings if f.rule == "stale-ref-after-merge"]
        assert stale, "Expected stale-ref-after-merge finding for branch_name used after merge"
        assert any(f.step_name == "audit" for f in stale)

    def test_B3_stale_ref_after_merge_clean_when_sha_used(self) -> None:
        """B3: Rule does NOT fire when audit_impl uses a stable SHA, not a branch ref."""
        recipe = Recipe(
            name="test-clean-sha",
            description="test",
            steps={
                "capture_sha": RecipeStep(
                    tool="run_cmd",
                    with_args={"cmd": "git rev-parse main", "cwd": "/work"},
                    capture={"base_sha": "${{ result.stdout }}"},
                    on_success="implement",
                ),
                "implement": RecipeStep(
                    tool="run_skill_retry",
                    with_args={
                        "skill_command": "/autoskillit:implement-worktree-no-merge plan.md",
                    },
                    capture={
                        "worktree_path": "${{ result.worktree_path }}",
                        "branch_name": "${{ result.branch_name }}",
                    },
                    on_success="merge",
                ),
                "merge": RecipeStep(
                    tool="merge_worktree",
                    with_args={
                        "worktree_path": "${{ context.worktree_path }}",
                        "base_branch": "main",
                    },
                    capture={"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                    on_success="audit",
                ),
                "audit": RecipeStep(
                    tool="run_skill",
                    with_args={
                        "skill_command": (
                            "/autoskillit:audit-impl plan.md ${{ context.base_sha }} main"
                        ),
                    },
                    on_success="done",
                ),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        findings = run_semantic_rules(recipe)
        stale = [f for f in findings if f.rule == "stale-ref-after-merge"]
        assert not stale, f"Expected no stale-ref findings when base_sha is used: {stale}"

    def test_B4_stale_ref_after_merge_clean_before_merge(self) -> None:
        """B4: Rule does NOT fire when worktree_path is only consumed before merge_worktree."""
        recipe = Recipe(
            name="test-before-merge",
            description="test",
            steps={
                "implement": RecipeStep(
                    tool="run_skill_retry",
                    with_args={
                        "skill_command": "/autoskillit:implement-worktree-no-merge plan.md",
                    },
                    capture={"worktree_path": "${{ result.worktree_path }}"},
                    on_success="audit",
                ),
                "audit": RecipeStep(
                    tool="run_skill",
                    with_args={
                        "skill_command": (
                            "/autoskillit:audit-impl plan.md ${{ context.worktree_path }} main"
                        ),
                    },
                    on_success="merge",
                ),
                "merge": RecipeStep(
                    tool="merge_worktree",
                    with_args={
                        "worktree_path": "${{ context.worktree_path }}",
                        "base_branch": "main",
                    },
                    capture={"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                    on_success="done",
                ),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        findings = run_semantic_rules(recipe)
        stale = [f for f in findings if f.rule == "stale-ref-after-merge"]
        assert not stale, (
            "Expected no stale-ref findings when worktree_path is consumed BEFORE merge: "
            + str(stale)
        )

    def test_B5_bundled_recipes_pass_stale_ref_rule_after_part_a(
        self, all_bundled_recipes: list[tuple[str, Recipe]]
    ) -> None:
        """B5: All bundled recipes must pass the stale-ref-after-merge rule after Part A fixes."""
        for recipe_name, recipe in all_bundled_recipes:
            findings = run_semantic_rules(recipe)
            stale = [f for f in findings if f.rule == "stale-ref-after-merge"]
            assert not stale, (
                f"Bundled recipe '{recipe_name}' has stale-ref-after-merge violations: {stale}"
            )

    def test_B6_detect_ref_invalidations_in_dataflow_report(self) -> None:
        """B6: analyze_dataflow() emits REF_INVALIDATED warnings for stale-ref patterns."""
        from autoskillit.recipe.validator import analyze_dataflow

        recipe = _make_stale_worktree_path_recipe()
        report = analyze_dataflow(recipe)
        ref_warnings = [w for w in report.warnings if w.code == "REF_INVALIDATED"]
        assert ref_warnings, "Expected REF_INVALIDATED warnings in DataFlowReport"
        assert any(w.step_name == "audit" for w in ref_warnings)


# ---------------------------------------------------------------------------
# TestOnRetryField
# ---------------------------------------------------------------------------


class TestOnRetryField:
    """Tests for on_retry as a first-class routing field and cycle detection."""

    def test_on_retry_invalid_target_raises_validation_error(self) -> None:
        """on_retry must reference a declared step name."""
        from autoskillit.recipe.validator import validate_recipe

        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "fix": RecipeStep(
                    tool="run_skill",
                    on_success="done",
                    on_failure="cleanup",
                    on_retry="nonexistent_step",
                    with_args={"skill_command": "x", "cwd": "/tmp"},
                ),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        errors = validate_recipe(recipe)
        assert errors, "Expected validation errors for unknown on_retry target"
        assert any("on_retry" in e for e in errors)

    def test_on_retry_valid_target_passes_validation(self) -> None:
        """on_retry referencing a valid step passes validation."""
        from autoskillit.recipe.validator import validate_recipe

        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "fix": RecipeStep(
                    tool="run_skill",
                    on_success="done",
                    on_failure="cleanup",
                    on_retry="verify",
                    with_args={"skill_command": "x", "cwd": "/tmp"},
                ),
                "verify": RecipeStep(
                    tool="test_check",
                    on_success="done",
                    on_failure="cleanup",
                    with_args={"worktree_path": "/tmp"},
                ),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        errors = validate_recipe(recipe)
        assert not errors, f"Expected no errors but got: {errors}"

    def test_on_retry_and_retry_on_needs_retry_is_mutually_exclusive(self) -> None:
        """A step with both on_retry and retry.on='needs_retry' must be a validation error."""
        from autoskillit.recipe.validator import validate_recipe

        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "fix": RecipeStep(
                    tool="run_skill",
                    on_success="done",
                    on_failure="cleanup",
                    on_retry="verify",
                    with_args={"skill_command": "x", "cwd": "/tmp"},
                    retry=StepRetry(max_attempts=3, on="needs_retry", on_exhausted="cleanup"),
                ),
                "verify": RecipeStep(action="stop", message="done"),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        errors = validate_recipe(recipe)
        assert any("on_retry" in e and "retry" in e for e in errors)

    def test_unbounded_cycle_without_retry_block_produces_warning(self) -> None:
        """verify → assess → verify cycle without retry.max_attempts must produce a warning."""
        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "assess": RecipeStep(
                    tool="run_skill",
                    on_success="verify",
                    on_failure="cleanup",
                    with_args={"skill_command": "x", "cwd": "/tmp"},
                ),
                "verify": RecipeStep(
                    tool="test_check",
                    on_success="done",
                    on_failure="assess",
                    with_args={"worktree_path": "/tmp"},
                ),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        findings = run_semantic_rules(recipe)
        warnings = [f for f in findings if f.severity == Severity.WARNING]
        assert any(
            "unbounded" in f.message.lower() or "cycle" in f.message.lower() for f in warnings
        )

    def test_bounded_cycle_with_retry_block_does_not_warn(self) -> None:
        """A cycle with retry.max_attempts on the cycling step should NOT warn."""
        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "fix": RecipeStep(
                    tool="run_skill",
                    on_success="test",
                    on_failure="cleanup",
                    with_args={"skill_command": "x", "cwd": "/tmp"},
                    retry=StepRetry(max_attempts=3, on="needs_retry", on_exhausted="cleanup"),
                ),
                "test": RecipeStep(
                    tool="test_check",
                    on_success="done",
                    on_failure="fix",
                    with_args={"worktree_path": "/tmp"},
                ),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        findings = run_semantic_rules(recipe)
        cycle_warnings = [
            f for f in findings if "cycle" in f.message.lower() or "unbounded" in f.message.lower()
        ]
        assert not cycle_warnings, f"Expected no cycle warnings but got: {cycle_warnings}"

    def test_truly_trapped_cycle_without_exit_produces_error(self) -> None:
        """A cycle where every step's edges stay inside the cycle must produce an ERROR."""
        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "assess": RecipeStep(
                    tool="run_skill",
                    on_success="verify",
                    on_failure="verify",
                    with_args={"skill_command": "x", "cwd": "/tmp"},
                ),
                "verify": RecipeStep(
                    tool="test_check",
                    on_success="assess",
                    on_failure="assess",
                    with_args={"worktree_path": "/tmp"},
                ),
            },
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert any(
            "cycle" in f.message.lower() or "unbounded" in f.message.lower() for f in errors
        )


# ---------------------------------------------------------------------------
# Multipart plan_parts capture tests (D6–D7)
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
