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


def make_wp_result(wp_id: str, **overrides: Any) -> dict[str, Any]:
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
    return validate_wp_result(data)


def write_json(path: Path, data: object) -> None:
    """Write ``data`` as JSON to ``path``, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
