"""Tests for autoskillit.planner.validation — core validate_plan happy/fail paths and severity model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.planner.schema import DELIVERABLE_BOUNDS
from autoskillit.planner.validation import validate_plan
from tests.planner.conftest import (
    make_minimal_output_dir,
    write_json,
)

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_validate_plan_valid_returns_pass(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path)
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    assert result["issue_count"] == "0"


def test_validate_plan_cyclic_dep_fails(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path, wps_per_assignment=2)
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
    assert any(wp1 in f["message"] and wp2 in f["message"] for f in validation["findings"])


def test_validate_plan_missing_dep_ref_fails(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path, depends_on_override={"P1-A1-WP1": ["P1-A1-WP99"]})
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"


def test_validate_plan_phase_no_assignments_fails(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path, extra_phases=[2])
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"


def test_validate_plan_assignment_no_wps_fails(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path, extra_assignments=[(1, 2)])
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"


def test_validate_plan_wp_zero_deliverables_fails(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path)
    wp_path = tmp_path / "work_packages" / "P1-A1-WP1_result.json"
    raw = json.loads(wp_path.read_text())
    raw["deliverables"] = []
    wp_path.write_text(json.dumps(raw))
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"


def test_validate_plan_wp_too_many_deliverables_warns_not_fails(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path)
    wp_path = tmp_path / "work_packages" / "P1-A1-WP1_result.json"
    raw = json.loads(wp_path.read_text())
    _, hi = DELIVERABLE_BOUNDS
    raw["deliverables"] = [f"f{i}.py" for i in range(hi + 1)]
    wp_path.write_text(json.dumps(raw))
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    assert int(result["warning_count"]) == 1
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert validation["warnings"][0]["check"] == "sizing_bounds"


def test_validate_plan_duplicate_deliverables_fails(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path, wps_per_assignment=2, deliverables_override=["src/foo.py"])
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"


def test_validate_plan_failed_wp_flagged_but_not_sole_fail_cause(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path)
    manifest_path = tmp_path / "work_packages" / "wp_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["items"][0]["status"] = "failed"
    manifest_path.write_text(json.dumps(manifest))

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert any("P1-A1-WP1" in f["message"] for f in validation["findings"])


def test_validate_plan_dep_graph_backward_dep_injection(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path, wps_per_assignment=2)
    write_json(
        tmp_path / "dep_graph.json",
        {"added_backward_deps": {"P1-A1-WP2": ["P1-A1-WP1"]}, "forward_deps": {}},
    )
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"


def test_validate_plan_dep_graph_creates_cycle_fails(tmp_path: Path) -> None:
    make_minimal_output_dir(
        tmp_path, wps_per_assignment=2, depends_on_override={"P1-A1-WP1": ["P1-A1-WP2"]}
    )
    write_json(
        tmp_path / "dep_graph.json",
        {"added_backward_deps": {"P1-A1-WP2": ["P1-A1-WP1"]}, "forward_deps": {}},
    )
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"


def test_validate_plan_writes_validation_json(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path)
    result = validate_plan(str(tmp_path))
    validation_path = tmp_path / "validation.json"
    assert validation_path.exists()
    data = json.loads(validation_path.read_text())
    assert "verdict" in data
    assert result["validation_path"] == str(validation_path)


def test_validate_plan_return_values_are_strings(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path)
    result = validate_plan(str(tmp_path))
    assert all(isinstance(v, str) for v in result.values())


def test_warning_severity_does_not_fail_verdict(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path, wps_per_assignment=2)
    wp_dir = tmp_path / "work_packages"
    for wp_id in ("P1-A1-WP1", "P1-A1-WP2"):
        result_path = wp_dir / f"{wp_id}_result.json"
        data = json.loads(result_path.read_text())
        data["files_touched"] = ["src/shared.py"]
        result_path.write_text(json.dumps(data))

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    assert result["issue_count"] == "0"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert len(validation["findings"]) == 0
    assert len(validation["warnings"]) == 1
    assert validation["warnings"][0]["severity"] == "warning"
    assert validation["warnings"][0]["check"] == "duplicate_files_touched"
    assert "src/shared.py" in validation["warnings"][0]["message"]


def test_error_findings_have_structured_fields(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path, wps_per_assignment=2)
    wp1 = "P1-A1-WP1"
    wp2 = "P1-A1-WP2"
    wp_dir = tmp_path / "work_packages"
    data1 = json.loads((wp_dir / f"{wp1}_result.json").read_text())
    data1["depends_on"] = [wp2]
    (wp_dir / f"{wp1}_result.json").write_text(json.dumps(data1))
    data2 = json.loads((wp_dir / f"{wp2}_result.json").read_text())
    data2["depends_on"] = [wp1]
    (wp_dir / f"{wp2}_result.json").write_text(json.dumps(data2))

    validate_plan(str(tmp_path))
    validation = json.loads((tmp_path / "validation.json").read_text())
    for finding in validation["findings"]:
        assert "message" in finding
        assert "severity" in finding
        assert "check" in finding
        assert finding["severity"] == "error"
    cycle_findings = [f for f in validation["findings"] if f["check"] == "dag_acyclic"]
    assert len(cycle_findings) == 1


def test_mixed_errors_and_warnings(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path, wps_per_assignment=2)
    wp_dir = tmp_path / "work_packages"
    for wp_id in ("P1-A1-WP1", "P1-A1-WP2"):
        result_path = wp_dir / f"{wp_id}_result.json"
        data = json.loads(result_path.read_text())
        data["deliverables"] = []
        data["files_touched"] = ["src/shared.py"]
        result_path.write_text(json.dumps(data))

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"
    validation = json.loads((tmp_path / "validation.json").read_text())
    error_findings = [f for f in validation["findings"] if f["severity"] == "error"]
    assert len(error_findings) >= 1
    assert len(validation["warnings"]) == 1
    assert validation["warnings"][0]["severity"] == "warning"


def test_validation_json_schema_version_2(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path)
    validate_plan(str(tmp_path))
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert validation["schema_version"] == 2
    assert "warnings" in validation


def test_stub_through_validation_produces_failed_wps_finding(tmp_path: Path) -> None:
    """Test 1.3: End-to-end stub-through-validation produces failed_wps finding."""
    from autoskillit.planner.manifests import finalize_wp_manifest
    from tests.planner.conftest import (
        make_assignment_result,
        make_phase_result,
        make_wp_result,
    )

    phases_dir = tmp_path / "phases"
    assigns_dir = tmp_path / "assignments"
    wp_dir = tmp_path / "work_packages"

    write_json(phases_dir / "P1_result.json", make_phase_result(1))
    write_json(assigns_dir / "P1-A1_result.json", make_assignment_result(1, 1))
    write_json(wp_dir / "P1-A1-WP1_result.json", make_wp_result("P1-A1-WP1"))
    write_json(
        wp_dir / "P1-A1-WP2_result.json",
        make_wp_result(
            "P1-A1-WP2",
            allow_stub=True,
            elaboration_failed=True,
            deliverables=[],
            technical_steps=[],
            acceptance_criteria=[],
        ),
    )

    finalize_wp_manifest(str(wp_dir), str(tmp_path))
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"

    validation = json.loads((tmp_path / "validation.json").read_text())
    checks = {f["check"] for f in validation["findings"]}
    assert "failed_wps" in checks
    assert "sizing_bounds" in checks


def test_validate_plan_ignores_archived_stubs(tmp_path: Path) -> None:
    """Test 2.2: validate_plan does not see archived stubs."""
    make_minimal_output_dir(tmp_path)

    archived_dir = tmp_path / "work_packages" / "archived"
    archived_dir.mkdir()
    write_json(
        archived_dir / "P1-A2-WP1_result.json",
        {
            "id": "P1-A2-WP1",
            "name": "Archived stub",
            "elaboration_failed": True,
            "deliverables": [],
        },
    )

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    validation = json.loads((tmp_path / "validation.json").read_text())
    all_messages = " ".join(f["message"] for f in validation["findings"])
    assert "P1-A2-WP1" not in all_messages
