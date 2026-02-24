"""Tests for semantic validation rules."""

from __future__ import annotations

from autoskillit.semantic_rules import (
    RuleFinding,
    Severity,
    run_semantic_rules,
)
from autoskillit.workflow_loader import (
    Workflow,
    WorkflowInput,
    _parse_step,
    load_workflow,
)


def _make_workflow(steps: dict[str, dict]) -> Workflow:
    """Build a minimal Workflow from step dicts using _parse_step."""
    parsed_steps = {name: _parse_step(data) for name, data in steps.items()}
    return Workflow(name="test", description="test", steps=parsed_steps, constraints=["test"])


# ---------------------------------------------------------------------------
# T1: Registry collects decorated functions
# ---------------------------------------------------------------------------


def test_registry_collects_rules():
    """run_semantic_rules returns findings from all registered rules."""
    wf = _make_workflow(
        {
            "do_thing": {
                "tool": "run_cmd",
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert isinstance(findings, list)
    assert all(isinstance(f, RuleFinding) for f in findings)


# ---------------------------------------------------------------------------
# T2-T4b: unsatisfied-skill-input (replaces retry-without-worktree-path)
# ---------------------------------------------------------------------------


def test_unsatisfied_input_replaces_worktree_path_check():
    """REGRESSION: Same pipeline that triggered retry-without-worktree-path
    now triggers unsatisfied-skill-input for retry-worktree."""
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
    assert any(
        f.rule == "unsatisfied-skill-input" and "worktree_path" in f.message for f in errors
    )


def test_unsatisfied_input_clean_when_provided():
    """All required inputs are provided -> no finding."""
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
    assert not any(f.rule == "unsatisfied-skill-input" for f in findings)


def test_unsatisfied_input_not_available():
    """Required input never captured by any prior step -> ERROR."""
    wf = _make_workflow(
        {
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
    errors = [
        f for f in findings if f.rule == "unsatisfied-skill-input" and f.severity == Severity.ERROR
    ]
    assert any("worktree_path" in f.message for f in errors)


def test_unsatisfied_input_unknown_skill_ignored():
    """Steps with unrecognized skill names produce no contract findings."""
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
    assert not any(f.rule == "unsatisfied-skill-input" for f in findings)


def test_unsatisfied_input_from_pipeline_inputs():
    """Skill inputs satisfied by pipeline inputs (not just captures) -> no finding."""
    wf = Workflow(
        name="test",
        description="test",
        inputs={
            "plan_path": WorkflowInput(description="Plan file", required=True),
            "worktree_path": WorkflowInput(description="Worktree", required=True),
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
        constraints=["test"],
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "unsatisfied-skill-input" for f in findings)


def test_unsatisfied_input_non_skill_tool_ignored():
    """Non-skill tools (test_check, merge_worktree) are not contract-checked."""
    wf = _make_workflow(
        {
            "test": {
                "tool": "test_check",
                "with": {"worktree_path": "${{ context.worktree_path }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "unsatisfied-skill-input" for f in findings)


def test_unsatisfied_input_inline_positional_args_skipped():
    """Steps with inline positional text (no ${{ }} refs) are skipped to
    avoid false positives on bundled workflows like
    '/autoskillit:investigate the test failures'."""
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
    assert not any(f.rule == "unsatisfied-skill-input" for f in findings)


# ---------------------------------------------------------------------------
# T5-T6: unreachable-step
# ---------------------------------------------------------------------------


def test_unreachable_steps_detects_orphan():
    """Step not referenced by any routing and not the first step -> warning."""
    wf = _make_workflow(
        {
            "start": {
                "tool": "run_cmd",
                "on_success": "done",
            },
            "orphan": {
                "tool": "run_cmd",
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert any(f.rule == "unreachable-step" and "orphan" in f.message for f in findings)


def test_unreachable_steps_first_step_clean():
    """The first step (entry point) is never flagged as unreachable."""
    wf = _make_workflow(
        {
            "start": {
                "tool": "run_cmd",
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "unreachable-step" and "start" in f.step_name for f in findings)


# ---------------------------------------------------------------------------
# T7-T8: model-on-non-skill-step
# ---------------------------------------------------------------------------


def test_model_on_non_skill_triggers():
    """Step with tool=test_check and model=sonnet -> warning."""
    wf = _make_workflow(
        {
            "check": {
                "tool": "test_check",
                "model": "sonnet",
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert any(f.rule == "model-on-non-skill-step" for f in findings)


def test_model_on_non_skill_clean():
    """Step with tool=run_skill and model=sonnet -> no finding."""
    wf = _make_workflow(
        {
            "do": {
                "tool": "run_skill",
                "model": "sonnet",
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "model-on-non-skill-step" for f in findings)


# ---------------------------------------------------------------------------
# T9-T10b: retry-without-capture
# ---------------------------------------------------------------------------


def test_retry_without_capture_triggers():
    """run_skill_retry with retry, no capture, and downstream context ref -> warning."""
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


def test_retry_without_capture_clean_no_downstream():
    """run_skill_retry with retry, no capture, no downstream context ref -> no finding."""
    wf = _make_workflow(
        {
            "impl": {
                "tool": "run_skill_retry",
                "with": {"skill_command": "/implement"},
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "retry-without-capture" for f in findings)


def test_retry_without_capture_clean_with_capture():
    """run_skill_retry with retry AND capture -> no finding."""
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


# ---------------------------------------------------------------------------
# T11: RuleFinding serialization
# ---------------------------------------------------------------------------


def test_rule_finding_to_dict():
    """RuleFinding.to_dict() produces the expected JSON-friendly dict."""
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


# ---------------------------------------------------------------------------
# T12: Old rule removed
# ---------------------------------------------------------------------------


def test_old_rule_removed():
    """The retry-without-worktree-path rule no longer exists in the registry."""
    from autoskillit.semantic_rules import _RULE_REGISTRY

    assert not any(r.name == "retry-without-worktree-path" for r in _RULE_REGISTRY)


# ---------------------------------------------------------------------------
# T13: Bundled workflows pass semantic rules
# ---------------------------------------------------------------------------


def test_bundled_workflows_pass_semantic_rules():
    """All bundled workflow YAML files produce no error-severity findings."""
    from autoskillit.workflow_loader import builtin_workflows_dir

    wf_dir = builtin_workflows_dir()
    yaml_files = list(wf_dir.glob("*.yaml"))
    assert yaml_files, "Expected at least one bundled workflow"

    for path in yaml_files:
        wf = load_workflow(path)
        findings = run_semantic_rules(wf)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert not errors, (
            f"Bundled workflow {path.name} has error-severity semantic findings: {errors}"
        )
