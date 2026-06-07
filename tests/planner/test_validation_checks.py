"""Tests for autoskillit.planner.validation — individual _check_* function unit tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.planner.schema import DELIVERABLE_BOUNDS
from autoskillit.planner.validation import (
    _check_dep_id_format,
    _check_duplicate_deliverables,
    _check_duplicate_files_touched,
    _check_failed_wps,
    _check_sizing_bounds,
    _check_stub_consistency,
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


def test_check_duplicate_files_touched_detects_overlap() -> None:
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
    wp_results = {
        "P1-A1-WP1": {"files_touched": ["src/foo.py"]},
        "P1-A1-WP2": {"files_touched": ["src/bar.py"]},
    }
    findings = _check_duplicate_files_touched(wp_results)
    assert findings == []


def test_validate_plan_includes_duplicate_files_touched(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path, wps_per_assignment=2)
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


def test_check_duplicate_files_touched_returns_structured_findings() -> None:
    wp_results = {
        "P1-A1-WP1": {"files_touched": ["src/foo.py"]},
        "P2-A1-WP1": {"files_touched": ["src/foo.py"]},
    }
    findings = _check_duplicate_files_touched(wp_results)
    assert len(findings) == 1
    assert findings[0]["severity"] == "warning"
    assert findings[0]["check"] == "duplicate_files_touched"
    assert "message" in findings[0]


def test_version_bump_step_pyproject(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path)
    wp_dir = tmp_path / "work_packages"
    data = json.loads((wp_dir / "P1-A1-WP1_result.json").read_text())
    data["technical_steps"] = ["Edit pyproject.toml version field to X.Y.Z"]
    (wp_dir / "P1-A1-WP1_result.json").write_text(json.dumps(data))

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert validation["findings"] == []
    assert any(w["check"] == "version_bump_step" for w in validation["warnings"])


def test_version_bump_step_sync_versions_task(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path)
    wp_dir = tmp_path / "work_packages"
    data = json.loads((wp_dir / "P1-A1-WP1_result.json").read_text())
    data["technical_steps"] = ["Run task sync-versions"]
    (wp_dir / "P1-A1-WP1_result.json").write_text(json.dumps(data))

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert any(w["check"] == "version_bump_step" for w in validation["warnings"])


def test_version_bump_step_sync_versions_py(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path)
    wp_dir = tmp_path / "work_packages"
    data = json.loads((wp_dir / "P1-A1-WP1_result.json").read_text())
    data["technical_steps"] = ["python3 scripts/sync_versions.py"]
    (wp_dir / "P1-A1-WP1_result.json").write_text(json.dumps(data))

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert any(w["check"] == "version_bump_step" for w in validation["warnings"])


def test_version_bump_step_no_match(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path)
    wp_dir = tmp_path / "work_packages"
    data = json.loads((wp_dir / "P1-A1-WP1_result.json").read_text())
    data["technical_steps"] = ["Refactor the API handler"]
    (wp_dir / "P1-A1-WP1_result.json").write_text(json.dumps(data))

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert not any(w["check"] == "version_bump_step" for w in validation["warnings"])


def test_version_bump_step_in_name(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path)
    wp_dir = tmp_path / "work_packages"
    data = json.loads((wp_dir / "P1-A1-WP1_result.json").read_text())
    data["name"] = "WP3: Version Bump"
    data["technical_steps"] = []
    (wp_dir / "P1-A1-WP1_result.json").write_text(json.dumps(data))

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert any(w["check"] == "version_bump_step" for w in validation["warnings"])


def test_version_bump_step_no_technical_steps_key(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path)
    wp_dir = tmp_path / "work_packages"
    data = json.loads((wp_dir / "P1-A1-WP1_result.json").read_text())
    data.pop("technical_steps", None)
    (wp_dir / "P1-A1-WP1_result.json").write_text(json.dumps(data))

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert not any(w["check"] == "version_bump_step" for w in validation["warnings"])


def test_version_bump_step_only_flagged_wp(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path, wps_per_assignment=2)
    wp_dir = tmp_path / "work_packages"
    data1 = json.loads((wp_dir / "P1-A1-WP1_result.json").read_text())
    data1["technical_steps"] = ["Edit pyproject.toml version field to 1.2.3"]
    (wp_dir / "P1-A1-WP1_result.json").write_text(json.dumps(data1))
    data2 = json.loads((wp_dir / "P1-A1-WP2_result.json").read_text())
    data2["technical_steps"] = ["Implement the feature"]
    (wp_dir / "P1-A1-WP2_result.json").write_text(json.dumps(data2))

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    validation = json.loads((tmp_path / "validation.json").read_text())
    vb_warnings = [w for w in validation["warnings"] if w["check"] == "version_bump_step"]
    assert len(vb_warnings) == 1
    assert "P1-A1-WP1" in vb_warnings[0]["message"]


def test_version_bump_step_coexists_with_duplicate_files_touched(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path, wps_per_assignment=2)
    wp_dir = tmp_path / "work_packages"
    data1 = json.loads((wp_dir / "P1-A1-WP1_result.json").read_text())
    data1["technical_steps"] = ["Edit pyproject.toml version field to 1.2.3"]
    data1["files_touched"] = ["src/shared.py"]
    (wp_dir / "P1-A1-WP1_result.json").write_text(json.dumps(data1))
    data2 = json.loads((wp_dir / "P1-A1-WP2_result.json").read_text())
    data2["files_touched"] = ["src/shared.py"]
    (wp_dir / "P1-A1-WP2_result.json").write_text(json.dumps(data2))

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    validation = json.loads((tmp_path / "validation.json").read_text())
    checks = {w["check"] for w in validation["warnings"]}
    assert "version_bump_step" in checks
    assert "duplicate_files_touched" in checks
    assert len(validation["findings"]) == 0


def test_version_bump_step_via_summary(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path)
    wp_dir = tmp_path / "work_packages"
    data = json.loads((wp_dir / "P1-A1-WP1_result.json").read_text())
    data["summary"] = "Perform a version-bump before releasing."
    data["technical_steps"] = []
    (wp_dir / "P1-A1-WP1_result.json").write_text(json.dumps(data))

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert any(w["check"] == "version_bump_step" for w in validation["warnings"])


def test_validate_plan_warns_on_phase_sentinel_in_assignments_dir(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path)
    sentinel = {"id": "P1", "status": "complete", "assignment_count": 1, "failed_count": 0}
    write_json(tmp_path / "assignments" / "P1_result.json", sentinel)

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert any(
        w["check"] == "file_discovery_miss" and w["severity"] == "warning"
        for w in validation["warnings"]
    )


@pytest.mark.parametrize(
    "bad_dep,reason",
    [
        ("P1-A1", "assignment-level ID in WP depends_on"),
        ("P1", "phase-level ID in WP depends_on"),
        ("WP1", "bare WP ID without phase/assignment prefix"),
    ],
)
def test_check_dep_id_format_rejects_malformed(bad_dep, reason, tmp_path: Path) -> None:
    wp_results = {
        "P1-A1-WP1": {"id": "P1-A1-WP1", "depends_on": [bad_dep]},
        "P1-A1-WP2": {"id": "P1-A1-WP2", "depends_on": []},
    }
    findings = _check_dep_id_format(wp_results)
    assert any(f["severity"] == "error" and bad_dep in f["message"] for f in findings), (
        f"Expected error for malformed dep {bad_dep!r}: {reason}"
    )


def test_validate_plan_warns_on_non_wp_result_file_in_work_packages_dir(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path)
    sentinel = {"id": "P1", "status": "complete", "assignment_count": 1, "failed_count": 0}
    write_json(tmp_path / "work_packages" / "P1_result.json", sentinel)

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert any(
        w["check"] == "file_discovery_miss" and w["severity"] == "warning"
        for w in validation["warnings"]
    )


def test_validate_plan_emits_discovery_miss_warning_for_non_matching_files(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path, num_phases=1, wps_per_assignment=1)
    stray = {"id": "stray", "status": "complete", "assignment_count": 1, "failed_count": 0}
    write_json(tmp_path / "work_packages" / "stray_result.json", stray)

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert any(
        w["check"] == "file_discovery_miss" and w["severity"] == "warning"
        for w in validation["warnings"]
    ), "Expected a file_discovery_miss warning for stray_result.json"


@pytest.mark.parametrize(
    ("count", "expected_findings", "expected_severity"),
    [
        (0, 1, "error"),
        (DELIVERABLE_BOUNDS[0], 0, None),
        (5, 0, None),
        (DELIVERABLE_BOUNDS[1] + 1, 1, "warning"),
    ],
)
def test_check_sizing_bounds_boundary_values(
    count: int, expected_findings: int, expected_severity: str | None
) -> None:
    wp_results = {
        "P1-A1-WP1": {"deliverables": [f"f{i}.py" for i in range(count)]},
    }
    findings = _check_sizing_bounds(wp_results)
    assert len(findings) == expected_findings
    if expected_findings:
        assert findings[0]["check"] == "sizing_bounds"
        assert findings[0]["severity"] == expected_severity


def test_check_sizing_bounds_uses_deliverable_bounds_constant() -> None:
    lo, hi = DELIVERABLE_BOUNDS
    wp_at_lo = {"P1-A1-WP1": {"deliverables": [f"f{i}.py" for i in range(lo)]}}
    wp_at_hi = {"P1-A1-WP2": {"deliverables": [f"f{i}.py" for i in range(hi)]}}
    assert _check_sizing_bounds(wp_at_lo) == []
    assert _check_sizing_bounds(wp_at_hi) == []


def test_check_sizing_bounds_upper_violation_is_warning() -> None:
    _, hi = DELIVERABLE_BOUNDS
    wp_results = {
        "P1-A1-WP1": {"deliverables": [f"f{i}.py" for i in range(hi + 1)]},
    }
    findings = _check_sizing_bounds(wp_results)
    assert len(findings) == 1
    assert findings[0]["severity"] == "warning"
    assert findings[0]["check"] == "sizing_bounds"


def test_check_duplicate_deliverables_detects_shared() -> None:
    wp_results = {
        "P1-A1-WP1": {"deliverables": ["src/shared.py", "src/a.py"]},
        "P1-A1-WP2": {"deliverables": ["src/shared.py", "src/b.py"]},
    }
    findings = _check_duplicate_deliverables(wp_results)
    assert len(findings) == 1
    assert findings[0]["check"] == "duplicate_deliverables"
    assert "src/shared.py" in findings[0]["message"]


def test_check_duplicate_deliverables_no_false_positives() -> None:
    wp_results = {
        "P1-A1-WP1": {"deliverables": ["src/a.py"]},
        "P1-A1-WP2": {"deliverables": ["src/b.py"]},
    }
    findings = _check_duplicate_deliverables(wp_results)
    assert findings == []


def test_check_duplicate_deliverables_returns_structured_findings() -> None:
    wp_results = {
        "P1-A1-WP1": {"deliverables": ["src/shared.py"]},
        "P1-A1-WP2": {"deliverables": ["src/shared.py"]},
    }
    findings = _check_duplicate_deliverables(wp_results)
    assert len(findings) == 1
    f = findings[0]
    assert "message" in f
    assert f["severity"] == "error"
    assert f["check"] == "duplicate_deliverables"


def test_deduplication_orphan_cascade(tmp_path: Path) -> None:
    make_minimal_output_dir(tmp_path, wps_per_assignment=2)
    wp_dir = tmp_path / "work_packages"
    wp_b = json.loads((wp_dir / "P1-A1-WP2_result.json").read_text())
    wp_b["deliverables"] = []
    (wp_dir / "P1-A1-WP2_result.json").write_text(json.dumps(wp_b))

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "fail"
    validation = json.loads((tmp_path / "validation.json").read_text())
    sizing_findings = [f for f in validation["findings"] if f["check"] == "sizing_bounds"]
    assert len(sizing_findings) == 1
    assert "P1-A1-WP2" in sizing_findings[0]["message"]


def test_validate_plan_reads_finalized_manifest(tmp_path: Path) -> None:
    from autoskillit.planner import finalize_wp_manifest

    make_minimal_output_dir(tmp_path)
    wp_dir = tmp_path / "work_packages"
    finalize_wp_manifest(str(wp_dir), str(tmp_path))

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"


def test_check_dep_references_skips_voided_wp_ids() -> None:
    """Deps to voided WPs are not flagged as dangling references."""
    from autoskillit.planner.validation import _check_dep_references

    wp_results = {
        "P1-A1-WP1": {"depends_on": ["P1-A2-WP1"]},
    }
    lifecycle_registry = {
        "voided_wps": {"P1-A2-WP1": {"merged_into": "P1-A1-WP1", "reason": "subsumed"}},
    }
    findings = _check_dep_references(wp_results, lifecycle_registry)
    assert len(findings) == 0


def test_check_dep_references_still_flags_truly_dangling() -> None:
    """Deps to unknown WPs not in voided_wps are still flagged."""
    from autoskillit.planner.validation import _check_dep_references

    wp_results = {
        "P1-A1-WP1": {"depends_on": ["P1-A2-WP1", "P1-A3-WP1"]},
    }
    lifecycle_registry = {
        "voided_wps": {"P1-A2-WP1": {"merged_into": "P1-A1-WP1", "reason": "subsumed"}},
    }
    findings = _check_dep_references(wp_results, lifecycle_registry)
    assert len(findings) == 1
    assert "P1-A3-WP1" in findings[0]["message"]


def test_check_assignment_completeness_all_wps_voided(tmp_path: Path) -> None:
    """Assignments with all WPs voided are exempt from completeness check."""
    from autoskillit.planner.validation import _check_assignment_completeness

    assignment_results = {
        "P1-A1": {"phase_number": 1, "assignment_number": 1},
        "P1-A2": {"phase_number": 1, "assignment_number": 2},
    }
    wp_results = {
        "P1-A1-WP1": {"id": "P1-A1-WP1", "depends_on": []},
    }
    lifecycle_registry = {
        "voided_assignments": [],
        "absorbed": {},
        "voided_wps": {"P1-A2-WP1": {"merged_into": "P1-A1-WP1", "reason": "subsumed"}},
    }
    findings = _check_assignment_completeness(assignment_results, wp_results, lifecycle_registry)
    assert len(findings) == 0


def test_validate_plan_with_voided_wps_passes(tmp_path: Path) -> None:
    """Full validate_plan passes when voided WPs are properly registered."""
    from autoskillit.planner.lifecycle import LifecycleCategory, record_lifecycle_event

    # Create minimal output structure using proper helpers
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir(parents=True)
    write_json(phases_dir / "P1_result.json", make_phase_result(1))

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir(parents=True)
    write_json(assignments_dir / "P1-A1_result.json", make_assignment_result(1, 1))
    write_json(assignments_dir / "P1-A2_result.json", make_assignment_result(1, 2))

    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir(parents=True)
    write_json(
        wp_dir / "P1-A1-WP1_result.json", make_wp_result("P1-A1-WP1", deliverables=["src/a.py"])
    )

    # Void the second WP
    record_lifecycle_event(
        tmp_path,
        LifecycleCategory.VOIDED_WPS,
        {"P1-A2-WP1": {"merged_into": "P1-A1-WP1", "reason": "subsumed"}},
    )
    # Delete its result file
    wp2_file = tmp_path / "work_packages" / "P1-A2-WP1_result.json"
    if wp2_file.exists():
        wp2_file.unlink()

    from autoskillit.planner.validation import validate_plan

    result = validate_plan(str(tmp_path))
    assert result["verdict"] == "pass"


def test_check_failed_wps_detects_elaboration_failed_status() -> None:
    """Test 1.2: _check_failed_wps must detect elaboration_failed status."""
    wp_manifest = {
        "items": [
            {"id": "P1-A1-WP1", "status": "done"},
            {"id": "P1-A1-WP2", "status": "elaboration_failed"},
        ]
    }
    findings = _check_failed_wps(wp_manifest)
    assert len(findings) == 1
    assert findings[0]["check"] == "failed_wps"
    assert "P1-A1-WP2" in findings[0]["message"]
    assert "'elaboration_failed'" in findings[0]["message"]


def test_check_stub_consistency_catches_manifest_disk_mismatch() -> None:
    """Test 3.1: _check_stub_consistency catches manifest-content mismatch."""
    wp_results = {
        "P1-A1-WP1": {"id": "P1-A1-WP1", "elaboration_failed": True},
    }
    wp_manifest = {"items": [{"id": "P1-A1-WP1", "status": "done"}]}
    findings = _check_stub_consistency(wp_results, wp_manifest)
    assert len(findings) == 1
    assert findings[0]["check"] == "stub_consistency"
    assert findings[0]["severity"] == "error"
    assert (
        findings[0]["message"]
        == "WP P1-A1-WP1 has status 'done' but elaboration_failed in content"
    )


def test_check_stub_consistency_no_false_positive_when_status_correct() -> None:
    """No finding when manifest correctly has elaboration_failed status."""
    wp_results = {
        "P1-A1-WP1": {"id": "P1-A1-WP1", "elaboration_failed": True},
    }
    wp_manifest = {"items": [{"id": "P1-A1-WP1", "status": "elaboration_failed"}]}
    findings = _check_stub_consistency(wp_results, wp_manifest)
    assert findings == []
