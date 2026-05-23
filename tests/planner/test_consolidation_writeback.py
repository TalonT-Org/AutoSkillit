"""Tests for autoskillit.planner.consolidation.consolidate_wps — write-back + pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.planner.compiler import compile_plan
from autoskillit.planner.consolidation import consolidate_wps
from autoskillit.planner.validation import validate_plan
from tests.planner.conftest import (
    make_assignment_result,
    make_manifest,
    make_phase_result,
    make_refined_wps,
    make_wp_result,
    write_json,
    write_task_file,
)

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_writeback_overwrites_primary_result_file(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1", deliverables=["src/a.py"])
    wp2 = make_wp_result("P1-A1-WP2", deliverables=["src/b.py"])
    refined_path = make_refined_wps(tmp_path, [wp1, wp2])
    wp_dir = tmp_path / "work_packages"
    write_json(wp_dir / "P1-A1-WP1_result.json", wp1)
    write_json(wp_dir / "P1-A1-WP2_result.json", wp2)
    consolidation_dir = wp_dir / "consolidation"
    make_manifest(
        consolidation_dir,
        "P1",
        [
            {
                "merged_id": "P1-A1-WP1",
                "source_wp_ids": ["P1-A1-WP1", "P1-A1-WP2"],
                "merge_order": ["P1-A1-WP1", "P1-A1-WP2"],
                "name": None,
                "goal": None,
            }
        ],
    )

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    result_file = wp_dir / "P1-A1-WP1_result.json"
    assert result_file.exists()
    merged = json.loads(result_file.read_text())
    assert set(merged["deliverables"]) == {"src/a.py", "src/b.py"}
    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    assert merged == consolidated["work_packages"][0]


def test_writeback_removes_absorbed_result_files(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1")
    wp2 = make_wp_result("P1-A1-WP2")
    refined_path = make_refined_wps(tmp_path, [wp1, wp2])
    wp_dir = tmp_path / "work_packages"
    write_json(wp_dir / "P1-A1-WP1_result.json", wp1)
    write_json(wp_dir / "P1-A1-WP2_result.json", wp2)
    consolidation_dir = wp_dir / "consolidation"
    make_manifest(
        consolidation_dir,
        "P1",
        [
            {
                "merged_id": "P1-A1-WP1",
                "source_wp_ids": ["P1-A1-WP1", "P1-A1-WP2"],
                "merge_order": ["P1-A1-WP1", "P1-A1-WP2"],
                "name": None,
                "goal": None,
            }
        ],
    )

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    assert not (wp_dir / "P1-A1-WP2_result.json").exists()
    assert (wp_dir / "P1-A1-WP1_result.json").exists()


def test_writeback_preserves_uninvolved_result_files(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1")
    wp2 = make_wp_result("P1-A1-WP2")
    wp3 = make_wp_result("P1-A1-WP3")
    refined_path = make_refined_wps(tmp_path, [wp1, wp2, wp3])
    wp_dir = tmp_path / "work_packages"
    write_json(wp_dir / "P1-A1-WP1_result.json", wp1)
    write_json(wp_dir / "P1-A1-WP2_result.json", wp2)
    write_json(wp_dir / "P1-A1-WP3_result.json", wp3)
    consolidation_dir = wp_dir / "consolidation"
    make_manifest(
        consolidation_dir,
        "P1",
        [
            {
                "merged_id": "P1-A1-WP1",
                "source_wp_ids": ["P1-A1-WP1", "P1-A1-WP2"],
                "merge_order": ["P1-A1-WP1", "P1-A1-WP2"],
                "name": None,
                "goal": None,
            }
        ],
    )

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    wp3_result = wp_dir / "P1-A1-WP3_result.json"
    assert wp3_result.exists()
    data = json.loads(wp3_result.read_text())
    assert data["id"] == "P1-A1-WP3"
    assert (wp_dir / "P1-A1-WP1_result.json").exists()
    assert not (wp_dir / "P1-A1-WP2_result.json").exists()


def test_writeback_noop_when_no_merges(tmp_path: Path) -> None:
    wps = [make_wp_result(f"P1-A1-WP{i}") for i in range(1, 4)]
    refined_path = make_refined_wps(tmp_path, wps)
    wp_dir = tmp_path / "work_packages"
    for wp in wps:
        write_json(wp_dir / f"{wp['id']}_result.json", wp)

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    for i in range(1, 4):
        assert (wp_dir / f"P1-A1-WP{i}_result.json").exists()


def test_writeback_fallback_merges_update_result_files(tmp_path: Path) -> None:
    wps = [
        make_wp_result("P1-A1-WP1", files_touched=["src/config.yaml"]),
        make_wp_result("P1-A1-WP2", files_touched=["src/config.yaml"]),
        make_wp_result("P1-A1-WP3", files_touched=["src/config.yaml"]),
        make_wp_result("P1-A1-WP4", files_touched=["src/other.py"]),
        make_wp_result("P1-A1-WP5", files_touched=["src/other.py"]),
    ]
    refined_path = make_refined_wps(tmp_path, wps)
    wp_dir = tmp_path / "work_packages"
    for wp in wps:
        write_json(wp_dir / f"{wp['id']}_result.json", wp)

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    assert (wp_dir / "P1-A1-WP1_result.json").exists()
    assert (wp_dir / "P1-A1-WP4_result.json").exists()
    assert not (wp_dir / "P1-A1-WP2_result.json").exists()
    assert not (wp_dir / "P1-A1-WP3_result.json").exists()
    assert not (wp_dir / "P1-A1-WP5_result.json").exists()
    merged = json.loads((wp_dir / "P1-A1-WP1_result.json").read_text())
    assert "src/config.yaml" in merged["files_touched"]


def test_consolidate_then_validate_sees_merged_wps(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1", depends_on=[])
    wp2 = make_wp_result("P1-A1-WP2", depends_on=[])
    wp3 = make_wp_result("P1-A1-WP3", depends_on=["P1-A1-WP1"])
    refined_path = make_refined_wps(tmp_path, [wp1, wp2, wp3])

    phases_dir = tmp_path / "phases"
    assigns_dir = tmp_path / "assignments"
    wp_dir = tmp_path / "work_packages"

    write_json(phases_dir / "P1_result.json", make_phase_result(1))
    write_json(
        assigns_dir / "P1-A1_result.json",
        make_assignment_result(
            1, 1, proposed_work_packages=["P1-A1-WP1", "P1-A1-WP2", "P1-A1-WP3"]
        ),
    )
    write_json(wp_dir / "P1-A1-WP1_result.json", wp1)
    write_json(wp_dir / "P1-A1-WP2_result.json", wp2)
    write_json(wp_dir / "P1-A1-WP3_result.json", wp3)
    write_json(
        wp_dir / "wp_manifest.json",
        {
            "pass_name": "work_packages",
            "items": [
                {"id": "P1-A1-WP1", "status": "done"},
                {"id": "P1-A1-WP2", "status": "done"},
                {"id": "P1-A1-WP3", "status": "done"},
            ],
        },
    )
    consolidation_dir = wp_dir / "consolidation"
    make_manifest(
        consolidation_dir,
        "P1",
        [
            {
                "merged_id": "P1-A1-WP1",
                "source_wp_ids": ["P1-A1-WP1", "P1-A1-WP2"],
                "merge_order": ["P1-A1-WP1", "P1-A1-WP2"],
                "name": None,
                "goal": None,
            }
        ],
    )

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))
    result = validate_plan(str(tmp_path))

    assert result["verdict"] == "pass"
    assert result["issue_count"] == "0"


def test_consolidate_wps_writes_lifecycle_registry(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1", deliverables=["src/a.py"])
    wp2 = make_wp_result("P1-A2-WP1", deliverables=["src/b.py"])
    refined_path = make_refined_wps(tmp_path, [wp1, wp2])

    phases_dir = tmp_path / "phases"
    assigns_dir = tmp_path / "assignments"
    wp_dir = tmp_path / "work_packages"

    write_json(phases_dir / "P1_result.json", make_phase_result(1))
    write_json(
        assigns_dir / "P1-A1_result.json",
        make_assignment_result(1, 1, proposed_work_packages=["P1-A1-WP1"]),
    )
    write_json(
        assigns_dir / "P1-A2_result.json",
        make_assignment_result(1, 2, proposed_work_packages=["P1-A2-WP1"]),
    )
    write_json(wp_dir / "P1-A1-WP1_result.json", wp1)
    write_json(wp_dir / "P1-A2-WP1_result.json", wp2)
    consolidation_dir = wp_dir / "consolidation"
    make_manifest(
        consolidation_dir,
        "P1",
        [
            {
                "merged_id": "P1-A1-WP1",
                "source_wp_ids": ["P1-A1-WP1", "P1-A2-WP1"],
                "merge_order": ["P1-A1-WP1", "P1-A2-WP1"],
                "name": None,
                "goal": None,
            }
        ],
    )

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    registry_path = wp_dir / "lifecycle_registry.json"
    assert registry_path.exists(), "lifecycle_registry.json must be written"
    registry = json.loads(registry_path.read_text())
    assert "absorbed" in registry
    assert "P1-A2-WP1" in registry["absorbed"]
    assert registry["absorbed"]["P1-A2-WP1"]["merged_into"] == "P1-A1-WP1"


def test_consolidate_then_validate_cross_assignment_absorption(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1", deliverables=["src/a.py"])
    wp2 = make_wp_result("P1-A2-WP1", deliverables=["src/b.py"])
    refined_path = make_refined_wps(tmp_path, [wp1, wp2])

    phases_dir = tmp_path / "phases"
    assigns_dir = tmp_path / "assignments"
    wp_dir = tmp_path / "work_packages"

    write_json(phases_dir / "P1_result.json", make_phase_result(1))
    write_json(
        assigns_dir / "P1-A1_result.json",
        make_assignment_result(1, 1, proposed_work_packages=["P1-A1-WP1"]),
    )
    write_json(
        assigns_dir / "P1-A2_result.json",
        make_assignment_result(1, 2, proposed_work_packages=["P1-A2-WP1"]),
    )
    write_json(wp_dir / "P1-A1-WP1_result.json", wp1)
    write_json(wp_dir / "P1-A2-WP1_result.json", wp2)
    write_json(
        wp_dir / "wp_manifest.json",
        {
            "pass_name": "work_packages",
            "items": [{"id": "P1-A1-WP1", "status": "done"}],
        },
    )
    consolidation_dir = wp_dir / "consolidation"
    make_manifest(
        consolidation_dir,
        "P1",
        [
            {
                "merged_id": "P1-A1-WP1",
                "source_wp_ids": ["P1-A1-WP1", "P1-A2-WP1"],
                "merge_order": ["P1-A1-WP1", "P1-A2-WP1"],
                "name": None,
                "goal": None,
            }
        ],
    )

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))
    result = validate_plan(str(tmp_path))

    assert result["verdict"] == "pass", f"Expected pass but got: {result}"
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert not any(
        f["check"] == "assignment_completeness" and "P1-A2" in f["message"]
        for f in validation["findings"]
    ), "Assignment P1-A2 should be exempt due to cross-assignment absorption"
    assert not any(
        f["check"] == "assignment_completeness" and "P1-A2" in f["message"]
        for f in validation.get("warnings", [])
    ), "Assignment P1-A2 should not appear as a warning either"


def test_consolidate_wps_writes_lifecycle_registry_not_absorption(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1", deliverables=["src/a.py"])
    wp2 = make_wp_result("P1-A1-WP2", deliverables=["src/b.py"])
    refined_path = make_refined_wps(tmp_path, [wp1, wp2])

    consolidation_dir = tmp_path / "work_packages" / "consolidation"
    make_manifest(
        consolidation_dir,
        "P1",
        [
            {
                "merged_id": "P1-A1-WP1",
                "source_wp_ids": ["P1-A1-WP1", "P1-A1-WP2"],
                "merge_order": ["P1-A1-WP1", "P1-A1-WP2"],
                "name": None,
                "goal": None,
            }
        ],
    )

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    lifecycle_path = tmp_path / "work_packages" / "lifecycle_registry.json"
    absorption_path = tmp_path / "work_packages" / "absorption_registry.json"

    assert lifecycle_path.exists()
    assert not absorption_path.exists()

    registry = json.loads(lifecycle_path.read_text())
    assert "P1-A1-WP2" in registry["absorbed"]
    assert registry["absorbed"]["P1-A1-WP2"]["merged_into"] == "P1-A1-WP1"


def test_consolidate_then_compile_emits_correct_issue_count(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1", depends_on=[])
    wp2 = make_wp_result("P1-A1-WP2", depends_on=[])
    wp3 = make_wp_result("P1-A1-WP3", depends_on=["P1-A1-WP1"])
    refined_path = make_refined_wps(tmp_path, [wp1, wp2, wp3])

    phases_dir = tmp_path / "phases"
    assigns_dir = tmp_path / "assignments"
    wp_dir = tmp_path / "work_packages"

    write_json(phases_dir / "P1_result.json", make_phase_result(1))
    write_json(
        assigns_dir / "P1-A1_result.json",
        make_assignment_result(
            1, 1, proposed_work_packages=["P1-A1-WP1", "P1-A1-WP2", "P1-A1-WP3"]
        ),
    )
    write_json(wp_dir / "P1-A1-WP1_result.json", wp1)
    write_json(wp_dir / "P1-A1-WP2_result.json", wp2)
    write_json(wp_dir / "P1-A1-WP3_result.json", wp3)
    write_json(
        wp_dir / "wp_manifest.json",
        {
            "pass_name": "work_packages",
            "items": [
                {"id": "P1-A1-WP1", "status": "done"},
                {"id": "P1-A1-WP2", "status": "done"},
                {"id": "P1-A1-WP3", "status": "done"},
            ],
        },
    )
    consolidation_dir = wp_dir / "consolidation"
    make_manifest(
        consolidation_dir,
        "P1",
        [
            {
                "merged_id": "P1-A1-WP1",
                "source_wp_ids": ["P1-A1-WP1", "P1-A1-WP2"],
                "merge_order": ["P1-A1-WP1", "P1-A1-WP2"],
                "name": None,
                "goal": None,
            }
        ],
    )

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    task_file_path = write_task_file(tmp_path)
    write_json(
        tmp_path / "validation.json", {"verdict": "pass", "findings": [], "schema_version": 2}
    )
    compile_plan(str(tmp_path), task_file_path, "/src")

    issues_dir = tmp_path / "issues"
    issue_files = sorted(issues_dir.glob("*_issue.md"))
    assert len(issue_files) == 2
    issue_names = {f.name for f in issue_files}
    assert "P1-A1-WP1_issue.md" in issue_names
    assert "P1-A1-WP3_issue.md" in issue_names
    assert "P1-A1-WP2_issue.md" not in issue_names

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert len(manifest["execution_order"]) == 2

    merged_issue = (issues_dir / "P1-A1-WP1_issue.md").read_text()
    assert "src/mod_P1-A1-WP1.py" in merged_issue
    assert "src/mod_P1-A1-WP2.py" in merged_issue


def test_consolidate_wps_wp_index_in_work_packages(tmp_path: Path) -> None:
    wps = [make_wp_result("P1-A1-WP1")]
    refined_path = make_refined_wps(tmp_path, wps)

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    assert (tmp_path / "work_packages" / "wp_index.json").exists()
    assert not (tmp_path / "wp_index.json").exists()
