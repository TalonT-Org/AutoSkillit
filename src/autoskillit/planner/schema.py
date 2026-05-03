from __future__ import annotations

import re
import warnings
from typing import Any, Literal, TypedDict


class PhaseResult(TypedDict):
    id: str
    phase_number: int
    name: str
    name_slug: str
    goal: str
    scope: list[str]
    ordering: int
    assignments_preview: list[str]
    assignments: list[dict[str, Any]]
    relationship_notes: str


class AssignmentResult(TypedDict):
    id: str
    phase_number: int
    assignment_number: int
    name: str
    phase_id: str
    goal: str
    technical_approach: str
    proposed_work_packages: list[dict[str, Any]]


class WPResult(TypedDict):
    id: str
    name: str
    summary: str
    goal: str
    technical_steps: list[str]
    files_touched: list[str]
    apis_defined: list[str]
    apis_consumed: list[str]
    depends_on: list[str]
    deliverables: list[str]
    acceptance_criteria: list[str]


class PhaseShort(TypedDict):
    id: str
    name: str
    goal: str
    scope: list[str]
    ordering: int


class PhaseElaborated(TypedDict):
    id: str
    name: str
    goal: str
    scope: list[str]
    technical_approach: str
    relationship_notes: str
    assignments_preview: list[str]
    ordering: int


class AssignmentShort(TypedDict):
    id: str
    phase_id: str
    name: str
    goal: str


class AssignmentElaborated(TypedDict):
    id: str
    phase_id: str
    name: str
    goal: str
    technical_approach: str
    proposed_work_packages: list[dict[str, Any]]
    dependency_notes: str
    overlap_notes: str


class WPShort(TypedDict):
    id: str
    assignment_id: str
    phase_id: str
    name: str
    goal: str
    scope: str
    estimated_files: list[str]


class WPElaborated(TypedDict):
    id: str
    assignment_id: str
    phase_id: str
    name: str
    scope: str
    estimated_files: list[str]
    goal: str
    summary: str
    technical_steps: list[str]
    files_touched: list[str]
    apis_defined: list[str]
    apis_consumed: list[str]
    depends_on: list[str]
    deliverables: list[str]
    acceptance_criteria: list[str]


class _PlanDocumentBase(TypedDict):
    task: str
    source_dir: str


class PlanDocument(_PlanDocumentBase, total=False):
    phases: list[PhaseShort | PhaseElaborated]
    assignments: list[AssignmentShort | AssignmentElaborated]
    work_packages: list[WPShort | WPElaborated]


class PlannerManifestItem(TypedDict):
    id: str
    name: str
    status: str
    result_path: str | None
    metadata: dict[str, Any]


class PlannerManifest(TypedDict):
    pass_name: str
    result_dir: str
    created_at: str
    items: list[PlannerManifestItem]


class RunDirResult(TypedDict):
    planner_dir: str


class TaskResolutionResult(TypedDict):
    task_file_path: str
    task_label: str


class ValidationFinding(TypedDict):
    message: str
    severity: Literal["error", "warning"]
    check: str


_PHASE_ID_RE = re.compile(r"^P\d+$")
_ASSIGN_ID_RE = re.compile(r"^P\d+-A\d+$")
_WP_ID_RE = re.compile(r"^P\d+-A\d+-WP\d+$")

PHASE_REQUIRED_KEYS: frozenset[str] = frozenset({"id", "name", "ordering"})
ASSIGNMENT_REQUIRED_KEYS: frozenset[str] = frozenset({"id", "name", "proposed_work_packages"})
WP_REQUIRED_KEYS: frozenset[str] = frozenset({"id", "name", "deliverables"})


def _slugify(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _parse_phase_number(data: dict[str, Any]) -> int:
    if "ordering" in data:
        return int(data["ordering"])
    return int(str(data["id"])[1:])


def _parse_assignment_numbers(data: dict[str, Any]) -> tuple[int, int]:
    raw_id = str(data["id"])
    parts = raw_id.split("-")
    if len(parts) < 2:
        raise ValueError(f"Assignment id {raw_id!r} is missing '-' separator")
    try:
        return int(parts[0][1:]), int(parts[1][1:])
    except ValueError:
        raise ValueError(f"Assignment id {raw_id!r} has malformed numeric segment") from None


def validate_phase_result(data: dict[str, Any]) -> dict[str, Any]:
    missing = PHASE_REQUIRED_KEYS - data.keys()
    if missing:
        raise ValueError(f"Phase result missing required keys: {sorted(missing)}")

    result: dict[str, Any] = dict(data)

    if "phase_number" not in result:
        result["phase_number"] = _parse_phase_number(data)

    if "name_slug" not in result:
        result["name_slug"] = _slugify(data["name"])

    if "assignments" not in result:
        preview = result.get("assignments_preview", [])
        result["assignments"] = [{"name": name, "metadata": {}} for name in preview]

    result.setdefault("assignments_preview", [])
    result.setdefault("goal", "")
    result.setdefault("scope", [])
    result.setdefault("relationship_notes", "")

    return result


def validate_assignment_result(data: dict[str, Any]) -> dict[str, Any]:
    missing = ASSIGNMENT_REQUIRED_KEYS - data.keys()
    if missing:
        raise ValueError(f"Assignment result missing required keys: {sorted(missing)}")

    result: dict[str, Any] = dict(data)

    if "phase_number" not in result or "assignment_number" not in result:
        pn, an = _parse_assignment_numbers(data)
        result.setdefault("phase_number", pn)
        result.setdefault("assignment_number", an)

    result.setdefault("phase_id", f"P{result['phase_number']}")
    result.setdefault("goal", "")
    result.setdefault("technical_approach", "")

    return result


def validate_wp_result(data: dict[str, Any]) -> dict[str, Any]:
    missing = WP_REQUIRED_KEYS - data.keys()
    if missing:
        raise ValueError(f"WP result missing required keys: {sorted(missing)}")

    _reject_empty_string_ids(data, "WP result")

    result: dict[str, Any] = dict(data)

    wp_id = result["id"]
    if not _WP_ID_RE.match(wp_id):
        warnings.warn(
            f"WP id {wp_id!r} does not match expected PX-AY-WPZ format",
            stacklevel=2,
        )

    result.setdefault("summary", "")
    result.setdefault("goal", "")
    result.setdefault("technical_steps", [])
    result.setdefault("files_touched", [])
    result.setdefault("apis_defined", [])
    result.setdefault("apis_consumed", [])
    result.setdefault("depends_on", [])
    result.setdefault("acceptance_criteria", [])

    return result


def _reject_empty_string_ids(data: dict[str, Any], context: str) -> None:
    if "id" in data and data["id"] == "":
        raise ValueError(f"{context}: 'id' field is present but empty")


def resolve_wp_id(wp: dict[str, Any], assign_id: str) -> str:
    wp_id = wp.get("id", "")
    if wp_id:
        return wp_id
    id_suffix = wp.get("id_suffix", "")
    if id_suffix:
        return f"{assign_id}-{id_suffix}"
    raise ValueError(f"Work package in assignment {assign_id!r} has neither 'id' nor 'id_suffix'")


def validate_refined_assignments(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize refined_assignments.json at ingestion."""
    result: dict[str, Any] = dict(data)
    assignments = result.get("assignments", [])
    if not assignments:
        raise ValueError("refined_assignments must contain non-empty 'assignments' list")

    result["assignments"] = [dict(a) for a in assignments]

    for assign in result["assignments"]:
        assign_id = assign.get("id", "")
        if not assign_id:
            phase_id = assign.get("phase_id", "")
            pn = assign.get("phase_number", 0)
            an = assign.get("assignment_number", 0)
            if not phase_id and not pn:
                raise ValueError(
                    "assignment has no resolvable id: needs 'id', "
                    "or 'phase_id'+'assignment_number', "
                    "or 'phase_number'+'assignment_number'"
                )
            if not phase_id:
                phase_id = f"P{pn}"
            assign_id = f"{phase_id}-A{an}"
            assign["id"] = assign_id

        assign["proposed_work_packages"] = [
            dict(wp) for wp in assign.get("proposed_work_packages", [])
        ]
        for wp in assign["proposed_work_packages"]:
            wp["id"] = resolve_wp_id(wp, assign_id)

    return result


def validate_refined_plan(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize refined_plan.json at ingestion."""
    phases = data.get("phases", [])
    if not phases:
        raise ValueError("refined_plan must contain non-empty 'phases' list")

    for phase in phases:
        phase_id = phase.get("id", "")
        if not phase_id:
            raise ValueError(f"Phase has empty 'id': {phase.get('name', '<unnamed>')}")
        previews = phase.get("assignments_preview", [])
        for i, preview in enumerate(previews):
            if (
                isinstance(preview, dict)
                and not preview.get("id", "")
                and not preview.get("name", "")
            ):
                raise ValueError(
                    f"Phase {phase_id} assignments_preview[{i}] has neither 'id' nor 'name'"
                )

    return data
