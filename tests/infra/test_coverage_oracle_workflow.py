"""Publication-proof contracts for workflows that publish task-backed results."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
_TASK_INVOCATION = re.compile(r"\btask\s+[^\s\\]+")
_STATUS_CAPTURE = re.compile(
    r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)=\$(?:\?|\{PIPESTATUS\[[0-9]+\]\})\s*$"
)


def _task_status_is_reemitted(script: str) -> bool:
    capture = _STATUS_CAPTURE.search(script)
    if capture is None:
        return False
    status_name = capture.group(1)
    return bool(re.search(rf"\bexit\s+\"?\${status_name}\"?\b", script))


def _workflow_trigger(workflow: dict) -> dict:
    return workflow.get("on") or workflow.get(True) or {}


def test_task_steps_preceding_git_push_reemit_their_status() -> None:
    """A job publishing with git push must make each preceding task's status explicit."""
    for workflow_path in sorted(_WORKFLOWS.glob("*.yml")):
        workflow = load_yaml(workflow_path)
        assert isinstance(workflow, dict)
        for job_name, job in workflow["jobs"].items():
            assert isinstance(job, dict)
            steps = job.get("steps", [])
            push_indices = [
                index for index, step in enumerate(steps) if "git push" in str(step.get("run", ""))
            ]
            for push_index in push_indices:
                for step in steps[:push_index]:
                    script = step.get("run")
                    if isinstance(script, str) and _TASK_INVOCATION.search(script):
                        assert _task_status_is_reemitted(script), (
                            f"{workflow_path.name}:{job_name}: task-backed publishing requires "
                            f"explicit status re-emission in step {step.get('name')!r}"
                        )


@pytest.fixture(scope="module")
def coverage_oracle_workflow() -> dict:
    workflow = load_yaml(_WORKFLOWS / "coverage-oracle.yml")
    assert isinstance(workflow, dict)
    return workflow


def _step_named(workflow: dict, name: str) -> dict:
    steps = workflow["jobs"]["refresh-oracle"]["steps"]
    return next(step for step in steps if step.get("name") == name)


def test_coverage_oracle_commit_is_success_gated(coverage_oracle_workflow: dict) -> None:
    commit_step = _step_named(coverage_oracle_workflow, "Commit updated oracle")
    condition = commit_step.get("if")
    assert isinstance(condition, str)
    assert "success()" in condition


def test_coverage_oracle_manual_dispatch_defaults_to_no_publication(
    coverage_oracle_workflow: dict,
) -> None:
    dispatch = _workflow_trigger(coverage_oracle_workflow)["workflow_dispatch"]
    assert dispatch["inputs"]["publish"]["default"] is False


def test_coverage_oracle_manual_publish_requires_explicit_input(
    coverage_oracle_workflow: dict,
) -> None:
    commit_step = _step_named(coverage_oracle_workflow, "Commit updated oracle")
    condition = commit_step.get("if")
    assert isinstance(condition, str)
    assert "github.event_name != 'workflow_dispatch'" in condition
    assert "inputs.publish" in condition
