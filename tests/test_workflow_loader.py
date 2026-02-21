"""Tests for workflow YAML loading and validation."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from autoskillit.workflow_loader import (
    Workflow,
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
        workflows = list_workflows(tmp_path)
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

        workflows = list_workflows(tmp_path)
        match = next(w for w in workflows if w.name == "bugfix-loop")
        assert match.source == "project"
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
