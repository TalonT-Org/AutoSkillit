"""Tests for validate_plan callable."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.planner.validation import validate_plan
from tests.planner.conftest import (
    make_assignment_result,
    make_phase_result,
    make_wp_result,
    write_json,
)

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


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
                write_json(
                    wps_dir / f"{wp_id}_result.json",
                    make_wp_result(wp_id, deliverables=deliverables, depends_on=deps),
                )

    manifest_items = []
    for p in range(1, num_phases + 1):
        for a in range(1, 2):
            for w in range(1, wps_per_assignment + 1):
                manifest_items.append({"id": f"P{p}-A{a}-WP{w}", "status": "done"})
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
    assert any(wp1 in f["message"] and wp2 in f["message"] for f in validation["findings"])


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
    assert any("P1-A1-WP1" in f["message"] for f in validation["findings"])


def test_validate_plan_dep_graph_backward_dep_injection(tmp_path: Path) -> None:
    _make_minimal_output_dir(tmp_path, wps_per_assignment=2)
    write_json(
        tmp_path / "dep_graph.json",
        {"added_backward_deps": {"P1-A1-WP2": ["P1-A1-WP1"]}, "forward_deps": {}},
    )
    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"


def test_validate_plan_dep_graph_creates_cycle_fails(tmp_path: Path) -> None:
    _make_minimal_output_dir(
        tmp_path, wps_per_assignment=2, depends_on_override={"P1-A1-WP1": ["P1-A1-WP2"]}
    )
    write_json(
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


# ---------------------------------------------------------------------------
# _check_duplicate_files_touched tests (T12–T14)
# ---------------------------------------------------------------------------


def test_check_duplicate_files_touched_detects_overlap() -> None:
    """T12: Two WPs touching the same file produce a finding."""
    from autoskillit.planner.validation import _check_duplicate_files_touched

    wp_results = {
        "P1-A1-WP1": {"files_touched": ["src/foo.py", "src/bar.py"]},
        "P2-A1-WP1": {"files_touched": ["src/foo.py", "src/baz.py"]},
    }
    findings = _check_duplicate_files_touched(wp_results)
    assert len(findings) == 1
    assert "src/foo.py" in findings[0]["message"]
    assert "P1-A1-WP1" in findings[0]["message"]
    assert "P2-A1-WP1" in findings[0]["message"]


def test_check_duplicate_files_touched_no_false_positives() -> None:
    """T13: WPs with disjoint files_touched produce no findings."""
    from autoskillit.planner.validation import _check_duplicate_files_touched

    wp_results = {
        "P1-A1-WP1": {"files_touched": ["src/foo.py"]},
        "P1-A1-WP2": {"files_touched": ["src/bar.py"]},
    }
    findings = _check_duplicate_files_touched(wp_results)
    assert findings == []


def test_validate_plan_includes_duplicate_files_touched(tmp_path: Path) -> None:
    """T14: validate_plan detects files_touched overlap as warning, not error."""
    _make_minimal_output_dir(tmp_path, wps_per_assignment=2)
    wp_dir = tmp_path / "work_packages"
    for wp_id in ("P1-A1-WP1", "P1-A1-WP2"):
        result_path = wp_dir / f"{wp_id}_result.json"
        data = json.loads(result_path.read_text())
        data["files_touched"] = ["src/shared.py"]
        result_path.write_text(json.dumps(data))

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert any("src/shared.py" in w["message"] for w in validation["warnings"])


# ---------------------------------------------------------------------------
# Severity-level tests (T15–T19)
# ---------------------------------------------------------------------------


def test_warning_severity_does_not_fail_verdict(tmp_path: Path) -> None:
    """T15: files_touched overlap is a warning, not an error — verdict stays pass."""
    _make_minimal_output_dir(tmp_path, wps_per_assignment=2)
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
    """T16: Error findings contain message, severity, and check fields."""
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
    """T17: Sizing violation (error) + files_touched overlap (warning) coexist."""
    _make_minimal_output_dir(tmp_path, wps_per_assignment=2, deliverables_override=[])
    wp_dir = tmp_path / "work_packages"
    for wp_id in ("P1-A1-WP1", "P1-A1-WP2"):
        result_path = wp_dir / f"{wp_id}_result.json"
        data = json.loads(result_path.read_text())
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
    """T18: validation.json uses schema_version 2 and includes warnings key."""
    _make_minimal_output_dir(tmp_path)
    validate_plan(str(tmp_path))
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert validation["schema_version"] == 2
    assert "warnings" in validation


def test_check_duplicate_files_touched_returns_structured_findings() -> None:
    """T19: _check_duplicate_files_touched returns dicts with message/severity/check."""
    from autoskillit.planner.validation import _check_duplicate_files_touched

    wp_results = {
        "P1-A1-WP1": {"files_touched": ["src/foo.py"]},
        "P2-A1-WP1": {"files_touched": ["src/foo.py"]},
    }
    findings = _check_duplicate_files_touched(wp_results)
    assert len(findings) == 1
    assert findings[0]["severity"] == "warning"
    assert findings[0]["check"] == "duplicate_files_touched"
    assert "message" in findings[0]
