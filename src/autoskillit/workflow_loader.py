"""YAML workflow parsing, validation, and discovery."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from autoskillit.types import RETRY_RESPONSE_FIELDS, LoadReport, LoadResult, WorkflowSource


@dataclass
class WorkflowInput:
    description: str
    required: bool = False
    default: str | None = None


@dataclass
class StepRetry:
    max_attempts: int = 3
    on: str | None = None
    on_exhausted: str = "escalate"


@dataclass
class StepResultRoute:
    """Multi-way routing based on a named field in a tool's JSON response."""

    field: str
    routes: dict[str, str] = dataclasses.field(default_factory=dict)


@dataclass
class WorkflowStep:
    tool: str | None = None
    action: str | None = None
    python: str | None = None
    with_args: dict[str, str] = field(default_factory=dict)
    on_success: str | None = None
    on_failure: str | None = None
    on_result: StepResultRoute | None = None
    retry: StepRetry | None = None
    message: str | None = None
    note: str | None = None


@dataclass
class Workflow:
    name: str
    description: str
    summary: str = ""
    inputs: dict[str, WorkflowInput] = field(default_factory=dict)
    steps: dict[str, WorkflowStep] = field(default_factory=dict)


@dataclass
class WorkflowInfo:
    name: str
    description: str
    source: WorkflowSource
    path: Path


def load_workflow(path: Path) -> Workflow:
    """Parse a YAML workflow file into a Workflow dataclass."""
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Workflow file must contain a YAML mapping: {path}")
    return _parse_workflow(data)


def validate_workflow(wf: Workflow) -> list[str]:
    """Return a list of validation errors (empty if valid)."""
    errors: list[str] = []

    if not wf.name:
        errors.append("Workflow must have a 'name'.")
    if not wf.steps:
        errors.append("Workflow must have at least one step.")

    step_names = set(wf.steps.keys())

    for step_name, step in wf.steps.items():
        discriminators = [d for d in ("tool", "action", "python") if getattr(step, d) is not None]
        if len(discriminators) == 0:
            errors.append(f"Step '{step_name}' must have 'tool', 'action', or 'python'.")
        if len(discriminators) > 1:
            errors.append(
                f"Step '{step_name}' has multiple discriminators "
                f"({', '.join(discriminators)}); pick one."
            )
        if step.python is not None and "." not in step.python:
            errors.append(
                f"Step '{step_name}'.python must be a dotted path "
                f"(module.function), got '{step.python}'."
            )
        if step.action == "stop" and not step.message:
            errors.append(f"Terminal step '{step_name}' (action: stop) must have a 'message'.")
        for goto_field in ("on_success", "on_failure"):
            target = getattr(step, goto_field)
            if target and target not in step_names and target != "done":
                errors.append(
                    f"Step '{step_name}'.{goto_field} references unknown step '{target}'."
                )
        if step.retry and step.retry.on_exhausted not in step_names:
            errors.append(
                f"Step '{step_name}'.retry.on_exhausted references "
                f"unknown step '{step.retry.on_exhausted}'."
            )
        if step.retry and step.retry.on and step.retry.on not in RETRY_RESPONSE_FIELDS:
            errors.append(
                f"Step '{step_name}'.retry.on references unknown response field "
                f"'{step.retry.on}'. Valid fields: {sorted(RETRY_RESPONSE_FIELDS)}"
            )
        if step.on_result is not None:
            if step.on_success is not None:
                errors.append(
                    f"Step '{step_name}' has both 'on_result' and 'on_success'; "
                    f"they are mutually exclusive."
                )
            if not step.on_result.field:
                errors.append(f"Step '{step_name}'.on_result.field must be non-empty.")
            if not step.on_result.routes:
                errors.append(f"Step '{step_name}'.on_result.routes must be non-empty.")
            for value, target in step.on_result.routes.items():
                if target not in step_names and target != "done":
                    errors.append(
                        f"Step '{step_name}'.on_result.routes.{value} references "
                        f"unknown step '{target}'."
                    )

    input_names = set(wf.inputs.keys())
    for step_name, step in wf.steps.items():
        for arg_key, arg_val in step.with_args.items():
            for ref in _extract_refs(arg_val):
                if ref.startswith("inputs."):
                    input_name = ref[len("inputs.") :]
                    if input_name not in input_names:
                        errors.append(
                            f"Step '{step_name}'.with.{arg_key} references "
                            f"undeclared input '{input_name}'."
                        )

    return errors


def list_workflows(project_dir: Path) -> LoadResult[WorkflowInfo]:
    """Find available workflows from project and built-in sources."""
    seen: set[str] = set()
    items: list[WorkflowInfo] = []
    errors: list[LoadReport] = []

    project_wf_dir = project_dir / ".autoskillit" / "workflows"
    _collect_workflows(WorkflowSource.PROJECT, project_wf_dir, seen, items, errors)

    builtin_dir = Path(__file__).parent / "workflows"
    _collect_workflows(WorkflowSource.BUILTIN, builtin_dir, seen, items, errors)

    return LoadResult(items=sorted(items, key=lambda w: w.name), errors=errors)


def builtin_workflows_dir() -> Path:
    """Return the path to the built-in workflows directory."""
    return Path(__file__).parent / "workflows"


# --- internal helpers ---


def _parse_workflow(data: dict[str, Any]) -> Workflow:
    name = data.get("name", "")
    description = data.get("description", "")
    summary = data.get("summary", "")

    inputs: dict[str, WorkflowInput] = {}
    for inp_name, inp_data in (data.get("inputs") or {}).items():
        if isinstance(inp_data, dict):
            inputs[inp_name] = WorkflowInput(
                description=inp_data.get("description", ""),
                required=inp_data.get("required", False),
                default=inp_data.get("default"),
            )

    steps: dict[str, WorkflowStep] = {}
    for step_name, step_data in (data.get("steps") or {}).items():
        if isinstance(step_data, dict):
            steps[step_name] = _parse_step(step_data)

    return Workflow(
        name=name, description=description, summary=summary, inputs=inputs, steps=steps
    )


def _parse_step(data: dict[str, Any]) -> WorkflowStep:
    retry = None
    retry_data = data.get("retry")
    if isinstance(retry_data, dict):
        retry = StepRetry(
            max_attempts=retry_data.get("max_attempts", 3),
            on=retry_data.get("on"),
            on_exhausted=retry_data.get("on_exhausted", "escalate"),
        )

    on_result = None
    on_result_data = data.get("on_result")
    if isinstance(on_result_data, dict):
        on_result = StepResultRoute(
            field=on_result_data.get("field", ""),
            routes=on_result_data.get("routes", {}),
        )

    return WorkflowStep(
        tool=data.get("tool"),
        action=data.get("action"),
        python=data.get("python"),
        with_args=data.get("with", {}),
        on_success=data.get("on_success"),
        on_failure=data.get("on_failure"),
        on_result=on_result,
        retry=retry,
        message=data.get("message"),
        note=data.get("note"),
    )


def _extract_refs(value: str) -> list[str]:
    """Extract ${{ X }} references from a string."""
    refs: list[str] = []
    rest = value
    while "${{" in rest:
        start = rest.index("${{") + 3
        end = rest.index("}}", start)
        refs.append(rest[start:end].strip())
        rest = rest[end + 2 :]
    return refs


def _collect_workflows(
    source: WorkflowSource,
    directory: Path,
    seen: set[str],
    result: list[WorkflowInfo],
    errors: list[LoadReport],
) -> None:
    if not directory.is_dir():
        return
    for f in sorted(directory.iterdir()):
        if f.suffix in (".yaml", ".yml") and f.is_file():
            try:
                wf = load_workflow(f)
                if wf.name and wf.name not in seen:
                    seen.add(wf.name)
                    result.append(
                        WorkflowInfo(
                            name=wf.name,
                            description=wf.description,
                            source=source,
                            path=f,
                        )
                    )
            except Exception as exc:
                errors.append(LoadReport(path=f, error=str(exc)))
