"""Tests for CI semantic rules: ci-polling-inline-shell and ci-failure-missing-conflict-gate."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe.io import _parse_step, builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    """Minimal recipe factory for CI rule tests."""
    return Recipe(
        name="test-ci-rule",
        description="Test recipe for ci-polling-inline-shell rule.",
        version="0.2.0",
        kitchen_rules="Use wait_for_ci.",
        steps=steps,
    )


def _make_workflow(steps: dict[str, dict]) -> Recipe:
    """Helper that accepts YAML-style step dicts and constructs a Recipe."""
    parsed_steps = {name: _parse_step(data) for name, data in steps.items()}
    return Recipe(
        name="test-ci-conflict-gate",
        description="Test recipe for ci-failure-missing-conflict-gate rule.",
        version="0.2.0",
        kitchen_rules="Use conflict gates.",
        steps=parsed_steps,
    )


def test_inline_ci_polling_detected() -> None:
    """run_cmd step with gh run list/watch triggers ci-polling-inline-shell WARNING."""
    steps = {
        "ci_watch": RecipeStep(
            tool="run_cmd",
            with_args={
                "cmd": (
                    "run_id=$(gh run list --branch main --limit 1 "
                    '--json databaseId,status --jq ".[]" | head -1)\n'
                    'gh run watch "$run_id" --exit-status'
                ),
                "cwd": "/tmp",
            },
        ),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    ci_findings = [f for f in findings if f.rule == "ci-polling-inline-shell"]
    assert len(ci_findings) == 1
    assert ci_findings[0].severity == Severity.WARNING
    assert ci_findings[0].step_name == "ci_watch"
    assert "wait_for_ci" in ci_findings[0].message


def test_wait_for_ci_tool_not_flagged() -> None:
    """Steps using tool: wait_for_ci must not trigger ci-polling-inline-shell."""
    steps = {
        "ci_watch": RecipeStep(
            tool="wait_for_ci",
            with_args={"branch": "main", "timeout_seconds": 300},
        ),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    ci_findings = [f for f in findings if f.rule == "ci-polling-inline-shell"]
    assert len(ci_findings) == 0


def test_run_cmd_without_gh_not_flagged() -> None:
    """run_cmd steps without gh run commands must not trigger the rule."""
    steps = {
        "echo_step": RecipeStep(
            tool="run_cmd",
            with_args={"cmd": "echo hello"},
        ),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    ci_findings = [f for f in findings if f.rule == "ci-polling-inline-shell"]
    assert len(ci_findings) == 0


def test_bundled_recipes_no_inline_ci_polling() -> None:
    """All bundled recipes must be free of ci-polling-inline-shell findings."""
    for yaml_path in sorted(builtin_recipes_dir().glob("*.yaml")):
        recipe = load_recipe(yaml_path)
        findings = run_semantic_rules(recipe)
        ci_findings = [f for f in findings if f.rule == "ci-polling-inline-shell"]
        assert len(ci_findings) == 0, (
            f"Recipe '{yaml_path.stem}' has inline CI polling: "
            + ", ".join(f.message for f in ci_findings)
        )


# ---------------------------------------------------------------------------
# ci-failure-missing-conflict-gate rule tests
# ---------------------------------------------------------------------------


def test_ci_failure_missing_conflict_gate_fires_on_direct_resolve() -> None:
    """wait_for_ci → resolve_ci (resolve-failures) with no gate → ERROR."""
    wf = _make_workflow(
        {
            "ci_watch": {"tool": "wait_for_ci", "on_success": "done", "on_failure": "resolve_ci"},
            "resolve_ci": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:resolve-failures work_dir plan_path main"},
                "on_success": "done",
                "on_failure": "done",
            },
            "done": {"action": "stop", "message": "done"},
        }
    )
    findings = run_semantic_rules(wf)
    names = [f.rule for f in findings]
    assert "ci-failure-missing-conflict-gate" in names


def test_ci_failure_missing_gate_fires_through_diagnose_ci() -> None:
    """wait_for_ci → diagnose_ci → resolve_ci with no gate → ERROR."""
    wf = _make_workflow(
        {
            "ci_watch": {"tool": "wait_for_ci", "on_success": "done", "on_failure": "diagnose_ci"},
            "diagnose_ci": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:diagnose-ci branch - - tests.yml"},
                "on_success": "resolve_ci",
                "on_failure": "resolve_ci",
            },
            "resolve_ci": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:resolve-failures work_dir plan main"},
                "on_success": "done",
                "on_failure": "done",
            },
            "done": {"action": "stop", "message": "done"},
        }
    )
    findings = run_semantic_rules(wf)
    names = [f.rule for f in findings]
    assert "ci-failure-missing-conflict-gate" in names


def test_ci_failure_conflict_gate_passes_with_merge_base_cmd() -> None:
    """wait_for_ci → detect_conflict(run_cmd merge-base) → resolve-failures → no error."""
    wf = _make_workflow(
        {
            "ci_watch": {
                "tool": "wait_for_ci",
                "on_success": "done",
                "on_failure": "detect_conflict",
            },
            "detect_conflict": {
                "tool": "run_cmd",
                "with": {
                    "cmd": (
                        "git fetch origin main && ! git merge-base --is-ancestor origin/main HEAD"
                    )
                },
                "on_success": "done",
                "on_failure": "resolve_ci",
            },
            "resolve_ci": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:resolve-failures work plan main"},
                "on_success": "done",
                "on_failure": "done",
            },
            "done": {"action": "stop", "message": "done"},
        }
    )
    findings = run_semantic_rules(wf)
    names = [f.rule for f in findings]
    assert "ci-failure-missing-conflict-gate" not in names


def test_ci_failure_conflict_gate_passes_with_resolve_merge_conflicts() -> None:
    """wait_for_ci → resolve-merge-conflicts gate → no error."""
    wf = _make_workflow(
        {
            "ci_watch": {
                "tool": "wait_for_ci",
                "on_success": "done",
                "on_failure": "ci_conflict_fix",
            },
            "ci_conflict_fix": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:resolve-merge-conflicts work plan main"},
                "on_success": "resolve_ci",
                "on_failure": "done",
            },
            "resolve_ci": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:resolve-failures work plan main"},
                "on_success": "done",
                "on_failure": "done",
            },
            "done": {"action": "stop", "message": "done"},
        }
    )
    findings = run_semantic_rules(wf)
    names = [f.rule for f in findings]
    assert "ci-failure-missing-conflict-gate" not in names


def test_ci_failure_no_resolve_failures_skips_rule() -> None:
    """wait_for_ci → diagnose_ci → cleanup (no resolve-failures) → no error."""
    wf = _make_workflow(
        {
            "ci_watch": {"tool": "wait_for_ci", "on_success": "done", "on_failure": "diagnose_ci"},
            "diagnose_ci": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:diagnose-ci branch - - tests.yml"},
                "on_success": "done",
                "on_failure": "done",
            },
            "done": {"action": "stop", "message": "done"},
        }
    )
    findings = run_semantic_rules(wf)
    names = [f.rule for f in findings]
    assert "ci-failure-missing-conflict-gate" not in names


def test_ci_failure_no_on_failure_skips_rule() -> None:
    """wait_for_ci with no on_failure routing → no error."""
    wf = _make_workflow(
        {
            "ci_watch": {"tool": "wait_for_ci", "on_success": "done"},
            "done": {"action": "stop", "message": "done"},
        }
    )
    findings = run_semantic_rules(wf)
    names = [f.rule for f in findings]
    assert "ci-failure-missing-conflict-gate" not in names
