"""Tests for semantic validation rules."""

from __future__ import annotations

from autoskillit.semantic_rules import (
    RuleFinding,
    Severity,
    run_semantic_rules,
)
from autoskillit.workflow_loader import (
    Workflow,
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
# T2-T4b: retry-without-worktree-path
# ---------------------------------------------------------------------------


def test_retry_without_worktree_path_triggers():
    """Preceding step captures worktree_path; run_skill_retry step with retry
    does NOT receive it -> error."""
    wf = _make_workflow(
        {
            "implement": {
                "tool": "run_skill",
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "on_success": "retry_step",
            },
            "retry_step": {
                "tool": "run_skill_retry",
                "with": {"skill_command": "/do-stuff"},
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert any(
        f.rule == "retry-without-worktree-path" and f.severity == Severity.ERROR for f in findings
    )


def test_retry_without_worktree_path_clean():
    """Step uses run_skill_retry with retry and receives worktree_path -> no finding."""
    wf = _make_workflow(
        {
            "implement": {
                "tool": "run_skill",
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "on_success": "retry_step",
            },
            "retry_step": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": "/do-stuff",
                    "cwd": "${{ context.worktree_path }}",
                },
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "retry-without-worktree-path" for f in findings)


def test_retry_without_worktree_path_skips_non_retry():
    """Steps without retry block are not flagged."""
    wf = _make_workflow(
        {
            "implement": {
                "tool": "run_skill",
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "on_success": "next_step",
            },
            "next_step": {
                "tool": "run_skill_retry",
                "with": {"skill_command": "/do-stuff"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "retry-without-worktree-path" for f in findings)


def test_retry_without_worktree_path_skips_no_preceding_capture():
    """run_skill_retry with retry but NO preceding step captures worktree_path
    -> no finding. The skill may create its own worktree internally."""
    wf = _make_workflow(
        {
            "retry_step": {
                "tool": "run_skill_retry",
                "with": {"skill_command": "/do-stuff"},
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "retry-without-worktree-path" for f in findings)


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
# T12: Bundled workflows pass semantic rules
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
