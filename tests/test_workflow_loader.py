"""Tests for workflow YAML loading and validation."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from autoskillit.types import RETRY_RESPONSE_FIELDS, WorkflowSource
from autoskillit.workflow_loader import (
    DataFlowReport,
    StepResultRoute,
    Workflow,
    WorkflowStep,
    _build_step_graph,
    _parse_step,
    _parse_workflow,
    analyze_dataflow,
    builtin_workflows_dir,
    list_workflows,
    load_workflow,
    validate_workflow,
)

VALID_WORKFLOW = {
    "name": "test-workflow",
    "description": "A test workflow",
    "inputs": {
        "test_dir": {"description": "Dir to test", "required": True},
        "branch": {"description": "Branch", "default": "main"},
    },
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


class TestWorkflowLoader:
    # WF1
    def test_load_valid_workflow(self, tmp_path: Path) -> None:
        f = _write_yaml(tmp_path / "wf.yaml", VALID_WORKFLOW)
        wf = load_workflow(f)
        assert wf.name == "test-workflow"
        assert wf.description == "A test workflow"
        assert "test_dir" in wf.inputs
        assert wf.inputs["test_dir"].required is True
        assert wf.inputs["branch"].default == "main"
        assert "run_tests" in wf.steps
        assert wf.steps["run_tests"].tool == "test_check"
        assert wf.steps["run_tests"].with_args["worktree_path"] == "${{ inputs.test_dir }}"
        assert wf.steps["done"].action == "stop"

    # WF2
    def test_workflow_requires_name(self, tmp_path: Path) -> None:
        data = {**VALID_WORKFLOW, "name": ""}
        f = _write_yaml(tmp_path / "wf.yaml", data)
        wf = load_workflow(f)
        errors = validate_workflow(wf)
        assert any("name" in e.lower() for e in errors)

    # WF3
    def test_workflow_requires_steps(self, tmp_path: Path) -> None:
        data = {"name": "no-steps", "description": "Missing steps"}
        f = _write_yaml(tmp_path / "wf.yaml", data)
        wf = load_workflow(f)
        errors = validate_workflow(wf)
        assert any("step" in e.lower() for e in errors)

    # WF4
    def test_input_defaults_applied(self, tmp_path: Path) -> None:
        f = _write_yaml(tmp_path / "wf.yaml", VALID_WORKFLOW)
        wf = load_workflow(f)
        assert wf.inputs["branch"].default == "main"
        assert wf.inputs["branch"].required is False

    # WF5
    def test_goto_targets_validated(self, tmp_path: Path) -> None:
        data = {
            "name": "bad-goto",
            "description": "Invalid goto",
            "steps": {
                "start": {
                    "tool": "run_cmd",
                    "on_success": "nonexistent",
                },
                "end": {"action": "stop", "message": "Done."},
            },
        }
        f = _write_yaml(tmp_path / "wf.yaml", data)
        wf = load_workflow(f)
        errors = validate_workflow(wf)
        assert any("nonexistent" in e for e in errors)

    # WF6
    def test_builtin_workflows_valid(self) -> None:
        bd = builtin_workflows_dir()
        yamls = list(bd.glob("*.yaml"))
        assert len(yamls) >= 4
        for f in yamls:
            wf = load_workflow(f)
            errors = validate_workflow(wf)
            assert errors == [], f"Validation errors in {f.name}: {errors}"

    # WF7
    def test_list_workflows_finds_builtins(self, tmp_path: Path) -> None:
        workflows = list_workflows(tmp_path).items
        names = {w.name for w in workflows}
        assert "bugfix-loop" in names
        assert "implementation" in names
        assert "audit-and-fix" in names
        assert "investigate-first" in names

    # WF8
    def test_project_workflow_overrides_builtin(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / ".autoskillit" / "workflows"
        wf_dir.mkdir(parents=True)
        override = {**VALID_WORKFLOW, "name": "bugfix-loop", "description": "Custom override"}
        _write_yaml(wf_dir / "bugfix-loop.yaml", override)

        workflows = list_workflows(tmp_path).items
        match = next(w for w in workflows if w.name == "bugfix-loop")
        assert match.source == WorkflowSource.PROJECT
        assert match.description == "Custom override"

    # WF9
    def test_step_with_retry_parsed(self, tmp_path: Path) -> None:
        data = {
            "name": "retry-wf",
            "description": "Has retry",
            "steps": {
                "impl": {
                    "tool": "run_skill_retry",
                    "retry": {"max_attempts": 5, "on": "needs_retry", "on_exhausted": "fail"},
                },
                "fail": {"action": "stop", "message": "Failed."},
            },
        }
        f = _write_yaml(tmp_path / "wf.yaml", data)
        wf = load_workflow(f)
        assert wf.steps["impl"].retry is not None
        assert wf.steps["impl"].retry.max_attempts == 5
        assert wf.steps["impl"].retry.on == "needs_retry"
        assert wf.steps["impl"].retry.on_exhausted == "fail"

    # WF10
    def test_terminal_step_has_message(self, tmp_path: Path) -> None:
        data = {
            "name": "no-msg",
            "description": "Terminal without message",
            "steps": {
                "end": {"action": "stop"},
            },
        }
        f = _write_yaml(tmp_path / "wf.yaml", data)
        wf = load_workflow(f)
        errors = validate_workflow(wf)
        assert any("message" in e.lower() for e in errors)

    def test_step_needs_tool_or_action(self, tmp_path: Path) -> None:
        data = {
            "name": "bad-step",
            "description": "Neither tool nor action",
            "steps": {"empty": {"note": "just a note"}},
        }
        f = _write_yaml(tmp_path / "wf.yaml", data)
        wf = load_workflow(f)
        errors = validate_workflow(wf)
        assert any("tool" in e and "action" in e for e in errors)

    def test_input_reference_validation(self, tmp_path: Path) -> None:
        data = {
            "name": "bad-ref",
            "description": "References undeclared input",
            "steps": {
                "run": {
                    "tool": "run_cmd",
                    "with": {"cmd": "${{ inputs.missing_input }}"},
                },
            },
        }
        f = _write_yaml(tmp_path / "wf.yaml", data)
        wf = load_workflow(f)
        errors = validate_workflow(wf)
        assert any("missing_input" in e for e in errors)

    def test_load_workflow_rejects_non_dict(self, tmp_path: Path) -> None:
        """YAML that parses to a non-dict must raise ValueError."""
        path = tmp_path / "list.yaml"
        path.write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="YAML mapping"):
            load_workflow(path)

    def test_list_workflows_reports_malformed_files(self, tmp_path: Path) -> None:
        """Malformed workflow files must produce error reports."""
        wf_dir = tmp_path / ".autoskillit" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "broken.yaml").write_text("{invalid: [unclosed\n")
        result = list_workflows(tmp_path)
        assert len(result.errors) >= 1

    # WF_SUM1
    def test_workflow_summary_defaults_to_empty(self) -> None:
        """Workflow dataclass has summary field defaulting to empty string."""
        wf = Workflow(name="test", description="desc")
        assert wf.summary == ""

    # WF_SUM2
    def test_parse_workflow_extracts_summary(self, tmp_path: Path) -> None:
        """_parse_workflow extracts summary from YAML data."""
        data = {**VALID_WORKFLOW, "summary": "run tests then merge"}
        f = _write_yaml(tmp_path / "wf.yaml", data)
        wf = load_workflow(f)
        assert wf.summary == "run tests then merge"

    # WF_SUM3
    def test_builtin_workflows_summary_is_str(self) -> None:
        """All builtin workflows have summary as str (empty string when absent)."""
        bd = builtin_workflows_dir()
        for f in bd.glob("*.yaml"):
            wf = load_workflow(f)
            assert isinstance(wf.summary, str), f"{f.name}: summary is not str"

    def test_retry_on_field_is_valid_response_key(self, tmp_path: Path) -> None:
        """retry.on must reference a field that run_skill_retry actually returns."""
        for wf_info in list_workflows(tmp_path).items:
            wf = load_workflow(wf_info.path)
            for step_name, step in wf.steps.items():
                if step.retry and step.retry.on:
                    assert step.retry.on in RETRY_RESPONSE_FIELDS, (
                        f"Workflow '{wf.name}' step '{step_name}' retry.on='{step.retry.on}' "
                        f"is not a known response field: {RETRY_RESPONSE_FIELDS}"
                    )

    def test_retry_on_unknown_field_fails_validation(self, tmp_path: Path) -> None:
        """validate_workflow rejects retry.on that references unknown response field."""
        data = {
            "name": "bad-retry-on",
            "description": "Unknown retry.on field",
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
        f = _write_yaml(tmp_path / "wf.yaml", data)
        wf = load_workflow(f)
        errors = validate_workflow(wf)
        assert any("nonexistent_field" in e for e in errors)

    def test_python_step_parsed(self, tmp_path: Path) -> None:
        """WorkflowStep.python is populated from YAML data."""
        data = {
            "name": "py-wf",
            "description": "Has python step",
            "steps": {
                "check": {
                    "python": "mymod.check_fn",
                    "on_success": "done",
                    "on_failure": "fail",
                },
                "done": {"action": "stop", "message": "OK"},
                "fail": {"action": "stop", "message": "Failed"},
            },
        }
        wf = load_workflow(_write_yaml(tmp_path / "wf.yaml", data))
        assert wf.steps["check"].python == "mymod.check_fn"
        assert wf.steps["check"].tool is None
        assert wf.steps["check"].action is None

    def test_step_rejects_both_python_and_tool(self, tmp_path: Path) -> None:
        """Step with both python and tool is invalid."""
        data = {
            "name": "bad",
            "description": "Both python and tool",
            "steps": {"run": {"python": "mod.fn", "tool": "run_cmd"}},
        }
        wf = load_workflow(_write_yaml(tmp_path / "wf.yaml", data))
        errors = validate_workflow(wf)
        assert any("python" in e and "tool" in e for e in errors)

    def test_step_accepts_python_alone(self, tmp_path: Path) -> None:
        """Step with only python discriminator is valid."""
        data = {
            "name": "ok",
            "description": "Python only",
            "constraints": ["test"],
            "steps": {
                "check": {"python": "mod.fn", "on_success": "done"},
                "done": {"action": "stop", "message": "OK"},
            },
        }
        wf = load_workflow(_write_yaml(tmp_path / "wf.yaml", data))
        errors = validate_workflow(wf)
        assert errors == []

    def test_python_step_requires_dotted_path(self, tmp_path: Path) -> None:
        """python: value must contain at least one dot (module.function)."""
        data = {
            "name": "bad-path",
            "description": "No dot",
            "steps": {"check": {"python": "bare_name"}},
        }
        wf = load_workflow(_write_yaml(tmp_path / "wf.yaml", data))
        errors = validate_workflow(wf)
        assert any("dotted" in e.lower() or "module" in e.lower() for e in errors)

    def test_python_step_with_args_validated(self, tmp_path: Path) -> None:
        """python step's with: args have input references validated."""
        data = {
            "name": "ref-wf",
            "description": "Python with refs",
            "constraints": ["test"],
            "inputs": {"plan_id": {"description": "Plan ID"}},
            "steps": {
                "check": {
                    "python": "mod.fn",
                    "with": {"plan_id": "${{ inputs.plan_id }}"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "OK"},
            },
        }
        wf = load_workflow(_write_yaml(tmp_path / "wf.yaml", data))
        errors = validate_workflow(wf)
        assert errors == []

    # CAP1
    def test_capture_field_parsed(self, tmp_path: Path) -> None:
        """CAP1: capture dict is parsed from step YAML."""
        data = {
            "name": "cap-wf",
            "description": "Capture test",
            "steps": {
                "run": {
                    "tool": "run_skill",
                    "with": {"cwd": "/tmp"},
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                },
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_workflow(_write_yaml(tmp_path / "wf.yaml", data))
        assert wf.steps["run"].capture == {"worktree_path": "${{ result.worktree_path }}"}

    # CAP2
    def test_capture_defaults_empty(self, tmp_path: Path) -> None:
        """CAP2: step without capture has empty capture dict."""
        wf = load_workflow(_write_yaml(tmp_path / "wf.yaml", VALID_WORKFLOW))
        for step in wf.steps.values():
            assert step.capture == {}

    # CAP3
    def test_capture_result_refs_valid(self, tmp_path: Path) -> None:
        """CAP3: capture values using result.* namespace produce no errors."""
        data = {
            "name": "cap-valid",
            "description": "Valid captures",
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
        wf = load_workflow(_write_yaml(tmp_path / "wf.yaml", data))
        errors = validate_workflow(wf)
        assert not any("capture" in e for e in errors)

    # CAP4
    def test_capture_non_result_namespace_rejected(self, tmp_path: Path) -> None:
        """CAP4: capture values must use result.* namespace."""
        data = {
            "name": "cap-bad-ns",
            "description": "Bad namespace",
            "steps": {
                "run": {
                    "tool": "run_cmd",
                    "capture": {"foo": "${{ inputs.bar }}"},
                },
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_workflow(_write_yaml(tmp_path / "wf.yaml", data))
        errors = validate_workflow(wf)
        assert any("result" in e and "capture" in e for e in errors)

        # Also reject context.* namespace in capture values
        data["steps"]["run"]["capture"] = {"foo": "${{ context.bar }}"}
        wf = load_workflow(_write_yaml(tmp_path / "wf2.yaml", data))
        errors = validate_workflow(wf)
        assert any("result" in e and "capture" in e for e in errors)

    # CAP5
    def test_capture_literal_value_rejected(self, tmp_path: Path) -> None:
        """CAP5: capture values must contain ${{ result.X }} expression."""
        data = {
            "name": "cap-literal",
            "description": "Literal capture",
            "steps": {
                "run": {
                    "tool": "run_cmd",
                    "capture": {"foo": "literal string"},
                },
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_workflow(_write_yaml(tmp_path / "wf.yaml", data))
        errors = validate_workflow(wf)
        assert any("capture" in e and "result" in e for e in errors)

    # CAP6
    def test_context_ref_to_captured_var_valid(self, tmp_path: Path) -> None:
        """CAP6: ${{ context.X }} referencing a preceding capture is valid."""
        data = {
            "name": "ctx-valid",
            "description": "Valid context ref",
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
        wf = load_workflow(_write_yaml(tmp_path / "wf.yaml", data))
        errors = validate_workflow(wf)
        assert not any("context" in e for e in errors)

    # CAP7
    def test_context_ref_to_uncaptured_var_rejected(self, tmp_path: Path) -> None:
        """CAP7: ${{ context.X }} where X is never captured is an error."""
        data = {
            "name": "ctx-bad",
            "description": "Uncaptured ref",
            "steps": {
                "test": {
                    "tool": "test_check",
                    "with": {"worktree_path": "${{ context.nonexistent }}"},
                },
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_workflow(_write_yaml(tmp_path / "wf.yaml", data))
        errors = validate_workflow(wf)
        assert any("nonexistent" in e and "context" in e for e in errors)

    # CAP8
    def test_context_forward_reference_rejected(self, tmp_path: Path) -> None:
        """CAP8: ${{ context.X }} referencing a variable captured by a later step is an error."""
        # Step names chosen so alphabetical order (yaml.dump sorts keys)
        # puts "check" (consumer) before "produce" (capturer)
        data = {
            "name": "ctx-fwd",
            "description": "Forward ref",
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
        wf = load_workflow(_write_yaml(tmp_path / "wf.yaml", data))
        errors = validate_workflow(wf)
        assert any("wp" in e and "context" in e for e in errors)

    # CAP9
    def test_bundled_workflows_still_valid(self) -> None:
        """CAP9: existing bundled workflows pass validation with new capture rules."""
        bd = builtin_workflows_dir()
        for f in bd.glob("*.yaml"):
            wf = load_workflow(f)
            errors = validate_workflow(wf)
            assert errors == [], f"Regression in {f.name}: {errors}"

    # CAP10
    def test_multiple_captures_cumulative(self, tmp_path: Path) -> None:
        """CAP10: context.X can reference captures from any preceding step."""
        data = {
            "name": "cumulative",
            "description": "Multi-capture",
            "steps": {
                "step_a": {
                    "tool": "run_skill",
                    "capture": {"var_a": "${{ result.a }}"},
                    "on_success": "step_b",
                },
                "step_b": {
                    "tool": "run_skill",
                    "capture": {"var_b": "${{ result.b }}"},
                    "on_success": "step_c",
                },
                "step_c": {
                    "tool": "run_cmd",
                    "with": {
                        "cmd": "${{ context.var_a }} ${{ context.var_b }}",
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_workflow(_write_yaml(tmp_path / "wf.yaml", data))
        errors = validate_workflow(wf)
        assert not any("context" in e for e in errors)

    # CAP11
    def test_capture_dotted_result_path_valid(self, tmp_path: Path) -> None:
        """CAP11: result.nested.path in capture values is valid."""
        data = {
            "name": "dotted",
            "description": "Dotted result path",
            "steps": {
                "run": {
                    "tool": "run_cmd",
                    "capture": {"foo": "${{ result.data.worktree_path }}"},
                },
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_workflow(_write_yaml(tmp_path / "wf.yaml", data))
        errors = validate_workflow(wf)
        assert not any("capture" in e for e in errors)

    # T4
    def test_workflow_skill_commands_are_namespaced(self) -> None:
        """All skill_command values in workflow YAMLs use /autoskillit: namespace."""
        import autoskillit

        wf_dir = Path(autoskillit.__file__).parent / "workflows"
        for wf_path in wf_dir.glob("*.yaml"):
            content = wf_path.read_text()
            for match in re.finditer(r'skill_command:\s*"(/\S+)', content):
                ref = match.group(1)
                # Allow template expressions like /audit-${{ inputs.audit_type }}
                if "${{" in ref:
                    continue
                assert ref.startswith("/autoskillit:"), (
                    f"{wf_path.name}: {ref} should use /autoskillit: namespace"
                )

    # T_OR1
    def test_on_result_parsed(self, tmp_path: Path) -> None:
        """on_result block is parsed into StepResultRoute."""
        data = {
            "name": "result-wf",
            "description": "Has on_result",
            "steps": {
                "classify": {
                    "tool": "classify_fix",
                    "on_result": {
                        "field": "restart_scope",
                        "routes": {
                            "full_restart": "investigate",
                            "partial_restart": "implement",
                        },
                    },
                    "on_failure": "escalate",
                },
                "investigate": {"action": "stop", "message": "Investigating."},
                "implement": {"action": "stop", "message": "Implementing."},
                "escalate": {"action": "stop", "message": "Escalating."},
            },
        }
        f = _write_yaml(tmp_path / "wf.yaml", data)
        wf = load_workflow(f)
        assert wf.steps["classify"].on_result is not None
        assert isinstance(wf.steps["classify"].on_result, StepResultRoute)
        assert wf.steps["classify"].on_result.field == "restart_scope"
        assert wf.steps["classify"].on_result.routes == {
            "full_restart": "investigate",
            "partial_restart": "implement",
        }

    # T_OR2
    def test_on_result_and_on_success_mutually_exclusive(self, tmp_path: Path) -> None:
        """Having both on_result and on_success is a validation error."""
        data = {
            "name": "conflict-wf",
            "description": "Both on_result and on_success",
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
        f = _write_yaml(tmp_path / "wf.yaml", data)
        wf = load_workflow(f)
        errors = validate_workflow(wf)
        assert any("on_result" in e and "on_success" in e for e in errors)

    # T_OR3
    def test_on_result_empty_field_rejected(self, tmp_path: Path) -> None:
        """on_result.field must be non-empty."""
        data = {
            "name": "empty-field-wf",
            "description": "Empty on_result field",
            "steps": {
                "classify": {
                    "tool": "classify_fix",
                    "on_result": {
                        "field": "",
                        "routes": {"a": "done"},
                    },
                },
                "done": {"action": "stop", "message": "Done."},
            },
        }
        f = _write_yaml(tmp_path / "wf.yaml", data)
        wf = load_workflow(f)
        errors = validate_workflow(wf)
        assert any("field" in e.lower() for e in errors)

    # T_OR4
    def test_on_result_empty_routes_rejected(self, tmp_path: Path) -> None:
        """on_result.routes must be non-empty."""
        data = {
            "name": "empty-routes-wf",
            "description": "Empty on_result routes",
            "steps": {
                "classify": {
                    "tool": "classify_fix",
                    "on_result": {
                        "field": "restart_scope",
                        "routes": {},
                    },
                },
                "done": {"action": "stop", "message": "Done."},
            },
        }
        f = _write_yaml(tmp_path / "wf.yaml", data)
        wf = load_workflow(f)
        errors = validate_workflow(wf)
        assert any("routes" in e.lower() for e in errors)

    # T_OR5
    def test_on_result_route_targets_validated(self, tmp_path: Path) -> None:
        """on_result route targets must reference existing steps or 'done'."""
        data = {
            "name": "bad-route-wf",
            "description": "Bad on_result route target",
            "steps": {
                "classify": {
                    "tool": "classify_fix",
                    "on_result": {
                        "field": "restart_scope",
                        "routes": {
                            "full_restart": "nonexistent",
                            "partial_restart": "done",
                        },
                    },
                },
                "escalate": {"action": "stop", "message": "Escalating."},
            },
        }
        f = _write_yaml(tmp_path / "wf.yaml", data)
        wf = load_workflow(f)
        errors = validate_workflow(wf)
        assert any("nonexistent" in e for e in errors)

    # T_OR6
    def test_on_result_route_done_is_valid(self, tmp_path: Path) -> None:
        """on_result route target 'done' is accepted."""
        data = {
            "name": "done-route-wf",
            "description": "Route to done",
            "constraints": ["test"],
            "steps": {
                "classify": {
                    "tool": "classify_fix",
                    "on_result": {
                        "field": "restart_scope",
                        "routes": {
                            "full_restart": "done",
                            "partial_restart": "done",
                        },
                    },
                    "on_failure": "escalate",
                },
                "escalate": {"action": "stop", "message": "Escalating."},
            },
        }
        f = _write_yaml(tmp_path / "wf.yaml", data)
        wf = load_workflow(f)
        errors = validate_workflow(wf)
        assert errors == []

    # T_OR7
    def test_on_result_with_on_failure_valid(self, tmp_path: Path) -> None:
        """on_result + on_failure together is valid (on_failure is the fallback)."""
        data = {
            "name": "valid-combo-wf",
            "description": "on_result with on_failure",
            "constraints": ["test"],
            "steps": {
                "classify": {
                    "tool": "classify_fix",
                    "on_result": {
                        "field": "restart_scope",
                        "routes": {
                            "full_restart": "investigate",
                            "partial_restart": "implement",
                        },
                    },
                    "on_failure": "escalate",
                },
                "investigate": {"action": "stop", "message": "Investigating."},
                "implement": {"action": "stop", "message": "Implementing."},
                "escalate": {"action": "stop", "message": "Escalating."},
            },
        }
        f = _write_yaml(tmp_path / "wf.yaml", data)
        wf = load_workflow(f)
        errors = validate_workflow(wf)
        assert errors == []

    # T_OR9
    def test_on_result_defaults_to_none(self, tmp_path: Path) -> None:
        """Steps without on_result have on_result=None."""
        f = _write_yaml(tmp_path / "wf.yaml", VALID_WORKFLOW)
        wf = load_workflow(f)
        assert wf.steps["run_tests"].on_result is None

    # CON1
    def test_workflow_schema_supports_constraints(self):
        """Workflow dataclass must have a constraints field."""
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(Workflow)}
        assert "constraints" in field_names, (
            "Workflow dataclass must have a 'constraints' field "
            "for pipeline orchestrator discipline"
        )

    # CON2
    def test_parse_workflow_extracts_constraints(self, tmp_path):
        """_parse_workflow must extract constraints from YAML."""
        data = {
            **VALID_WORKFLOW,
            "constraints": [
                "ONLY use AutoSkillit MCP tools",
                "NEVER use Edit, Write, Read",
            ],
        }
        wf = load_workflow(_write_yaml(tmp_path / "wf.yaml", data))
        assert wf.constraints == [
            "ONLY use AutoSkillit MCP tools",
            "NEVER use Edit, Write, Read",
        ]

    # CON3
    def test_validate_workflow_warns_missing_constraints(self, tmp_path):
        """validate_workflow should warn when constraints are empty."""
        wf = load_workflow(_write_yaml(tmp_path / "wf.yaml", VALID_WORKFLOW))
        errors = validate_workflow(wf)
        warnings = [e for e in errors if "constraints" in e.lower()]
        assert warnings, "validate_workflow must warn when constraints are empty"

    # CON4
    def test_bundled_workflows_have_constraints(self):
        """All bundled workflows must have a non-empty constraints field."""
        wf_dir = builtin_workflows_dir()
        failures = []
        for path in sorted(wf_dir.glob("*.yaml")):
            wf = load_workflow(path)
            if not wf.constraints:
                failures.append(f"{path.name}: missing constraints")
        assert not failures, "Bundled workflows missing constraints:\n" + "\n".join(
            f"  - {f}" for f in failures
        )

    # OPT1
    def test_workflow_step_has_optional_field(self):
        """WorkflowStep must have an optional field of type bool defaulting to False."""
        import dataclasses

        fields = {f.name: f for f in dataclasses.fields(WorkflowStep)}
        assert "optional" in fields, "WorkflowStep must have an 'optional' field"
        assert fields["optional"].type == "bool", (
            f"WorkflowStep.optional must be bool, got {fields['optional'].type}"
        )
        assert fields["optional"].default is False, "WorkflowStep.optional must default to False"

    # OPT2
    def test_parse_step_preserves_optional(self):
        """_parse_step must preserve optional=True and default to False."""
        step_with = _parse_step({"tool": "test_check", "optional": True})
        assert step_with.optional is True, "_parse_step must preserve optional=True"

        step_without = _parse_step({"tool": "test_check"})
        assert step_without.optional is False, "_parse_step must default optional to False"

    # MOD1
    def test_step_model_field_defaults_to_none(self):
        step = WorkflowStep(tool="run_skill")
        assert step.model is None

    # MOD2
    def test_parse_step_extracts_model(self):
        step = _parse_step({"tool": "run_skill", "model": "sonnet"})
        assert step.model == "sonnet"

    # MOD3
    def test_parse_step_model_absent(self):
        step = _parse_step({"tool": "run_skill"})
        assert step.model is None

    # MOD4
    def test_bundled_assess_steps_use_sonnet(self):
        bd = builtin_workflows_dir()
        for f in bd.glob("*.yaml"):
            wf = load_workflow(f)
            for step_name, step in wf.steps.items():
                if (
                    step.with_args.get("skill_command")
                    and "assess-and-merge" in step.with_args["skill_command"]
                ):
                    assert step.model == "sonnet", (
                        f"{f.name} step '{step_name}' should have model='sonnet'"
                    )


class TestDataFlowQuality:
    """Tests for data-flow quality analysis (DFQ prefix)."""

    def _make_workflow(self, steps: dict[str, dict]) -> Workflow:
        """Build a minimal Workflow from step dicts."""
        from autoskillit.workflow_loader import _parse_step

        parsed_steps = {name: _parse_step(data) for name, data in steps.items()}
        return Workflow(
            name="test",
            description="test",
            steps=parsed_steps,
            constraints=["test"],
        )

    # DFQ1
    def test_analyze_dataflow_returns_report(self):
        """analyze_dataflow returns a DataFlowReport with warnings list and summary str."""
        wf = self._make_workflow(
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
    def test_dead_output_detected(self):
        """Captured var with no downstream context.X consumer triggers DEAD_OUTPUT."""
        wf = self._make_workflow(
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
    def test_consumed_output_not_flagged(self):
        """Captured var consumed by downstream step should not trigger DEAD_OUTPUT."""
        wf = self._make_workflow(
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

    # DFQ4
    def test_dead_output_on_any_path_not_flagged(self):
        """Var consumed on one path but not another should NOT trigger DEAD_OUTPUT."""
        wf = self._make_workflow(
            {
                "impl": {
                    "tool": "run_skill",
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_success": "merge",
                    "on_failure": "escalate",
                },
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "${{ context.worktree_path }}"},
                    "on_success": "done",
                },
                "escalate": {"action": "stop", "message": "Failed"},
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT"]
        assert len(dead) == 0

    # DFQ5
    def test_implicit_handoff_detected(self):
        """run_skill step without capture triggers IMPLICIT_HANDOFF."""
        wf = self._make_workflow(
            {
                "impl": {
                    "tool": "run_skill",
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        implicit = [w for w in report.warnings if w.code == "IMPLICIT_HANDOFF"]
        assert len(implicit) == 1
        assert implicit[0].step_name == "impl"

    # DFQ6
    def test_non_skill_step_no_implicit_handoff(self):
        """test_check step without capture should NOT trigger IMPLICIT_HANDOFF."""
        wf = self._make_workflow(
            {
                "test": {
                    "tool": "test_check",
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        implicit = [w for w in report.warnings if w.code == "IMPLICIT_HANDOFF"]
        assert len(implicit) == 0

    # DFQ7
    def test_skill_step_with_capture_no_implicit_handoff(self):
        """run_skill step with capture should NOT trigger IMPLICIT_HANDOFF."""
        wf = self._make_workflow(
            {
                "impl": {
                    "tool": "run_skill",
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        implicit = [w for w in report.warnings if w.code == "IMPLICIT_HANDOFF"]
        assert len(implicit) == 0

    # DFQ8
    def test_terminal_step_no_implicit_handoff(self):
        """action: stop steps should NOT trigger IMPLICIT_HANDOFF."""
        wf = self._make_workflow(
            {
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        implicit = [w for w in report.warnings if w.code == "IMPLICIT_HANDOFF"]
        assert len(implicit) == 0

    # DFQ9
    def test_graph_construction_follows_all_routing_edges(self):
        """_build_step_graph follows on_success, on_failure, on_result, retry edges."""
        wf = self._make_workflow(
            {
                "start": {
                    "tool": "run_skill",
                    "on_success": "check",
                    "on_failure": "fix",
                    "retry": {"max_attempts": 3, "on": "needs_retry", "on_exhausted": "escalate"},
                },
                "check": {
                    "tool": "test_check",
                    "on_result": {
                        "field": "passed",
                        "routes": {"true": "done", "false": "fix"},
                    },
                },
                "fix": {"tool": "run_skill", "on_success": "start"},
                "escalate": {"action": "stop", "message": "Exhausted"},
                "done": {"action": "stop", "message": "Done"},
            }
        )
        graph = _build_step_graph(wf)
        assert graph["start"] == {"check", "fix", "escalate"}
        assert graph["check"] == {"done", "fix"}
        assert graph["fix"] == {"start"}
        assert graph["escalate"] == set()
        assert graph["done"] == set()

    # DFQ10
    def test_dead_output_via_on_result_route(self):
        """Dead output detection works with on_result routing."""
        # Case 1: consumed on one route -> no warning
        wf = self._make_workflow(
            {
                "impl": {
                    "tool": "run_skill",
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_result": {
                        "field": "success",
                        "routes": {"true": "merge", "false": "escalate"},
                    },
                },
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "${{ context.worktree_path }}"},
                    "on_success": "done",
                },
                "escalate": {"action": "stop", "message": "Failed"},
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT"]
        assert len(dead) == 0

        # Case 2: consumed on neither route -> warning
        wf2 = self._make_workflow(
            {
                "impl": {
                    "tool": "run_skill",
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_result": {
                        "field": "success",
                        "routes": {"true": "done", "false": "escalate"},
                    },
                },
                "done": {"action": "stop", "message": "Done"},
                "escalate": {"action": "stop", "message": "Failed"},
            }
        )
        report2 = analyze_dataflow(wf2)
        dead2 = [w for w in report2.warnings if w.code == "DEAD_OUTPUT"]
        assert len(dead2) == 1
        assert dead2[0].field == "worktree_path"

    # DFQ11
    def test_summary_reports_counts(self):
        """Summary includes warning count when warnings exist."""
        wf = self._make_workflow(
            {
                "impl": {
                    "tool": "run_skill",
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_success": "run",
                },
                "run": {
                    "tool": "run_skill",
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        # 1 dead output (worktree_path) + 1 implicit handoff (run) = 2
        assert "2 data-flow warnings" in report.summary

    # DFQ12
    def test_clean_workflow_summary(self):
        """Clean workflow summary says no warnings."""
        wf = self._make_workflow(
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
        assert "No data-flow warnings" in report.summary

    # DFQ13
    def test_bundled_workflows_produce_reports(self):
        """analyze_dataflow runs cleanly on all bundled workflow YAMLs."""
        wf_dir = builtin_workflows_dir()
        assert wf_dir.is_dir(), f"Bundled workflows dir not found: {wf_dir}"
        yaml_files = list(wf_dir.glob("*.yaml")) + list(wf_dir.glob("*.yml"))
        assert len(yaml_files) > 0, "No bundled workflow files found"
        for yaml_file in yaml_files:
            wf = load_workflow(yaml_file)
            report = analyze_dataflow(wf)
            assert isinstance(report, DataFlowReport)
            assert isinstance(report.warnings, list)

    # DFQ15
    def test_multiple_dead_outputs_all_reported(self):
        """Multiple dead captures each get their own DEAD_OUTPUT warning."""
        wf = self._make_workflow(
            {
                "impl": {
                    "tool": "run_skill",
                    "capture": {
                        "a": "${{ result.a }}",
                        "b": "${{ result.b }}",
                        "c": "${{ result.c }}",
                    },
                    "on_success": "test",
                },
                "test": {
                    "tool": "test_check",
                    "with": {"val": "${{ context.a }}"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT"]
        assert len(dead) == 2
        dead_fields = {w.field for w in dead}
        assert dead_fields == {"b", "c"}


# ---------------------------------------------------------------------------
# TestVersionField: autoskillit_version field on Workflow dataclass
# ---------------------------------------------------------------------------

_VALID_WORKFLOW_DATA: dict = {
    "name": "version-test-workflow",
    "description": "A workflow for testing the version field",
    "steps": {
        "do_it": {
            "tool": "run_cmd",
            "on_success": "done",
        },
        "done": {"action": "stop", "message": "Done."},
    },
    "constraints": ["Only use AutoSkillit MCP tools during pipeline execution"],
}


class TestVersionField:
    """autoskillit_version field on Workflow dataclass."""

    # VER1: Workflow without autoskillit_version has version=None
    def test_version_none_when_absent(self) -> None:
        """_parse_workflow sets version=None when autoskillit_version is absent."""
        data = dict(_VALID_WORKFLOW_DATA)
        wf = _parse_workflow(data)
        assert wf.version is None

    # VER2: Workflow with autoskillit_version="0.2.0" parses correctly
    def test_version_set_when_present(self) -> None:
        """_parse_workflow reads autoskillit_version and stores it as version."""
        data = dict(_VALID_WORKFLOW_DATA)
        data["autoskillit_version"] = "0.2.0"
        wf = _parse_workflow(data)
        assert wf.version == "0.2.0"

    # VER3: autoskillit_version does not cause validation errors
    def test_version_does_not_cause_validation_errors(self) -> None:
        """A workflow with autoskillit_version passes validate_workflow with no errors."""
        data = dict(_VALID_WORKFLOW_DATA)
        data["autoskillit_version"] = "0.2.0"
        wf = _parse_workflow(data)
        errors = validate_workflow(wf)
        assert errors == []

    # VER4: autoskillit_version is preserved in round-trip (parse -> access)
    def test_version_preserved_in_round_trip(self, tmp_path: Path) -> None:
        """version attribute survives a full write-to-disk and load_workflow round-trip."""
        data = dict(_VALID_WORKFLOW_DATA)
        data["autoskillit_version"] = "1.3.0"
        path = _write_yaml(tmp_path / "wf.yaml", data)
        wf = load_workflow(path)
        assert wf.version == "1.3.0"
