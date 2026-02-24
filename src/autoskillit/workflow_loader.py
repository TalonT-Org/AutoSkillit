"""YAML workflow parsing, validation, and discovery."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from autoskillit.types import RETRY_RESPONSE_FIELDS, LoadReport, LoadResult, WorkflowSource

_SKILL_TOOLS: frozenset[str] = frozenset({"run_skill", "run_skill_retry"})


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
    capture: dict[str, str] = field(default_factory=dict)
    optional: bool = False
    model: str | None = None


@dataclass
class Workflow:
    name: str
    description: str
    summary: str = ""
    inputs: dict[str, WorkflowInput] = field(default_factory=dict)
    steps: dict[str, WorkflowStep] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    version: str | None = None


@dataclass
class WorkflowInfo:
    name: str
    description: str
    source: WorkflowSource
    path: Path


@dataclass
class DataFlowWarning:
    """A non-blocking quality finding about pipeline data flow."""

    code: str  # DEAD_OUTPUT, IMPLICIT_HANDOFF
    step_name: str  # Step where the issue originates
    field: str  # Capture key or tool name
    message: str  # Human-readable explanation


@dataclass
class DataFlowReport:
    """Quality analysis of pipeline data flow (non-blocking)."""

    warnings: list[DataFlowWarning] = field(default_factory=list)
    summary: str = ""


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

    # Validate capture values: must contain ${{ result.* }} expressions
    for step_name, step in wf.steps.items():
        for cap_key, cap_val in step.capture.items():
            refs = _extract_refs(cap_val)
            if not refs:
                errors.append(
                    f"Step '{step_name}'.capture.{cap_key} must contain "
                    f"a ${{{{ result.* }}}} expression."
                )
            for ref in refs:
                if not ref.startswith("result."):
                    errors.append(
                        f"Step '{step_name}'.capture.{cap_key} references "
                        f"'{ref}'; capture values must use the 'result.' namespace."
                    )

    # Validate input and context references in with_args
    input_names = set(wf.inputs.keys())
    available_context: set[str] = set()

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
                elif ref.startswith("context."):
                    ctx_var = ref[len("context.") :]
                    if ctx_var not in available_context:
                        errors.append(
                            f"Step '{step_name}'.with.{arg_key} references "
                            f"context variable '{ctx_var}' which has not been "
                            f"captured by a preceding step."
                        )

        # After validating this step's with_args, add its captures for subsequent steps
        available_context.update(step.capture.keys())

    if not wf.constraints:
        errors.append(
            "Workflow has no 'constraints' field. Pipeline scripts should include "
            "orchestrator discipline constraints."
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

    constraints = data.get("constraints", [])
    if not isinstance(constraints, list):
        constraints = []

    return Workflow(
        name=name,
        description=description,
        summary=summary,
        inputs=inputs,
        steps=steps,
        constraints=constraints,
        version=data.get("autoskillit_version"),
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
        capture=data.get("capture", {}),
        optional=bool(data.get("optional", False)),
        model=data.get("model"),
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


def _build_step_graph(wf: Workflow) -> dict[str, set[str]]:
    """Build a routing adjacency list from all step routing fields.

    Each key is a step name, each value is the set of step names
    reachable in one hop (successors). Terminal targets like "done"
    are excluded since they are not real steps.
    """
    step_names = set(wf.steps.keys())
    graph: dict[str, set[str]] = {name: set() for name in step_names}

    for name, step in wf.steps.items():
        for target in (step.on_success, step.on_failure):
            if target and target in step_names:
                graph[name].add(target)
        if step.on_result:
            for target in step.on_result.routes.values():
                if target in step_names:
                    graph[name].add(target)
        if step.retry and step.retry.on_exhausted in step_names:
            graph[name].add(step.retry.on_exhausted)

    return graph


def _detect_dead_outputs(wf: Workflow, graph: dict[str, set[str]]) -> list[DataFlowWarning]:
    """Detect captured variables that are never consumed downstream."""
    warnings: list[DataFlowWarning] = []

    for step_name, step in wf.steps.items():
        if not step.capture:
            continue

        # BFS: collect all steps reachable from this step's successors
        reachable: set[str] = set()
        frontier = list(graph.get(step_name, set()))
        while frontier:
            current = frontier.pop()
            if current in reachable:
                continue
            reachable.add(current)
            frontier.extend(graph.get(current, set()))

        # Collect all context.X references in reachable steps' with_args
        consumed: set[str] = set()
        for reachable_name in reachable:
            reachable_step = wf.steps[reachable_name]
            for arg_val in reachable_step.with_args.values():
                for ref in _extract_refs(arg_val):
                    if ref.startswith("context."):
                        consumed.add(ref[len("context.") :])

        # Flag captured vars not consumed on any path
        for cap_key in step.capture:
            if cap_key not in consumed:
                warnings.append(
                    DataFlowWarning(
                        code="DEAD_OUTPUT",
                        step_name=step_name,
                        field=cap_key,
                        message=(
                            f"Step '{step_name}' captures '{cap_key}' but no "
                            f"reachable downstream step references "
                            f"${{{{ context.{cap_key} }}}}."
                        ),
                    )
                )

    return warnings


def _detect_implicit_handoffs(wf: Workflow) -> list[DataFlowWarning]:
    """Detect skill-invoking steps with no capture block."""
    warnings: list[DataFlowWarning] = []

    for step_name, step in wf.steps.items():
        if step.tool in _SKILL_TOOLS and not step.capture:
            warnings.append(
                DataFlowWarning(
                    code="IMPLICIT_HANDOFF",
                    step_name=step_name,
                    field=step.tool,
                    message=(
                        f"Step '{step_name}' calls '{step.tool}' but has no "
                        f"capture: block. Data flows to subsequent steps "
                        f"implicitly through agent context rather than "
                        f"explicit ${{{{ context.X }}}} wiring."
                    ),
                )
            )

    return warnings


def analyze_dataflow(wf: Workflow) -> DataFlowReport:
    """Analyze pipeline data flow quality (non-blocking warnings).

    Unlike validate_workflow() which returns blocking errors for
    structural problems, this function returns advisory warnings
    about data-flow quality: dead outputs, implicit hand-offs,
    and a summary.
    """
    graph = _build_step_graph(wf)

    warnings: list[DataFlowWarning] = []
    warnings.extend(_detect_dead_outputs(wf, graph))
    warnings.extend(_detect_implicit_handoffs(wf))

    if warnings:
        summary = f"{len(warnings)} data-flow warning{'s' if len(warnings) != 1 else ''} found."
    else:
        summary = (
            "No data-flow warnings. All captures are consumed"
            " and skill outputs are explicitly wired."
        )

    return DataFlowReport(warnings=warnings, summary=summary)


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
