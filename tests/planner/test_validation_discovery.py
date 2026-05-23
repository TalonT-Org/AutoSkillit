"""Tests for autoskillit.planner.validation — discover_tier_files, _load_* tier loaders, DAG decomposition, lifecycle registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.planner.schema import PHASE_RESULT_FILE_RE, WP_RESULT_FILE_RE
from autoskillit.planner.validation import (
    DiscoveryResult,
    _check_assignment_completeness,
    _check_dag_acyclic,
    _check_phase_completeness,
    _load_assignment_results,
    _load_phase_results,
    _load_wp_results,
    discover_tier_files,
    validate_plan,
)
from tests.planner.conftest import (
    make_assignment_result,
    make_minimal_output_dir,
    make_phase_result,
    make_wp_result,
    write_json,
)

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_discover_tier_files_returns_accepted_and_rejected(tmp_path: Path) -> None:
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir(parents=True)
    write_json(phases_dir / "P1_result.json", make_phase_result(1))
    stray = {"id": "stray", "status": "complete"}
    write_json(phases_dir / "stray_result.json", stray)

    result = discover_tier_files(phases_dir, PHASE_RESULT_FILE_RE)
    assert isinstance(result, DiscoveryResult)
    assert len(result.accepted) == 1
    assert result.accepted[0].name == "P1_result.json"
    assert len(result.rejected) == 1
    assert result.rejected[0].name == "stray_result.json"


def test_validate_plan_exempts_absorbed_assignments(tmp_path: Path) -> None:
    phases_dir = tmp_path / "phases"
    assigns_dir = tmp_path / "assignments"
    wps_dir = tmp_path / "work_packages"

    write_json(phases_dir / "P1_result.json", make_phase_result(1))
    write_json(
        assigns_dir / "P1-A1_result.json",
        make_assignment_result(1, 1, proposed_work_packages=["P1-A1-WP1"]),
    )
    write_json(
        assigns_dir / "P1-A2_result.json",
        make_assignment_result(1, 2, proposed_work_packages=["P1-A2-WP1"]),
    )
    write_json(
        wps_dir / "P1-A1-WP1_result.json",
        make_wp_result("P1-A1-WP1", deliverables=["src/a.py"]),
    )
    write_json(
        wps_dir / "wp_manifest.json",
        {"pass_name": "work_packages", "items": [{"id": "P1-A1-WP1", "status": "done"}]},
    )
    registry = {
        "schema_version": 1,
        "absorbed": {"P1-A2-WP1": {"merged_into": "P1-A1-WP1", "group_id": "P1-A1-WP1"}},
    }
    write_json(wps_dir / "absorption_registry.json", registry)

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert not any(
        f["check"] == "assignment_completeness" and "P1-A2" in f["message"]
        for f in validation["findings"]
    ), "Assignment P1-A2 should be exempt due to absorption registry"
    assert not any(
        f["check"] == "assignment_completeness" and "P1-A2" in f["message"]
        for f in validation.get("warnings", [])
    ), "Assignment P1-A2 should not appear as a warning either"


def test_discover_tier_files_places_non_canonical_in_rejected(tmp_path: Path) -> None:
    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir()
    write_json(wp_dir / "P1-A1-WP1_result.json", make_wp_result("P1-A1-WP1"))
    write_json(wp_dir / "P1-A1-WP2a_result.json", {"id": "P1-A1-WP2a", "name": "bad"})

    result = discover_tier_files(wp_dir, WP_RESULT_FILE_RE)
    assert [f.name for f in result.accepted] == ["P1-A1-WP1_result.json"]
    assert [f.name for f in result.rejected] == ["P1-A1-WP2a_result.json"]


def test_load_wp_results_returns_non_canonical_in_rejected(tmp_path: Path) -> None:
    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir()
    write_json(wp_dir / "P1-A1-WP1_result.json", make_wp_result("P1-A1-WP1"))
    write_json(wp_dir / "P1-A1-WP2a_result.json", {"id": "P1-A1-WP2a", "name": "bad"})

    results, rejected = _load_wp_results(tmp_path)
    assert "P1-A1-WP1" in results
    assert len(rejected) == 1
    assert rejected[0].name == "P1-A1-WP2a_result.json"


def test_load_phase_results_returns_non_canonical_in_rejected(tmp_path: Path) -> None:
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    write_json(phases_dir / "P1_result.json", make_phase_result(1))
    write_json(phases_dir / "Phase1_result.json", {"id": "P1", "name": "bad"})

    results, rejected = _load_phase_results(tmp_path)
    assert "P1" in results
    assert len(rejected) == 1
    assert rejected[0].name == "Phase1_result.json"


def test_load_assignment_results_returns_non_canonical_in_rejected(tmp_path: Path) -> None:
    assigns_dir = tmp_path / "assignments"
    assigns_dir.mkdir()
    write_json(
        assigns_dir / "P1-A1_result.json",
        make_assignment_result(1, 1, name="Good"),
    )
    write_json(
        assigns_dir / "P1-A2b_result.json", {"id": "P1-A2b", "phase_id": "P1", "name": "bad"}
    )

    results, rejected = _load_assignment_results(tmp_path)
    assert "P1-A1" in results
    assert len(rejected) == 1
    assert rejected[0].name == "P1-A2b_result.json"


def test_validate_plan_emits_warning_for_non_canonical_wp_file(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path)
    wp_dir = tmp_path / "work_packages"
    write_json(wp_dir / "P1-A1-WP2a_result.json", {"id": "P1-A1-WP2a", "name": "bad"})

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert any(
        w["check"] == "file_discovery_miss" and "P1-A1-WP2a_result.json" in w["message"]
        for w in validation["warnings"]
    ), "Expected file_discovery_miss warning for P1-A1-WP2a_result.json"


def test_check_dag_acyclic_decomposes_2_node_cycle() -> None:
    wp_results = {
        "P1-A1-WP1": {"depends_on": ["P1-A1-WP2"]},
        "P1-A1-WP2": {"depends_on": ["P1-A1-WP1"]},
        "P1-A1-WP3": {"depends_on": []},
    }
    findings = _check_dag_acyclic(wp_results)
    assert len(findings) == 1
    assert findings[0]["cycle_size"] == 2
    assert set(findings[0]["cycle_nodes"]) == {"P1-A1-WP1", "P1-A1-WP2"}
    assert set(map(tuple, findings[0]["cycle_edges"])) == {
        ("P1-A1-WP1", "P1-A1-WP2"),
        ("P1-A1-WP2", "P1-A1-WP1"),
    }


def test_check_dag_acyclic_3_node_cycle_no_edges() -> None:
    wp_results = {
        "P1-A1-WP1": {"depends_on": ["P1-A1-WP3"]},
        "P1-A1-WP2": {"depends_on": ["P1-A1-WP1"]},
        "P1-A1-WP3": {"depends_on": ["P1-A1-WP2"]},
    }
    findings = _check_dag_acyclic(wp_results)
    assert len(findings) == 1
    assert findings[0]["cycle_size"] == 3
    assert set(findings[0]["cycle_nodes"]) == {"P1-A1-WP1", "P1-A1-WP2", "P1-A1-WP3"}
    assert "cycle_edges" not in findings[0]


def test_check_assignment_completeness_skips_voided_assignments() -> None:
    assignment_results = {
        "P4-A2": {"phase_number": 4, "assignment_number": 2},
        "P4-A1": {"phase_number": 4, "assignment_number": 1},
        "P4-A3": {"phase_number": 4, "assignment_number": 3},
    }
    wp_results = {
        "P4-A1-WP1": {"id": "P4-A1-WP1", "depends_on": []},
    }
    lifecycle_registry = {
        "voided_assignments": ["P4-A2"],
        "voided_phases": [],
        "absorbed": {},
    }
    findings = _check_assignment_completeness(assignment_results, wp_results, lifecycle_registry)
    assert not any("P4-A2" in f["message"] for f in findings)
    assert any("P4-A3" in f["message"] for f in findings)


def test_check_phase_completeness_skips_voided_phases() -> None:
    phase_results = {
        "P3": {"phase_number": 3},
        "P1": {"phase_number": 1},
        "P2": {"phase_number": 2},
    }
    assignment_results = {
        "P1-A1": {"phase_number": 1, "assignment_number": 1},
    }
    lifecycle_registry = {
        "voided_phases": ["P3"],
        "voided_assignments": [],
        "absorbed": {},
    }
    findings = _check_phase_completeness(phase_results, assignment_results, lifecycle_registry)
    assert not any("P3" in f["message"] for f in findings)
    assert any("P2" in f["message"] for f in findings)


def test_validate_plan_voided_assignment_no_false_positive(tmp_path: Path) -> None:
    from autoskillit.planner.merge import merge_refined_assignments

    phases_dir = tmp_path / "phases"
    assigns_dir = tmp_path / "assignments"
    wps_dir = tmp_path / "work_packages"

    write_json(phases_dir / "P1_result.json", make_phase_result(1))
    write_json(
        assigns_dir / "P1-A1_result.json",
        make_assignment_result(1, 1, proposed_work_packages=["P1-A1-WP1"]),
    )
    write_json(
        assigns_dir / "P1-A2_result.json",
        make_assignment_result(1, 2, proposed_work_packages=["P1-A2-WP1"]),
    )
    write_json(
        wps_dir / "P1-A1-WP1_result.json",
        make_wp_result("P1-A1-WP1", deliverables=["src/a.py"]),
    )
    write_json(
        wps_dir / "wp_manifest.json",
        {"pass_name": "work_packages", "items": [{"id": "P1-A1-WP1", "status": "done"}]},
    )

    ctx_dir = tmp_path / "refine_contexts"
    ctx_dir.mkdir()
    voided_assignment = {
        "id": "P1-A2",
        "phase_id": "P1",
        "name": "Voided",
        "goal": "g",
        "technical_approach": "",
        "proposed_work_packages": [],
    }
    normal_assignment = {
        "id": "P1-A1",
        "phase_id": "P1",
        "name": "Normal",
        "goal": "g",
        "technical_approach": "",
        "proposed_work_packages": [
            {
                "id": "P1-A1-WP1",
                "name": "WP1",
                "summary": "s",
                "goal": "g",
                "technical_steps": [],
                "files_touched": ["src/a.py"],
                "apis_defined": [],
                "apis_consumed": [],
                "depends_on": [],
                "deliverables": ["src/a.py"],
                "acceptance_criteria": [],
            }
        ],
    }
    write_json(
        ctx_dir / "P1_result.json",
        {"schema_version": 1, "assignments": [normal_assignment, voided_assignment]},
    )

    merge_refined_assignments(planner_dir=str(tmp_path))
    result = validate_plan(str(tmp_path))

    assert result["verdict"] == "pass"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert not any(
        f["check"] == "assignment_completeness" and "P1-A2" in f["message"]
        for f in validation["findings"]
    )


def test_validate_plan_backward_compat_absorption_registry(tmp_path: Path) -> None:
    phases_dir = tmp_path / "phases"
    assigns_dir = tmp_path / "assignments"
    wps_dir = tmp_path / "work_packages"

    write_json(phases_dir / "P1_result.json", make_phase_result(1))
    write_json(
        assigns_dir / "P1-A1_result.json",
        make_assignment_result(1, 1, proposed_work_packages=["P1-A1-WP1"]),
    )
    write_json(
        assigns_dir / "P1-A2_result.json",
        make_assignment_result(1, 2, proposed_work_packages=["P1-A2-WP1"]),
    )
    write_json(
        wps_dir / "P1-A1-WP1_result.json",
        make_wp_result("P1-A1-WP1", deliverables=["src/a.py"]),
    )
    write_json(
        wps_dir / "wp_manifest.json",
        {"pass_name": "work_packages", "items": [{"id": "P1-A1-WP1", "status": "done"}]},
    )
    registry = {
        "schema_version": 1,
        "absorbed": {"P1-A2-WP1": {"merged_into": "P1-A1-WP1", "group_id": "P1-A1-WP1"}},
    }
    write_json(wps_dir / "absorption_registry.json", registry)

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    assert not any(
        "P1-A2" in f.get("message", "")
        for f in json.loads((tmp_path / "validation.json").read_text())["findings"]
    )
