from __future__ import annotations

import re
from typing import Any, TypedDict


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

    result: dict[str, Any] = dict(data)
    result.setdefault("summary", "")
    result.setdefault("goal", "")
    result.setdefault("technical_steps", [])
    result.setdefault("files_touched", [])
    result.setdefault("apis_defined", [])
    result.setdefault("apis_consumed", [])
    result.setdefault("depends_on", [])
    result.setdefault("acceptance_criteria", [])

    return result
