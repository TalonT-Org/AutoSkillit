from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autoskillit.planner.schema import (
    validate_assignment_result,
    validate_phase_result,
    validate_wp_result,
)


def make_phase_result(
    phase_number: int, *, name: str = "Test Phase", **overrides: Any
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": f"P{phase_number}",
        "name": name,
        "goal": f"Goal for phase {phase_number}",
        "scope": [],
        "ordering": phase_number,
        "relationship_notes": "",
        "assignments_preview": [],
        **overrides,
    }
    return validate_phase_result(data)


def make_assignment_result(
    phase_number: int, assignment_number: int, **overrides: Any
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": f"P{phase_number}-A{assignment_number}",
        "name": f"Assignment {assignment_number}",
        "phase_id": f"P{phase_number}",
        "goal": "Test goal",
        "technical_approach": "Test approach",
        "proposed_work_packages": [],
        **overrides,
    }
    return validate_assignment_result(data)


def make_wp_result(wp_id: str, *, allow_stub: bool = False, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": wp_id,
        "name": f"WP {wp_id}",
        "summary": "summary",
        "goal": "goal",
        "deliverables": [f"src/mod_{wp_id}.py"],
        "technical_steps": ["step 1"],
        "acceptance_criteria": ["criterion 1"],
        "depends_on": [],
        **overrides,
    }
    return validate_wp_result(data, allow_stub=allow_stub)


def write_json(path: Path, data: object) -> None:
    """Write ``data`` as JSON to ``path``, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def write_task_file(tmp_path: Path, content: str = "test task") -> str:
    f = tmp_path / "task_input.md"
    f.write_text(content)
    return str(f)


def make_refined_wps(tmp_path: Path, wps: list[dict[str, Any]]) -> Path:
    doc = {"task": "Test task", "source_dir": "/src", "work_packages": wps, "schema_version": 1}
    p = tmp_path / "refined_wps.json"
    write_json(p, doc)
    return p


def make_manifest(consolidation_dir: Path, phase_id: str, groups: list[dict[str, Any]]) -> None:
    consolidation_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        consolidation_dir / f"{phase_id}_consolidation.json",
        {"phase_id": phase_id, "groups": groups},
    )


def make_minimal_output_dir(
    tmp_path: Path,
    *,
    num_phases: int = 1,
    wps_per_assignment: int = 1,
    deliverables_override: list[str] | None = None,
    depends_on_override: dict[str, list[str]] | None = None,
    extra_phases: list[int] | None = None,
    extra_assignments: list[tuple[int, int]] | None = None,
    stub_wp_ids: set[str] | None = None,
) -> Path:
    phases_dir = tmp_path / "phases"
    assigns_dir = tmp_path / "assignments"
    wps_dir = tmp_path / "work_packages"

    for p in range(1, num_phases + 1):
        write_json(
            phases_dir / f"P{p}_result.json",
            make_phase_result(p, name=f"Phase {p}"),
        )

    for p in range(1, num_phases + 1):
        for a in range(1, 2):
            write_json(
                assigns_dir / f"P{p}-A{a}_result.json",
                make_assignment_result(
                    p,
                    a,
                    name=f"Assignment P{p}-A{a}",
                    proposed_work_packages=[
                        f"P{p}-A{a}-WP{w}" for w in range(1, wps_per_assignment + 1)
                    ],
                ),
            )

    _stub_ids = stub_wp_ids or set()
    for p in range(1, num_phases + 1):
        for a in range(1, 2):
            for w in range(1, wps_per_assignment + 1):
                wp_id = f"P{p}-A{a}-WP{w}"
                if wp_id in _stub_ids:
                    write_json(
                        wps_dir / f"{wp_id}_result.json",
                        make_wp_result(
                            wp_id,
                            allow_stub=True,
                            elaboration_failed=True,
                            deliverables=[],
                            technical_steps=[],
                            acceptance_criteria=[],
                        ),
                    )
                else:
                    deliverables = (
                        deliverables_override
                        if deliverables_override is not None
                        else [f"src/mod_{wp_id}.py"]
                    )
                    deps = (depends_on_override or {}).get(wp_id, [])
                    write_json(
                        wps_dir / f"{wp_id}_result.json",
                        make_wp_result(wp_id, deliverables=deliverables, depends_on=deps),
                    )

    manifest_items = []
    for p in range(1, num_phases + 1):
        for a in range(1, 2):
            for w in range(1, wps_per_assignment + 1):
                wp_id = f"P{p}-A{a}-WP{w}"
                status = "elaboration_failed" if wp_id in _stub_ids else "done"
                manifest_items.append({"id": wp_id, "status": status})
    write_json(
        wps_dir / "wp_manifest.json",
        {"pass_name": "work_packages", "items": manifest_items},
    )

    if extra_phases:
        for p in extra_phases:
            write_json(
                phases_dir / f"P{p}_result.json",
                make_phase_result(p, name=f"Phase {p}"),
            )

    if extra_assignments:
        for p, a in extra_assignments:
            write_json(
                assigns_dir / f"P{p}-A{a}_result.json",
                make_assignment_result(p, a, name=f"Orphan assignment P{p}-A{a}"),
            )

    return tmp_path
