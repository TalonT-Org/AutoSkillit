"""Tests for validate_plan callable."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.planner.validation import validate_plan

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _make_minimal_output_dir(
    tmp_path: Path,
    *,
    num_phases: int = 1,
    wps_per_assignment: int = 1,
    deliverables_override: list[str] | None = None,
    depends_on_override: dict[str, list[str]] | None = None,
    extra_phases: list[int] | None = None,
    extra_assignments: list[tuple[int, int]] | None = None,
) -> Path:
    """Build a minimal valid output_dir structure."""
    phases_dir = tmp_path / "phases"
    assigns_dir = tmp_path / "assignments"
    wps_dir = tmp_path / "work_packages"

    for p in range(1, num_phases + 1):
        _write_json(
            phases_dir / f"P{p}_result.json",
            {
                "phase_number": p,
                "name": f"Phase {p}",
                "name_slug": f"phase-{p}",
                "assignments": [f"P{p}-A1"],
            },
        )

    for p in range(1, num_phases + 1):
        for a in range(1, 2):
            _write_json(
                assigns_dir / f"P{p}-A{a}_result.json",
                {
                    "phase_number": p,
                    "assignment_number": a,
                    "name": f"Assignment P{p}-A{a}",
                    "proposed_work_packages": [
                        f"P{p}-A{a}-WP{w}" for w in range(1, wps_per_assignment + 1)
                    ],
                },
            )

    for p in range(1, num_phases + 1):
        for a in range(1, 2):
            for w in range(1, wps_per_assignment + 1):
                wp_id = f"P{p}-A{a}-WP{w}"
                deliverables = (
                    deliverables_override
                    if deliverables_override is not None
                    else [f"src/mod_{wp_id}.py"]
                )
                deps = (depends_on_override or {}).get(wp_id, [])
                _write_json(
                    wps_dir / f"{wp_id}_result.json",
                    {
                        "id": wp_id,
                        "name": f"WP {wp_id}",
                        "summary": "summary",
                        "goal": "goal",
                        "deliverables": deliverables,
                        "technical_steps": ["step 1"],
                        "acceptance_criteria": ["criterion 1"],
                        "depends_on": deps,
                    },
                )

    manifest_items = []
    for p in range(1, num_phases + 1):
        for a in range(1, 2):
            for w in range(1, wps_per_assignment + 1):
                manifest_items.append({"id": f"P{p}-A{a}-WP{w}", "status": "done"})
    _write_json(
        wps_dir / "wp_manifest.json",
        {"pass_name": "work_packages", "items": manifest_items},
    )

    if extra_phases:
        for p in extra_phases:
            _write_json(
                phases_dir / f"P{p}_result.json",
                {
                    "phase_number": p,
                    "name": f"Phase {p}",
                    "name_slug": f"phase-{p}",
                    "assignments": [],
                },
            )

    if extra_assignments:
        for p, a in extra_assignments:
            _write_json(
                assigns_dir / f"P{p}-A{a}_result.json",
                {
                    "phase_number": p,
                    "assignment_number": a,
                    "name": f"Orphan assignment P{p}-A{a}",
                    "proposed_work_packages": [],
                },
            )

    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_validate_plan_valid_returns_pass(tmp_path: Path) -> None:
    _make_minimal_output_dir(tmp_path)
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    assert result["issue_count"] == "0"


def test_validate_plan_cyclic_dep_fails(tmp_path: Path) -> None:
    _make_minimal_output_dir(tmp_path, wps_per_assignment=2)
    wp1 = "P1-A1-WP1"
    wp2 = "P1-A1-WP2"
    wp_dir = tmp_path / "work_packages"
    data1 = json.loads((wp_dir / f"{wp1}_result.json").read_text())
    data1["depends_on"] = [wp2]
    (wp_dir / f"{wp1}_result.json").write_text(json.dumps(data1))
    data2 = json.loads((wp_dir / f"{wp2}_result.json").read_text())
    data2["depends_on"] = [wp1]
    (wp_dir / f"{wp2}_result.json").write_text(json.dumps(data2))

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert any(wp1 in f and wp2 in f for f in validation["findings"])


def test_validate_plan_missing_dep_ref_fails(tmp_path: Path) -> None:
    _make_minimal_output_dir(tmp_path, depends_on_override={"P1-A1-WP1": ["P1-A1-WP99"]})
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"


def test_validate_plan_phase_no_assignments_fails(tmp_path: Path) -> None:
    _make_minimal_output_dir(tmp_path, extra_phases=[2])
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"


def test_validate_plan_assignment_no_wps_fails(tmp_path: Path) -> None:
    _make_minimal_output_dir(tmp_path, extra_assignments=[(1, 2)])
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"


def test_validate_plan_wp_zero_deliverables_fails(tmp_path: Path) -> None:
    _make_minimal_output_dir(tmp_path, deliverables_override=[])
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"


def test_validate_plan_wp_too_many_deliverables_fails(tmp_path: Path) -> None:
    _make_minimal_output_dir(tmp_path, deliverables_override=["a", "b", "c", "d", "e", "f"])
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"


def test_validate_plan_duplicate_deliverables_fails(tmp_path: Path) -> None:
    _make_minimal_output_dir(tmp_path, wps_per_assignment=2, deliverables_override=["src/foo.py"])
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"


def test_validate_plan_failed_wp_flagged_but_not_sole_fail_cause(tmp_path: Path) -> None:
    _make_minimal_output_dir(tmp_path)
    manifest_path = tmp_path / "work_packages" / "wp_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["items"][0]["status"] = "failed"
    manifest_path.write_text(json.dumps(manifest))

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert any("P1-A1-WP1" in f for f in validation["findings"])


def test_validate_plan_dep_graph_backward_dep_injection(tmp_path: Path) -> None:
    _make_minimal_output_dir(tmp_path, wps_per_assignment=2)
    _write_json(
        tmp_path / "dep_graph.json",
        {"added_backward_deps": {"P1-A1-WP2": ["P1-A1-WP1"]}, "forward_deps": {}},
    )
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"


def test_validate_plan_dep_graph_creates_cycle_fails(tmp_path: Path) -> None:
    _make_minimal_output_dir(
        tmp_path, wps_per_assignment=2, depends_on_override={"P1-A1-WP1": ["P1-A1-WP2"]}
    )
    _write_json(
        tmp_path / "dep_graph.json",
        {"added_backward_deps": {"P1-A1-WP2": ["P1-A1-WP1"]}, "forward_deps": {}},
    )
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"


def test_validate_plan_writes_validation_json(tmp_path: Path) -> None:
    _make_minimal_output_dir(tmp_path)
    result = validate_plan(str(tmp_path))
    validation_path = tmp_path / "validation.json"
    assert validation_path.exists()
    data = json.loads(validation_path.read_text())
    assert "verdict" in data
    assert result["validation_path"] == str(validation_path)


def test_validate_plan_return_values_are_strings(tmp_path: Path) -> None:
    _make_minimal_output_dir(tmp_path)
    result = validate_plan(str(tmp_path))
    assert all(isinstance(v, str) for v in result.values())
