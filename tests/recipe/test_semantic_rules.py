"""Tests for recipe semantic rules — all semantic rule validations."""

from __future__ import annotations

import textwrap

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


# ---------------------------------------------------------------------------
# optional / skip_when_false standalone tests
# ---------------------------------------------------------------------------


def test_optional_without_skip_when_fires_error() -> None:
    """optional: true without skip_when_false must be an ERROR."""
    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="opt_step"),
            "opt_step": RecipeStep(
                tool="run_skill",
                optional=True,
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:investigate plan.md", "cwd": "/tmp"},
                note="Optional step.",
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )
    violations = run_semantic_rules(recipe)
    rule_findings = [v for v in violations if v.rule == "optional-without-skip-when"]
    assert len(rule_findings) == 1
    assert rule_findings[0].severity == Severity.ERROR


def test_optional_with_skip_when_does_not_fire() -> None:
    """optional: true WITH skip_when_false must not fire the optional-without-skip-when rule."""
    from autoskillit.recipe.schema import RecipeIngredient

    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="opt_step"),
            "opt_step": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.run_audit",
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:investigate plan.md", "cwd": "/tmp"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        ingredients={
            "run_audit": RecipeIngredient(description="", required=False, default="true")
        },
        kitchen_rules=["test"],
    )
    violations = run_semantic_rules(recipe)
    rule_findings = [v for v in violations if v.rule == "optional-without-skip-when"]
    assert rule_findings == []


def test_skip_when_false_referencing_undeclared_ingredient_fires() -> None:
    """skip_when_false must reference a declared ingredient; undeclared must fire ERROR."""
    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="opt_step"),
            "opt_step": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.nonexistent_ingredient",
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:investigate plan.md", "cwd": "/tmp"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        # "nonexistent_ingredient" is NOT in ingredients
        kitchen_rules=["test"],
    )
    violations = run_semantic_rules(recipe)
    rule_findings = [v for v in violations if v.rule == "skip-when-false-undeclared"]
    assert len(rule_findings) == 1


class TestPushBeforeAuditRule:
    def test_ppb1_audit_before_push_no_finding(self) -> None:
        """PPB1: audit-impl runs before push_to_remote — no warning emitted."""
        recipe = _make_workflow(
            {
                "start": {"tool": "run_cmd", "on_success": "audit"},
                "audit": {
                    "tool": "run_skill",
                    "on_success": "push",
                    "with": {"skill_command": "/autoskillit:audit-impl plan.md", "cwd": "/tmp"},
                },
                "push": {
                    "tool": "push_to_remote",
                    "on_success": "done",
                    "with": {
                        "clone_path": "/tmp/clone",
                        "source_dir": "/tmp/src",
                        "branch": "main",
                    },
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = [f for f in run_semantic_rules(recipe) if f.rule == "push-before-audit"]
        assert findings == []

    def test_ppb2_push_before_audit_fires_warning(self) -> None:
        """PPB2: push_to_remote is reachable without any audit-impl step → WARNING."""
        recipe = _make_workflow(
            {
                "start": {"tool": "run_cmd", "on_success": "push"},
                "push": {
                    "tool": "push_to_remote",
                    "on_success": "done",
                    "with": {
                        "clone_path": "/tmp/clone",
                        "source_dir": "/tmp/src",
                        "branch": "main",
                    },
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = [f for f in run_semantic_rules(recipe) if f.rule == "push-before-audit"]
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING
        assert findings[0].step_name == "push"

    def test_ppb3_no_push_step_no_finding(self) -> None:
        """PPB3: recipe has no push_to_remote step — rule is silent."""
        recipe = _make_workflow(
            {
                "start": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = [f for f in run_semantic_rules(recipe) if f.rule == "push-before-audit"]
        assert findings == []

    def test_ip_push_after_audit_now_correctly_has_violation(self) -> None:
        """T_IP_PBA: bypass path via skip_when_false makes push-before-audit fire.

        Uses a synthetic recipe mirroring implementation-pipeline topology:
          start → audit_impl (optional, skip_when_false) → open_pr_step → push
        The skip_when_false bypass allows push to be reached without audit.

        The real recipe YAML will have skip_when_false added in Part B, at which
        point the TestImplementationPipelineStructure fixture will also trigger this rule.
        """
        recipe = _make_workflow(
            {
                "start": {"tool": "run_cmd", "on_success": "audit_impl"},
                "audit_impl": {
                    "tool": "run_skill",
                    "optional": True,
                    "skip_when_false": "inputs.audit",
                    "with": {
                        "skill_command": "/autoskillit:audit-impl plan.md",
                        "cwd": "/tmp",
                    },
                    "on_success": "open_pr_step",
                    "on_failure": "done",
                },
                "open_pr_step": {
                    "tool": "run_skill",
                    "optional": True,
                    "skip_when_false": "inputs.open_pr",
                    "with": {
                        "skill_command": "/autoskillit:open-pr",
                        "cwd": "/tmp",
                    },
                    "on_success": "push",
                },
                "push": {
                    "tool": "push_to_remote",
                    "on_success": "done",
                    "with": {
                        "clone_path": "/tmp/clone",
                        "source_dir": "/tmp/src",
                        "branch": "main",
                    },
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(recipe)
        violations = [f for f in findings if f.rule == "push-before-audit"]
        assert len(violations) >= 1
        assert violations[0].severity == Severity.WARNING


# ===========================================================================
# merge-base-unpublished rule tests
# ===========================================================================


class TestMergeBaseUnpublishedRule:
    """Tests for the merge-base-unpublished semantic rule."""

    def test_merge_base_unpublished_rule_fires_when_push_absent(self) -> None:
        """merge-base-unpublished ERROR fires when merge_worktree.base_branch
        is a context variable without a preceding push_to_remote step."""
        recipe = _make_workflow(
            {
                "clone": {
                    "tool": "clone_repo",
                    "with": {"source_dir": ".", "run_name": "test"},
                    "on_success": "create_branch",
                },
                "create_branch": {
                    "tool": "run_cmd",
                    "with": {"cmd": "echo test", "cwd": "/tmp"},
                    "capture": {"merge_target": "${{ result.stdout }}"},
                    "on_success": "merge_step",
                },
                "merge_step": {
                    "tool": "merge_worktree",
                    "with": {
                        "worktree_path": "${{ context.worktree_path }}",
                        "base_branch": "${{ context.merge_target }}",
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "done"},
            }
        )
        findings = run_semantic_rules(recipe)
        rule_findings = [f for f in findings if f.rule == "merge-base-unpublished"]
        assert len(rule_findings) >= 1
        assert any(f.severity == Severity.ERROR for f in rule_findings)

    def test_merge_base_unpublished_rule_passes_when_push_precedes_merge(self) -> None:
        """merge-base-unpublished does NOT fire when push_to_remote appears
        on the path to merge_worktree for the same context variable."""
        recipe = _make_workflow(
            {
                "clone": {
                    "tool": "clone_repo",
                    "with": {"source_dir": ".", "run_name": "test"},
                    "on_success": "create_branch",
                },
                "create_branch": {
                    "tool": "run_cmd",
                    "with": {"cmd": "echo test", "cwd": "/tmp"},
                    "capture": {"merge_target": "${{ result.stdout }}"},
                    "on_success": "push_target",
                },
                "push_target": {
                    "tool": "push_to_remote",
                    "with": {
                        "clone_path": "/tmp",
                        "branch": "${{ context.merge_target }}",
                        "remote_url": "https://example.com/repo.git",
                    },
                    "on_success": "merge_step",
                },
                "merge_step": {
                    "tool": "merge_worktree",
                    "with": {
                        "worktree_path": "${{ context.worktree_path }}",
                        "base_branch": "${{ context.merge_target }}",
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "done"},
            }
        )
        findings = run_semantic_rules(recipe)
        assert not any(f.rule == "merge-base-unpublished" for f in findings)

    def test_merge_base_unpublished_rule_does_not_fire_for_literal_branch(self) -> None:
        """merge-base-unpublished does NOT fire when base_branch is a
        literal string — literals like 'main' are always published."""
        recipe = _make_workflow(
            {
                "start": {
                    "tool": "run_cmd",
                    "with": {"cmd": "echo ok", "cwd": "/tmp"},
                    "on_success": "merge_step",
                },
                "merge_step": {
                    "tool": "merge_worktree",
                    "with": {
                        "worktree_path": "${{ context.worktree_path }}",
                        "base_branch": "main",
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "done"},
            }
        )
        findings = run_semantic_rules(recipe)
        assert not any(f.rule == "merge-base-unpublished" for f in findings)

    def test_implementation_pipeline_satisfies_push_before_merge_contract(self) -> None:
        """implementation-pipeline.yaml must pass the merge-base-unpublished
        rule after the push_merge_target step is added."""
        recipe = load_recipe(builtin_recipes_dir() / "implementation-pipeline.yaml")
        findings = run_semantic_rules(recipe)
        assert not any(f.rule == "merge-base-unpublished" for f in findings)


# ===========================================================================
# Predicate-based on_result routing — structural validation, semantic rules,
# dataflow, and recipe integration tests
# ===========================================================================


class TestPredicateOnResultValidation:
    """Structural validation for predicate-format on_result (conditions list)."""

    def _make_merge_recipe(self, merge_step: dict, extra_steps: dict | None = None) -> Recipe:
        steps: dict = {
            "merge": merge_step,
            "assess": {"action": "stop", "message": "Assess."},
            "cleanup_failure": {"action": "stop", "message": "Cleanup."},
            "push": {"action": "stop", "message": "Push."},
        }
        if extra_steps:
            steps.update(extra_steps)
        return _make_workflow(steps)

    def test_predicate_on_result_on_success_mutually_exclusive(self) -> None:
        """Step with predicate on_result (list) + on_success → validation error."""
        from autoskillit.recipe.validator import validate_recipe

        wf = self._make_merge_recipe(
            {
                "tool": "merge_worktree",
                "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                "on_result": [
                    {"when": "result.error", "route": "cleanup_failure"},
                    {"route": "push"},
                ],
                "on_success": "push",  # mutually exclusive
            }
        )
        errors = validate_recipe(wf)
        assert any("on_result" in e and "on_success" in e for e in errors)

    def test_predicate_on_result_on_failure_mutually_exclusive(self) -> None:
        """Step with predicate on_result (list) + on_failure → validation error."""
        from autoskillit.recipe.validator import validate_recipe

        wf = self._make_merge_recipe(
            {
                "tool": "merge_worktree",
                "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                "on_result": [
                    {"when": "result.error", "route": "cleanup_failure"},
                    {"route": "push"},
                ],
                "on_failure": "cleanup_failure",  # mutually exclusive with predicate format
            }
        )
        errors = validate_recipe(wf)
        assert any("on_failure" in e and "predicate" in e.lower() for e in errors)

    def test_predicate_condition_invalid_route_target_rejected(self) -> None:
        """A condition referencing an unknown step name is a validation error."""
        from autoskillit.recipe.validator import validate_recipe

        wf = self._make_merge_recipe(
            {
                "tool": "merge_worktree",
                "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                "on_result": [
                    {"when": "result.error", "route": "nonexistent_step"},
                    {"route": "push"},
                ],
            }
        )
        errors = validate_recipe(wf)
        assert any("nonexistent_step" in e for e in errors)

    def test_predicate_condition_route_valid_step_accepted(self) -> None:
        """All condition routes pointing to valid step names pass validation."""
        from autoskillit.recipe.validator import validate_recipe

        wf = self._make_merge_recipe(
            {
                "tool": "merge_worktree",
                "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                "on_result": [
                    {"when": "result.error", "route": "cleanup_failure"},
                    {"route": "push"},
                ],
                "capture": {"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
            }
        )
        errors = validate_recipe(wf)
        assert errors == []

    def test_predicate_format_no_on_failure_required(self) -> None:
        """merge_worktree step with predicate on_result and no on_failure passes validation."""
        from autoskillit.recipe.validator import validate_recipe

        wf = self._make_merge_recipe(
            {
                "tool": "merge_worktree",
                "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                "on_result": [
                    {"when": "result.failed_step == 'test_gate'", "route": "assess"},
                    {"when": "result.error", "route": "cleanup_failure"},
                    {"route": "push"},
                ],
                "capture": {"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
            }
        )
        errors = validate_recipe(wf)
        assert errors == []

    def test_predicate_on_result_empty_conditions_rejected(self) -> None:
        """on_result with conditions=[] bypasses predicate path; emits field error.

        When StepResultRoute(conditions=[]) is constructed directly (bypassing _parse_step,
        which collapses empty conditions to on_result=None), the validator falls through to
        legacy format validation and emits an explicit error for the missing field.
        """
        from autoskillit.recipe.validator import validate_recipe

        recipe = Recipe(
            name="test-predicate-empty",
            description="test",
            steps={
                "start": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "x", "cwd": "y"},
                    on_result=StepResultRoute(conditions=[]),
                ),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        errors = validate_recipe(recipe)
        assert any("on_result.field must be non-empty" in e for e in errors)


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


class TestPredicateSemanticRules:
    """Semantic rules behave correctly for predicate-format on_result."""

    def test_unreachable_step_includes_condition_routes(self) -> None:
        """A step reachable only via condition route is NOT flagged as unreachable."""
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
        findings = run_semantic_rules(wf)
        unreachable = [f for f in findings if f.rule == "unreachable-step"]
        step_names = {f.step_name for f in unreachable}
        assert "assess" not in step_names
        assert "cleanup" not in step_names
        assert "push" not in step_names

    def test_on_result_missing_failure_route_does_not_fire_for_predicate_format(
        self,
    ) -> None:
        """RCA rule does NOT fire for predicate-format on_result (no on_failure needed)."""
        wf = _make_workflow(
            {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_result": [
                        {"when": "result.error", "route": "cleanup"},
                        {"route": "push"},
                    ],
                    "capture": {"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                },
                "cleanup": {"action": "stop", "message": "Cleanup."},
                "push": {"action": "stop", "message": "Push."},
            }
        )
        findings = run_semantic_rules(wf)
        assert not any(f.rule == "on-result-missing-failure-route" for f in findings)

    def test_on_result_missing_failure_route_still_fires_for_legacy_format(
        self,
    ) -> None:
        """RCA1 rule continues to fire for legacy format with no on_failure (no regression)."""
        wf = _make_workflow(
            {
                "audit": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/autoskillit:audit-impl plan.md impl main"},
                    "capture": {"verdict": "${{ result.verdict }}"},
                    "on_result": {"field": "verdict", "routes": {"GO": "done", "NO GO": "fix"}},
                    # no on_failure — the gap
                },
                "fix": {"action": "stop", "message": "Fix."},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        assert any(f.rule == "on-result-missing-failure-route" for f in findings)


class TestRecipeIntegrationPredicateRouting:
    """Integration tests: bundled recipes with predicate on_result validate correctly."""

    def setup_method(self) -> None:
        self.if_recipe = load_recipe(builtin_recipes_dir() / "investigate-first.yaml")
        self.ip_recipe = load_recipe(builtin_recipes_dir() / "implementation-pipeline.yaml")

    def test_investigate_first_merge_step_has_predicate_on_result(self) -> None:
        """The merge step in investigate-first.yaml has predicate on_result."""
        step = self.if_recipe.steps["merge"]
        assert step.on_result is not None
        assert step.on_result.conditions, "merge step must have predicate conditions"
        assert len(step.on_result.conditions) == 3

        cond0 = step.on_result.conditions[0]
        assert cond0.when == "result.failed_step == 'test_gate'"
        assert cond0.route == "assess"

        cond1 = step.on_result.conditions[1]
        assert cond1.when == "result.error"
        assert cond1.route == "cleanup_failure"

        cond2 = step.on_result.conditions[2]
        assert cond2.when is None
        assert cond2.route == "push"

    def test_investigate_first_merge_step_captures_worktree_path(self) -> None:
        """The merge step captures worktree_path from result.worktree_path."""
        step = self.if_recipe.steps["merge"]
        assert "worktree_path" in step.capture
        assert "result.worktree_path" in step.capture["worktree_path"]

    def test_implementation_pipeline_merge_step_has_predicate_on_result(self) -> None:
        """The merge step in implementation-pipeline.yaml has predicate on_result."""
        step = self.ip_recipe.steps["merge"]
        assert step.on_result is not None
        assert step.on_result.conditions, "merge step must have predicate conditions"
        assert len(step.on_result.conditions) == 3

        cond0 = step.on_result.conditions[0]
        assert cond0.when == "result.failed_step == 'test_gate'"
        assert cond0.route == "fix"

        cond1 = step.on_result.conditions[1]
        assert cond1.when == "result.error"
        assert cond1.route == "cleanup_failure"

        cond2 = step.on_result.conditions[2]
        assert cond2.when is None
        assert cond2.route == "next_or_done"

    def test_implementation_pipeline_merge_step_captures_worktree_path(self) -> None:
        """The merge step in implementation-pipeline.yaml captures worktree_path."""
        step = self.ip_recipe.steps["merge"]
        assert "worktree_path" in step.capture
        assert "result.worktree_path" in step.capture["worktree_path"]

    def test_both_recipes_validate_cleanly(self) -> None:
        """Both recipes have no structural errors after predicate routing changes."""
        from autoskillit.recipe.validator import validate_recipe

        if_errors = validate_recipe(self.if_recipe)
        assert if_errors == [], f"investigate-first.yaml has validation errors: {if_errors}"

        ip_errors = validate_recipe(self.ip_recipe)
        assert ip_errors == [], f"implementation-pipeline.yaml has validation errors: {ip_errors}"

    def test_both_recipes_no_error_semantic_findings(self) -> None:
        """Both recipes pass semantic rules with no ERROR-severity findings."""
        for recipe, name in [
            (self.if_recipe, "investigate-first"),
            (self.ip_recipe, "implementation-pipeline"),
        ]:
            findings = run_semantic_rules(recipe)
            errors = [f for f in findings if f.severity == Severity.ERROR]
            assert errors == [], f"{name} has ERROR-severity semantic findings: " + str(
                [(f.rule, f.step_name, f.message) for f in errors]
            )


# ---------------------------------------------------------------------------
# skill-command-missing-prefix rule tests
# ---------------------------------------------------------------------------


class TestSkillCommandMissingPrefixRule:
    """Tests for the skill-command-missing-prefix semantic rule."""

    def test_scp1_prose_run_skill_warns(self) -> None:
        """SCP1: run_skill with prose skill_command → WARNING finding."""
        wf = _make_workflow(
            {
                "step": {
                    "tool": "run_skill",
                    "with": {"skill_command": "Fix the auth bug in main.py", "cwd": "/tmp"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        assert any(
            f.rule == "skill-command-missing-prefix" and f.severity == Severity.WARNING
            for f in findings
        ), "Expected skill-command-missing-prefix WARNING for prose skill_command"

    def test_scp2_prose_run_skill_retry_warns(self) -> None:
        """SCP2: run_skill_retry with prose skill_command → WARNING finding."""
        wf = _make_workflow(
            {
                "step": {
                    "tool": "run_skill_retry",
                    "with": {"skill_command": "Investigate the bug", "cwd": "/tmp"},
                    "on_success": "done",
                    "on_failure": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        assert any(f.rule == "skill-command-missing-prefix" for f in findings)

    def test_scp3_autoskillit_prefix_no_warning(self) -> None:
        """SCP3: /autoskillit:investigate → no skill-command-missing-prefix warning."""
        wf = _make_workflow(
            {
                "step": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/autoskillit:investigate error", "cwd": "/tmp"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        assert not any(f.rule == "skill-command-missing-prefix" for f in findings)

    def test_scp4_bare_slash_local_skill_no_warning(self) -> None:
        """SCP4: /audit-arch (local skill, starts with /) → no warning."""
        wf = _make_workflow(
            {
                "step": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/audit-arch", "cwd": "/tmp"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        assert not any(f.rule == "skill-command-missing-prefix" for f in findings)

    def test_scp5_dynamic_prefix_no_warning(self) -> None:
        """SCP5: /audit-${{ inputs.audit_type }} → no warning (starts with /)."""
        wf = _make_workflow(
            {
                "step": {
                    "tool": "run_skill",
                    "with": {
                        "skill_command": "/audit-${{ inputs.audit_type }}",
                        "cwd": "/tmp",
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        assert not any(f.rule == "skill-command-missing-prefix" for f in findings)

    def test_scp6_non_skill_tool_no_warning(self) -> None:
        """SCP6: run_cmd step (not run_skill) → rule does not fire."""
        wf = _make_workflow(
            {
                "step": {
                    "tool": "run_cmd",
                    "with": {"cmd": "ls -la", "cwd": "/tmp"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        assert not any(f.rule == "skill-command-missing-prefix" for f in findings)


class TestPushMissingExplicitRemoteUrl:
    """push-missing-explicit-remote-url rule fires when push_to_remote lacks remote_url."""

    def test_warns_when_push_to_remote_has_no_remote_url(self) -> None:
        """Rule fires when push_to_remote step has source_dir but no remote_url."""
        recipe = _make_workflow(
            {
                "clone": {
                    "tool": "clone_repo",
                    "with": {"source_dir": "${{ inputs.source_dir }}", "run_name": "test"},
                    "capture": {
                        "work_dir": "${{ result.clone_path }}",
                        "source_dir": "${{ result.source_dir }}",
                    },
                    "on_success": "push",
                },
                "push": {
                    "tool": "push_to_remote",
                    "with": {
                        "clone_path": "${{ context.work_dir }}",
                        "source_dir": "${{ context.source_dir }}",
                        "branch": "main",
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(recipe)
        rule_names = [f.rule for f in findings]
        assert "push-missing-explicit-remote-url" in rule_names

    def test_no_warning_when_explicit_remote_url_provided(self) -> None:
        """Rule is silent when push_to_remote step includes an explicit remote_url."""
        recipe = _make_workflow(
            {
                "clone": {
                    "tool": "clone_repo",
                    "with": {"source_dir": "${{ inputs.source_dir }}", "run_name": "test"},
                    "capture": {
                        "work_dir": "${{ result.clone_path }}",
                        "source_dir": "${{ result.source_dir }}",
                        "remote_url": "${{ result.remote_url }}",
                    },
                    "on_success": "push",
                },
                "push": {
                    "tool": "push_to_remote",
                    "with": {
                        "clone_path": "${{ context.work_dir }}",
                        "remote_url": "${{ context.remote_url }}",
                        "branch": "main",
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(recipe)
        rule_names = [f.rule for f in findings]
        assert "push-missing-explicit-remote-url" not in rule_names

    def test_no_finding_when_no_push_to_remote_step(self) -> None:
        """Rule is silent when recipe has no push_to_remote step."""
        recipe = _make_workflow(
            {
                "start": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = [
            f for f in run_semantic_rules(recipe) if f.rule == "push-missing-explicit-remote-url"
        ]
        assert findings == []


class TestShadowedRequiredInput:
    """Tests for the shadowed-required-input semantic rule."""

    def test_fires_when_required_input_in_context_but_passed_as_prose(self) -> None:
        """Rule fires when plan_path is an ingredient but skill_command passes prose text."""
        recipe = Recipe(
            name="test",
            description="test",
            ingredients={
                "plan_path": RecipeIngredient(description="Plan file path", required=True),
            },
            steps={
                "implement": _parse_step(
                    {
                        "tool": "run_skill",
                        "with": {
                            "skill_command": (
                                "/autoskillit:implement-worktree-no-merge temp/my-plan.md"
                            ),
                            "cwd": "/tmp",
                        },
                        "on_success": "done",
                    }
                ),
                "done": _parse_step({"action": "stop", "message": "Done."}),
            },
            kitchen_rules=["test"],
        )
        findings = run_semantic_rules(recipe)
        assert any(
            f.rule == "shadowed-required-input" and f.step_name == "implement" for f in findings
        ), "Expected shadowed-required-input finding when plan_path is ingredient but prose passed"

    def test_clean_when_template_ref_used(self) -> None:
        """Rule is silent when skill_command uses ${{ context.plan_path }} template reference."""
        recipe = Recipe(
            name="test",
            description="test",
            ingredients={
                "plan_path": RecipeIngredient(description="Plan file path", required=True),
            },
            steps={
                "implement": _parse_step(
                    {
                        "tool": "run_skill",
                        "with": {
                            "skill_command": (
                                "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                            ),
                            "cwd": "/tmp",
                        },
                        "on_success": "done",
                    }
                ),
                "done": _parse_step({"action": "stop", "message": "Done."}),
            },
            kitchen_rules=["test"],
        )
        findings = run_semantic_rules(recipe)
        assert not any(f.rule == "shadowed-required-input" for f in findings), (
            "Expected no shadowed-required-input finding when template ref is used"
        )

    def test_clean_when_input_not_yet_in_context(self) -> None:
        """Rule is silent when plan_path is not an ingredient and not in available context."""
        recipe = Recipe(
            name="test",
            description="test",
            ingredients={},  # plan_path not declared — not yet available
            steps={
                "implement": _parse_step(
                    {
                        "tool": "run_skill",
                        "with": {
                            "skill_command": (
                                "/autoskillit:implement-worktree-no-merge temp/my-plan.md"
                            ),
                            "cwd": "/tmp",
                        },
                        "on_success": "done",
                    }
                ),
                "done": _parse_step({"action": "stop", "message": "Done."}),
            },
            kitchen_rules=["test"],
        )
        findings = run_semantic_rules(recipe)
        assert not any(f.rule == "shadowed-required-input" for f in findings), (
            "Expected no shadowed-required-input finding when input is not available in context"
        )
