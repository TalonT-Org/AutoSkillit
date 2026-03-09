"""Tests for structural semantic rules (step reachability, field validation, recipe version)."""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import (
    _parse_step,
    builtin_recipes_dir,
    load_recipe,
)
from autoskillit.recipe.schema import (
    Recipe,
    RecipeIngredient,
    RecipeStep,
    StepResultRoute,
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
                "retries": 0,
                "on_context_limit": "retry_step",
                "on_success": "retry_step",
            },
            "retry_step": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:retry-worktree ${{ context.plan_path }}",
                },
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
                "retries": 0,
                "on_context_limit": "retry_step",
                "on_success": "retry_step",
            },
            "retry_step": {
                "tool": "run_skill",
                "with": {
                    "skill_command": (
                        "/autoskillit:retry-worktree "
                        "${{ context.plan_path }} ${{ context.worktree_path }}"
                    ),
                },
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
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:retry-worktree ${{ context.plan_path }}"},
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
                    "tool": "run_skill",
                    "with": {
                        "skill_command": (
                            "/autoskillit:retry-worktree "
                            "${{ inputs.plan_path }} ${{ inputs.worktree_path }}"
                        ),
                    },
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
    @pytest.mark.parametrize(
        "script_ver,installed_ver,expected_count",
        [
            ("0.1.0", "0.2.0", 1),  # MSR1: below installed → fires
            ("0.2.0", "0.2.0", 0),  # MSR2: matches → does not fire
            (None, "0.2.0", 1),  # MSR3: None → fires
            ("0.1.0", "0.2.0", 1),  # MSR4: also fires (same as MSR1; severity checked separately)
        ],
    )
    def test_outdated_recipe_version_rule(
        self, monkeypatch: pytest.MonkeyPatch, script_ver, installed_ver, expected_count
    ) -> None:
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", installed_ver)
        wf = _make_workflow(
            {
                "do_thing": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        wf.version = script_ver
        findings = [f for f in run_semantic_rules(wf) if f.rule == "outdated-recipe-version"]
        assert len(findings) == expected_count

    def test_outdated_recipe_version_rule_severity_is_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "0.2.0")
        wf = _make_workflow(
            {
                "do_thing": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        wf.version = "0.1.0"
        findings = [f for f in run_semantic_rules(wf) if f.rule == "outdated-recipe-version"]
        assert findings[0].severity == Severity.WARNING


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
                    tool="run_skill",
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
                    tool="run_skill",
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
                tool="run_skill",
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
                tool="run_skill",
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
# TestOnContextLimitField
# ---------------------------------------------------------------------------


class TestOnContextLimitField:
    """Tests for on_context_limit as a routing field and cycle detection."""

    def test_on_context_limit_invalid_target_raises_validation_error(self) -> None:
        """on_context_limit must reference a declared step name."""
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
                    on_context_limit="nonexistent_step",
                    with_args={"skill_command": "x", "cwd": "/tmp"},
                ),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        errors = validate_recipe(recipe)
        assert errors, "Expected validation errors for unknown on_context_limit target"
        assert any("on_context_limit" in e for e in errors)

    def test_on_context_limit_valid_target_passes_validation(self) -> None:
        """on_context_limit referencing a valid step passes validation."""
        from autoskillit.recipe.validator import validate_recipe

        recipe = Recipe(
            name="test",
            description="test",
            summary="test",
            ingredients={},
            kitchen_rules=["test"],
            steps={
                "implement": RecipeStep(
                    tool="run_skill",
                    on_success="done",
                    on_failure="cleanup",
                    on_context_limit="retry_worktree",
                    retries=0,
                    with_args={"skill_command": "/autoskillit:implement-worktree-no-merge x"},
                ),
                "retry_worktree": RecipeStep(
                    tool="run_skill",
                    on_success="done",
                    on_failure="cleanup",
                    with_args={"skill_command": "/autoskillit:retry-worktree x y"},
                ),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        errors = validate_recipe(recipe)
        assert not errors, f"Expected no errors but got: {errors}"

    def test_on_exhausted_invalid_target_raises_validation_error(self) -> None:
        """on_exhausted must reference a declared step name or be a reserved terminal."""
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
                    on_exhausted="nonexistent_step",
                    with_args={"skill_command": "x", "cwd": "/tmp"},
                ),
                "cleanup": RecipeStep(action="stop", message="done"),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        errors = validate_recipe(recipe)
        assert errors, "Expected validation errors for unknown on_exhausted target"

    def test_on_exhausted_escalate_reserved_passes_validation(self) -> None:
        """on_exhausted: 'escalate' is reserved — passes validation without an escalate step."""
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
                    on_failure="done",
                    on_exhausted="escalate",
                    with_args={"skill_command": "/autoskillit:investigate x"},
                ),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        errors = validate_recipe(recipe)
        assert not errors, f"Expected no errors but got: {errors}"

    def test_unbounded_cycle_without_retries_produces_warning(self) -> None:
        """verify → assess → verify cycle with retries=0 must produce a warning."""
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
                    retries=0,
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

    def test_bounded_cycle_with_retries_does_not_warn(self) -> None:
        """A cycle with retries > 0 on the cycling step should NOT warn."""
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
                    retries=3,
                    on_exhausted="cleanup",
                    with_args={"skill_command": "x", "cwd": "/tmp"},
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
                    retries=0,
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
# TestSkillCommandMissingPrefixRule
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

    def test_scp2_prose_run_skill_warns(self) -> None:
        """SCP2: run_skill with prose skill_command → WARNING finding."""
        wf = _make_workflow(
            {
                "step": {
                    "tool": "run_skill",
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


# ---------------------------------------------------------------------------
# TestShadowedRequiredInput
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# TestMergeBaseUnpublishedRule
# ---------------------------------------------------------------------------


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
        """implementation.yaml must pass the merge-base-unpublished
        rule after the push_merge_target step is added."""
        recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
        findings = run_semantic_rules(recipe)
        assert not any(f.rule == "merge-base-unpublished" for f in findings)


# ---------------------------------------------------------------------------
# TestPredicateOnResultValidation
# ---------------------------------------------------------------------------


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

    def test_predicate_format_with_on_failure_allowed(self) -> None:
        """validator.py must not reject on_failure alongside on_result.conditions."""
        from autoskillit.recipe.validator import validate_recipe

        wf = self._make_merge_recipe(
            {
                "tool": "merge_worktree",
                "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                "on_result": [
                    {"when": "result.error", "route": "cleanup_failure"},
                    {"route": "push"},
                ],
                "on_failure": "cleanup_failure",
            }
        )
        errors = validate_recipe(wf)
        assert not any("mutually exclusive" in e for e in errors), errors

    def test_on_result_missing_failure_route_fires_for_predicate_format(self) -> None:
        """Predicate-format steps with no on_failure must trigger ERROR finding."""
        wf = self._make_merge_recipe(
            {
                "tool": "merge_worktree",
                "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                "on_result": [
                    {"when": "result.error", "route": "cleanup_failure"},
                    {"route": "push"},
                ],
                # no on_failure — should trigger finding
            }
        )
        findings = run_semantic_rules(wf)
        names = [f.rule for f in findings]
        assert "on-result-missing-failure-route" in names

    def test_on_result_missing_failure_route_clear_when_predicate_has_on_failure(self) -> None:
        """Predicate-format step with on_failure must not trigger the rule."""
        wf = self._make_merge_recipe(
            {
                "tool": "merge_worktree",
                "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                "on_result": [
                    {"when": "result.error", "route": "cleanup_failure"},
                    {"route": "push"},
                ],
                "on_failure": "cleanup_failure",
            }
        )
        findings = run_semantic_rules(wf)
        names = [f.rule for f in findings]
        assert "on-result-missing-failure-route" not in names


# ---------------------------------------------------------------------------
# TestPredicateBuildStepGraph
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
# TestPredicateSemanticRules
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# TestMergeRoutingIncompleteRule
# ---------------------------------------------------------------------------


class TestMergeRoutingIncompleteRule:
    """Tests for the merge-routing-incomplete semantic rule (RMR*)."""

    def _make_merge_step(self, conditions: list[dict]) -> Recipe:
        """Build a minimal recipe with a merge_worktree step using predicate on_result."""
        return _make_workflow(
            {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_result": conditions,
                    "capture": {"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                },
                "recover": {"action": "stop", "message": "Recover."},
                "done": {"action": "stop", "message": "Done."},
                "escalate": {"action": "stop", "message": "Escalate."},
            }
        )

    def test_rmr1_fires_when_test_gate_missing(self):
        """RMR1: ERROR when test_gate is not explicitly routed."""
        recipe = self._make_merge_step(
            [
                {"when": "result.failed_step == 'dirty_tree'", "route": "recover"},
                {"when": "result.failed_step == 'post_rebase_test_gate'", "route": "recover"},
                {"when": "result.failed_step == 'rebase'", "route": "recover"},
                {"when": "result.error", "route": "escalate"},
                {"route": "done"},
            ]
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-routing-incomplete"]
        assert len(errors) == 1
        assert "test_gate" in errors[0].message

    def test_rmr2_fires_when_post_rebase_test_gate_missing(self):
        """RMR2: ERROR when post_rebase_test_gate is not explicitly routed."""
        recipe = self._make_merge_step(
            [
                {"when": "result.failed_step == 'dirty_tree'", "route": "recover"},
                {"when": "result.failed_step == 'test_gate'", "route": "recover"},
                {"when": "result.failed_step == 'rebase'", "route": "recover"},
                {"when": "result.error", "route": "escalate"},
                {"route": "done"},
            ]
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-routing-incomplete"]
        assert len(errors) == 1
        assert "post_rebase_test_gate" in errors[0].message

    def test_rmr3_fires_when_rebase_missing(self):
        """RMR3: ERROR when rebase is not explicitly routed."""
        recipe = self._make_merge_step(
            [
                {"when": "result.failed_step == 'dirty_tree'", "route": "recover"},
                {"when": "result.failed_step == 'test_gate'", "route": "recover"},
                {"when": "result.failed_step == 'post_rebase_test_gate'", "route": "recover"},
                {"when": "result.error", "route": "escalate"},
                {"route": "done"},
            ]
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-routing-incomplete"]
        assert len(errors) == 1
        assert "rebase" in errors[0].message

    def test_rmr4_clears_when_all_four_covered(self):
        """RMR4: No finding when all recoverable values are explicitly routed."""
        recipe = self._make_merge_step(
            [
                {"when": "result.failed_step == 'dirty_tree'", "route": "recover"},
                {"when": "result.failed_step == 'test_gate'", "route": "recover"},
                {"when": "result.failed_step == 'post_rebase_test_gate'", "route": "recover"},
                {"when": "result.failed_step == 'rebase'", "route": "recover"},
                {"when": "result.error", "route": "escalate"},
                {"route": "done"},
            ]
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-routing-incomplete"]
        assert errors == []

    def test_rmr7_fires_when_dirty_tree_missing(self):
        """RMR7: ERROR when dirty_tree is not explicitly routed."""
        recipe = self._make_merge_step(
            [
                {"when": "result.failed_step == 'test_gate'", "route": "recover"},
                {"when": "result.failed_step == 'post_rebase_test_gate'", "route": "recover"},
                {"when": "result.failed_step == 'rebase'", "route": "recover"},
                {"when": "result.error", "route": "escalate"},
                {"route": "done"},
            ]
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-routing-incomplete"]
        assert len(errors) == 1
        assert "dirty_tree" in errors[0].message

    def test_rmr5_does_not_fire_for_non_merge_worktree_step(self):
        """RMR5: Rule is scoped to merge_worktree steps only."""
        recipe = _make_workflow(
            {
                "run": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/autoskillit:implement-worktree", "cwd": "/tmp"},
                    "on_result": [
                        {"when": "result.error", "route": "done"},
                        {"route": "done"},
                    ],
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-routing-incomplete"]
        assert errors == []

    def test_rmr6_does_not_fire_when_no_on_result(self):
        """RMR6: Rule is silent for merge_worktree steps without on_result."""
        recipe = _make_workflow(
            {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_success": "done",
                    "on_failure": "escalate",
                },
                "done": {"action": "stop", "message": "Done."},
                "escalate": {"action": "stop", "message": "Escalate."},
            }
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-routing-incomplete"]
        assert errors == []


# ---------------------------------------------------------------------------
# TestRecipeIntegrationPredicateRouting
# ---------------------------------------------------------------------------


class TestRecipeIntegrationPredicateRouting:
    """Integration tests: bundled recipes with predicate on_result validate correctly."""

    def setup_method(self) -> None:
        self.if_recipe = load_recipe(builtin_recipes_dir() / "remediation.yaml")
        self.ip_recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")

    def test_investigate_first_merge_step_has_predicate_on_result(self) -> None:
        """The merge step in remediation.yaml has predicate on_result."""
        step = self.if_recipe.steps["merge"]
        assert step.on_result is not None
        assert step.on_result.conditions, "merge step must have predicate conditions"
        assert len(step.on_result.conditions) == 6

        cond0 = step.on_result.conditions[0]
        assert cond0.when == "result.failed_step == 'dirty_tree'"
        assert cond0.route == "assess"

        cond1 = step.on_result.conditions[1]
        assert cond1.when == "result.failed_step == 'test_gate'"
        assert cond1.route == "assess"

        cond2 = step.on_result.conditions[2]
        assert cond2.when == "result.failed_step == 'post_rebase_test_gate'"
        assert cond2.route == "assess"

        cond3 = step.on_result.conditions[3]
        assert cond3.when == "result.failed_step == 'rebase'"
        assert cond3.route == "assess"

        cond4 = step.on_result.conditions[4]
        assert cond4.when == "result.error"
        assert cond4.route == "release_issue_failure"

        cond5 = step.on_result.conditions[5]
        assert cond5.when is None
        assert cond5.route == "push"

    def test_investigate_first_merge_step_captures_worktree_path(self) -> None:
        """The merge step captures worktree_path from result.worktree_path."""
        step = self.if_recipe.steps["merge"]
        assert "worktree_path" in step.capture
        assert "result.worktree_path" in step.capture["worktree_path"]

    def test_implementation_pipeline_merge_step_has_predicate_on_result(self) -> None:
        """The merge step in implementation.yaml has predicate on_result."""
        step = self.ip_recipe.steps["merge"]
        assert step.on_result is not None
        assert step.on_result.conditions, "merge step must have predicate conditions"
        assert len(step.on_result.conditions) == 6

        cond0 = step.on_result.conditions[0]
        assert cond0.when == "result.failed_step == 'dirty_tree'"
        assert cond0.route == "fix"

        cond1 = step.on_result.conditions[1]
        assert cond1.when == "result.failed_step == 'test_gate'"
        assert cond1.route == "fix"

        cond2 = step.on_result.conditions[2]
        assert cond2.when == "result.failed_step == 'post_rebase_test_gate'"
        assert cond2.route == "fix"

        cond3 = step.on_result.conditions[3]
        assert cond3.when == "result.failed_step == 'rebase'"
        assert cond3.route == "fix"

        cond4 = step.on_result.conditions[4]
        assert cond4.when == "result.error"
        assert cond4.route == "release_issue_failure"

        cond5 = step.on_result.conditions[5]
        assert cond5.when is None
        assert cond5.route == "next_or_done"

    def test_implementation_pipeline_merge_step_captures_worktree_path(self) -> None:
        """The merge step in implementation.yaml captures worktree_path."""
        step = self.ip_recipe.steps["merge"]
        assert "worktree_path" in step.capture
        assert "result.worktree_path" in step.capture["worktree_path"]

    def test_both_recipes_validate_cleanly(self) -> None:
        """Both recipes have no structural errors after predicate routing changes."""
        from autoskillit.recipe.validator import validate_recipe

        if_errors = validate_recipe(self.if_recipe)
        assert if_errors == [], f"remediation.yaml has validation errors: {if_errors}"

        ip_errors = validate_recipe(self.ip_recipe)
        assert ip_errors == [], f"implementation.yaml has validation errors: {ip_errors}"

    def test_both_recipes_no_error_semantic_findings(self) -> None:
        """Both recipes pass semantic rules with no ERROR-severity findings."""
        for recipe, name in [
            (self.if_recipe, "remediation"),
            (self.ip_recipe, "implementation"),
        ]:
            findings = run_semantic_rules(recipe)
            errors = [f for f in findings if f.severity == Severity.ERROR]
            assert errors == [], f"{name} has ERROR-severity semantic findings: " + str(
                [(f.rule, f.step_name, f.message) for f in errors]
            )

    def test_bugfix_loop_merge_step_has_complete_predicate_routing(self) -> None:
        """The merge step in bugfix-loop.yaml has complete predicate on_result routing."""
        bf_recipe = load_recipe(builtin_recipes_dir() / "bugfix-loop.yaml")
        step = bf_recipe.steps["merge"]
        assert step.on_result is not None
        assert step.on_result.conditions, "merge step must have predicate conditions"
        assert len(step.on_result.conditions) == 6

        cond0 = step.on_result.conditions[0]
        assert cond0.when == "result.failed_step == 'dirty_tree'"
        assert cond0.route == "assess"

        cond1 = step.on_result.conditions[1]
        assert cond1.when == "result.failed_step == 'test_gate'"
        assert cond1.route == "assess"

        cond2 = step.on_result.conditions[2]
        assert cond2.when == "result.failed_step == 'post_rebase_test_gate'"
        assert cond2.route == "assess"

        cond3 = step.on_result.conditions[3]
        assert cond3.when == "result.failed_step == 'rebase'"
        assert cond3.route == "assess"

        cond4 = step.on_result.conditions[4]
        assert cond4.when == "result.error"
        assert cond4.route == "escalate"

        cond5 = step.on_result.conditions[5]
        assert cond5.when is None
        assert cond4.route == "done"
