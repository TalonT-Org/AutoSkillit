"""Tests for autoskillit.planner.consolidation.consolidate_wps."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autoskillit.planner.compiler import compile_plan
from autoskillit.planner.consolidation import consolidate_wps
from autoskillit.planner.validation import validate_plan
from tests.planner.conftest import (
    make_assignment_result,
    make_phase_result,
    make_wp_result,
    write_json,
    write_task_file,
)

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def _make_refined_wps(tmp_path: Path, wps: list[dict[str, Any]]) -> Path:
    doc = {"task": "Test task", "source_dir": "/src", "work_packages": wps, "schema_version": 1}
    p = tmp_path / "refined_wps.json"
    write_json(p, doc)
    return p


def _make_manifest(consolidation_dir: Path, phase_id: str, groups: list[dict[str, Any]]) -> None:
    consolidation_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        consolidation_dir / f"{phase_id}_consolidation.json",
        {"phase_id": phase_id, "groups": groups},
    )


def test_passthrough_unchanged_when_no_manifests(tmp_path: Path) -> None:
    wps = [make_wp_result(f"P1-A1-WP{i}") for i in range(1, 4)]
    refined_path = _make_refined_wps(tmp_path, wps)

    result = consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    assert len(consolidated["work_packages"]) == 3
    assert {wp["id"] for wp in consolidated["work_packages"]} == {
        "P1-A1-WP1",
        "P1-A1-WP2",
        "P1-A1-WP3",
    }
    index = json.loads((tmp_path / "work_packages" / "wp_index.json").read_text())
    assert {e["id"] for e in index} == {"P1-A1-WP1", "P1-A1-WP2", "P1-A1-WP3"}
    # consolidate_wps returns str values (compatible with run_python result passing)
    assert result["total_count"] == "3"
    assert result["merged_count"] == "0"


def test_merge_union_fields(tmp_path: Path) -> None:
    wp1 = make_wp_result(
        "P1-A1-WP1",
        deliverables=["src/a.py"],
        acceptance_criteria=["criterion A"],
        files_touched=["src/a.py"],
        apis_defined=["api_a"],
        apis_consumed=["ext_a"],
    )
    wp2 = make_wp_result(
        "P1-A1-WP2",
        deliverables=["src/b.py"],
        acceptance_criteria=["criterion B"],
        files_touched=["src/b.py"],
        apis_defined=["api_b"],
        apis_consumed=["ext_b"],
    )
    refined_path = _make_refined_wps(tmp_path, [wp1, wp2])
    consolidation_dir = tmp_path / "work_packages" / "consolidation"
    _make_manifest(
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

    result = consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    assert len(consolidated["work_packages"]) == 1
    merged = consolidated["work_packages"][0]
    assert set(merged["deliverables"]) == {"src/a.py", "src/b.py"}
    assert set(merged["acceptance_criteria"]) == {"criterion A", "criterion B"}
    assert set(merged["files_touched"]) == {"src/a.py", "src/b.py"}
    assert set(merged["apis_defined"]) == {"api_a", "api_b"}
    assert set(merged["apis_consumed"]) == {"ext_a", "ext_b"}
    assert result["total_count"] == "1"
    assert result["merged_count"] == "1"


def test_merge_technical_steps_concatenated_in_merge_order(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1", technical_steps=["step A"])
    wp2 = make_wp_result("P1-A1-WP2", technical_steps=["step B"])
    refined_path = _make_refined_wps(tmp_path, [wp1, wp2])
    consolidation_dir = tmp_path / "work_packages" / "consolidation"
    _make_manifest(
        consolidation_dir,
        "P1",
        [
            {
                "merged_id": "P1-A1-WP1",
                "source_wp_ids": ["P1-A1-WP1", "P1-A1-WP2"],
                "merge_order": ["P1-A1-WP2", "P1-A1-WP1"],
                "name": None,
                "goal": None,
            }
        ],
    )

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    merged = consolidated["work_packages"][0]
    assert merged["technical_steps"] == ["step B", "step A"]


def test_merge_name_and_goal_from_primary(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1", name="Primary WP", goal="Primary goal")
    wp2 = make_wp_result("P1-A1-WP2", name="Secondary WP", goal="Secondary goal")
    refined_path = _make_refined_wps(tmp_path, [wp1, wp2])
    consolidation_dir = tmp_path / "work_packages" / "consolidation"
    _make_manifest(
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

    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    merged = consolidated["work_packages"][0]
    assert merged["name"] == "Primary WP"
    assert merged["goal"] == "Primary goal"


def test_merge_name_override_from_manifest(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1", name="Original WP1", goal="Original goal 1")
    wp2 = make_wp_result("P1-A1-WP2")
    refined_path = _make_refined_wps(tmp_path, [wp1, wp2])
    consolidation_dir = tmp_path / "work_packages" / "consolidation"
    _make_manifest(
        consolidation_dir,
        "P1",
        [
            {
                "merged_id": "P1-A1-WP1",
                "source_wp_ids": ["P1-A1-WP1", "P1-A1-WP2"],
                "merge_order": ["P1-A1-WP1", "P1-A1-WP2"],
                "name": "Combined file sharding refactor",
                "goal": "Refactor all file-sharding paths",
            }
        ],
    )

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    merged = consolidated["work_packages"][0]
    assert merged["name"] == "Combined file sharding refactor"
    assert merged["goal"] == "Refactor all file-sharding paths"


def test_dep_rewriting_intra_group_removed(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1", depends_on=["P1-A1-WP2"])
    wp2 = make_wp_result("P1-A1-WP2", depends_on=[])
    refined_path = _make_refined_wps(tmp_path, [wp1, wp2])
    consolidation_dir = tmp_path / "work_packages" / "consolidation"
    _make_manifest(
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

    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    merged = consolidated["work_packages"][0]
    assert merged["depends_on"] == []


def test_dep_rewriting_source_to_merged_id(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1")
    wp2 = make_wp_result("P1-A1-WP2")
    wp3 = make_wp_result("P1-A1-WP3", depends_on=["P1-A1-WP2"])
    refined_path = _make_refined_wps(tmp_path, [wp1, wp2, wp3])
    consolidation_dir = tmp_path / "work_packages" / "consolidation"
    _make_manifest(
        consolidation_dir,
        "P1",
        [
            {
                "merged_id": "P1-A1-WP1",
                "source_wp_ids": ["P1-A1-WP1", "P1-A1-WP2"],
                "merge_order": ["P1-A1-WP1", "P1-A1-WP2"],
                "name": None,
                "goal": None,
            },
            {
                "merged_id": "P1-A1-WP3",
                "source_wp_ids": ["P1-A1-WP3"],
                "merge_order": ["P1-A1-WP3"],
                "name": None,
                "goal": None,
            },
        ],
    )

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    wp3_out = next(wp for wp in consolidated["work_packages"] if wp["id"] == "P1-A1-WP3")
    assert wp3_out["depends_on"] == ["P1-A1-WP1"]


def test_external_dep_preserved(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1", depends_on=["P2-A1-WP1"])
    wp2 = make_wp_result("P1-A1-WP2")
    refined_path = _make_refined_wps(tmp_path, [wp1, wp2])
    consolidation_dir = tmp_path / "work_packages" / "consolidation"
    _make_manifest(
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

    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    merged = consolidated["work_packages"][0]
    assert "P2-A1-WP1" in merged["depends_on"]


def test_wp_index_rebuilt_with_merged_ids(tmp_path: Path) -> None:
    wps = [make_wp_result(f"P1-A1-WP{i}") for i in range(1, 4)]
    refined_path = _make_refined_wps(tmp_path, wps)
    consolidation_dir = tmp_path / "work_packages" / "consolidation"
    _make_manifest(
        consolidation_dir,
        "P1",
        [
            {
                "merged_id": "P1-A1-WP1",
                "source_wp_ids": ["P1-A1-WP1", "P1-A1-WP2"],
                "merge_order": ["P1-A1-WP1", "P1-A1-WP2"],
                "name": None,
                "goal": None,
            },
            {
                "merged_id": "P1-A1-WP3",
                "source_wp_ids": ["P1-A1-WP3"],
                "merge_order": ["P1-A1-WP3"],
                "name": None,
                "goal": None,
            },
        ],
    )

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    index = json.loads((tmp_path / "work_packages" / "wp_index.json").read_text())
    assert len(index) == 2
    merged_entry = next(e for e in index if e["id"] == "P1-A1-WP1")
    assert merged_entry["id"] == "P1-A1-WP1"
    assert "name" in merged_entry


def test_consolidated_wps_path_returned(tmp_path: Path) -> None:
    wps = [make_wp_result("P1-A1-WP1")]
    refined_path = _make_refined_wps(tmp_path, wps)

    result = consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    assert "consolidated_wps_path" in result
    out_path = Path(result["consolidated_wps_path"])
    assert out_path.exists()
    data = json.loads(out_path.read_text())
    assert "work_packages" in data


def test_multiple_phases_multiple_manifests(tmp_path: Path) -> None:
    wps_p1 = [make_wp_result(f"P1-A1-WP{i}") for i in range(1, 3)]
    wps_p2 = [make_wp_result(f"P2-A1-WP{i}") for i in range(1, 3)]
    wps_p3 = [make_wp_result(f"P3-A1-WP{i}") for i in range(1, 3)]
    all_wps = wps_p1 + wps_p2 + wps_p3
    refined_path = _make_refined_wps(tmp_path, all_wps)
    consolidation_dir = tmp_path / "work_packages" / "consolidation"
    for phase_id, prefix in [("P1", "P1-A1"), ("P2", "P2-A1"), ("P3", "P3-A1")]:
        _make_manifest(
            consolidation_dir,
            phase_id,
            [
                {
                    "merged_id": f"{prefix}-WP1",
                    "source_wp_ids": [f"{prefix}-WP1", f"{prefix}-WP2"],
                    "merge_order": [f"{prefix}-WP1", f"{prefix}-WP2"],
                    "name": None,
                    "goal": None,
                }
            ],
        )

    result = consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    assert len(consolidated["work_packages"]) == 3
    ids = {wp["id"] for wp in consolidated["work_packages"]}
    assert ids == {"P1-A1-WP1", "P2-A1-WP1", "P3-A1-WP1"}
    # Cross-phase deps must not be affected; absorbed source IDs must not appear
    absorbed_ids = {"P1-A1-WP2", "P2-A1-WP2", "P3-A1-WP2"}
    for wp in consolidated["work_packages"]:
        assert not any(dep in absorbed_ids for dep in wp["depends_on"])
    assert result["merged_count"] == "3"


def test_missing_source_wp_in_manifest_raises(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1")
    refined_path = _make_refined_wps(tmp_path, [wp1])
    consolidation_dir = tmp_path / "work_packages" / "consolidation"
    _make_manifest(
        consolidation_dir,
        "P1",
        [
            {
                "merged_id": "P1-A1-WP1",
                "source_wp_ids": ["P1-A1-WP1", "P1-A1-WP99"],
                "merge_order": ["P1-A1-WP1", "P1-A1-WP99"],
                "name": None,
                "goal": None,
            }
        ],
    )

    with pytest.raises(ValueError, match="unknown WP"):
        consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))


def test_consolidate_wps_rejects_merge_with_empty_deliverables(tmp_path: Path) -> None:
    """Merging WPs where all sources have empty deliverables must raise ValueError."""
    wps = [
        make_wp_result("P1-A1-WP1", allow_stub=True, deliverables=[]),
        make_wp_result("P1-A1-WP2", allow_stub=True, deliverables=[]),
    ]
    refined_path = _make_refined_wps(tmp_path, wps)
    consolidation_dir = tmp_path / "work_packages" / "consolidation"
    _make_manifest(
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

    with pytest.raises(ValueError, match="has 0 deliverables"):
        consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))


def test_fallback_merges_same_assignment_shared_files(tmp_path: Path) -> None:
    wps = [
        make_wp_result("P1-A1-WP1", files_touched=["src/config.yaml"]),
        make_wp_result("P1-A1-WP2", files_touched=["src/config.yaml"]),
        make_wp_result("P1-A1-WP3", files_touched=["src/config.yaml"]),
        make_wp_result("P1-A1-WP4", files_touched=["src/other.py"]),
        make_wp_result("P1-A1-WP5", files_touched=["src/other.py"]),
    ]
    refined_path = _make_refined_wps(tmp_path, wps)

    result = consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    output_ids = {wp["id"] for wp in consolidated["work_packages"]}
    assert output_ids == {"P1-A1-WP1", "P1-A1-WP4"}
    assert len(consolidated["work_packages"]) == 2
    assert result["merged_count"] == "2"


def test_fallback_skipped_when_manifest_has_merges(tmp_path: Path) -> None:
    wps = [
        make_wp_result("P1-A1-WP1", files_touched=["src/config.yaml"]),
        make_wp_result("P1-A1-WP2", files_touched=["src/config.yaml"]),
        make_wp_result("P1-A1-WP3", files_touched=["src/config.yaml"]),
        make_wp_result("P1-A1-WP4", files_touched=["src/other.py"]),
        make_wp_result("P1-A1-WP5", files_touched=["src/other.py"]),
    ]
    refined_path = _make_refined_wps(tmp_path, wps)
    consolidation_dir = tmp_path / "work_packages" / "consolidation"
    _make_manifest(
        consolidation_dir,
        "P1",
        [
            {
                "merged_id": "P1-A1-WP1",
                "source_wp_ids": ["P1-A1-WP1", "P1-A1-WP2"],
                "merge_order": ["P1-A1-WP1", "P1-A1-WP2"],
                "name": None,
                "goal": None,
            },
            {
                "merged_id": "P1-A1-WP3",
                "source_wp_ids": ["P1-A1-WP3"],
                "merge_order": ["P1-A1-WP3"],
                "name": None,
                "goal": None,
            },
            {
                "merged_id": "P1-A1-WP4",
                "source_wp_ids": ["P1-A1-WP4"],
                "merge_order": ["P1-A1-WP4"],
                "name": None,
                "goal": None,
            },
            {
                "merged_id": "P1-A1-WP5",
                "source_wp_ids": ["P1-A1-WP5"],
                "merge_order": ["P1-A1-WP5"],
                "name": None,
                "goal": None,
            },
        ],
    )

    result = consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    assert len(consolidated["work_packages"]) == 4
    assert result["merged_count"] == "1"


def test_fallback_skipped_when_wp_count_below_threshold(tmp_path: Path) -> None:
    wps = [
        make_wp_result("P1-A1-WP1", files_touched=["src/config.yaml"]),
        make_wp_result("P1-A1-WP2", files_touched=["src/config.yaml"]),
        make_wp_result("P1-A1-WP3", files_touched=["src/config.yaml"]),
    ]
    refined_path = _make_refined_wps(tmp_path, wps)

    result = consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    assert len(consolidated["work_packages"]) == 3
    assert result["merged_count"] == "0"


def test_fallback_respects_dependency_ordering(tmp_path: Path) -> None:
    wps = [
        make_wp_result("P1-A1-WP1", files_touched=["src/x.py"], depends_on=[]),
        make_wp_result("P1-A1-WP2", files_touched=["src/x.py"], depends_on=["P1-A1-WP1"]),
        make_wp_result("P1-A1-WP3", files_touched=["src/x.py"], depends_on=["P1-A1-WP2"]),
        make_wp_result("P1-A1-WP4", files_touched=["src/y.py"], depends_on=[]),
        make_wp_result("P1-A1-WP5", files_touched=["src/y.py"], depends_on=[]),
    ]
    refined_path = _make_refined_wps(tmp_path, wps)

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    output_ids = {wp["id"] for wp in consolidated["work_packages"]}
    assert "P1-A1-WP2" not in output_ids
    assert "P1-A1-WP3" not in output_ids
    merged_a = next(wp for wp in consolidated["work_packages"] if wp["id"] == "P1-A1-WP1")
    assert merged_a["id"] == "P1-A1-WP1"
    assert "P1-A1-WP2" not in merged_a["depends_on"]
    assert "P1-A1-WP3" not in merged_a["depends_on"]


def test_fallback_does_not_cross_assignment_boundary(tmp_path: Path) -> None:
    wps = [
        make_wp_result("P1-A1-WP1", files_touched=["src/shared.py"]),
        make_wp_result("P1-A1-WP2", files_touched=["src/shared.py"]),
        make_wp_result("P1-A1-WP3", files_touched=["src/shared.py"]),
        make_wp_result("P1-A2-WP1", files_touched=["src/shared.py"]),
        make_wp_result("P1-A2-WP2", files_touched=["src/shared.py"]),
        make_wp_result("P1-A2-WP3", files_touched=["src/shared.py"]),
    ]
    refined_path = _make_refined_wps(tmp_path, wps)

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    output_ids = {wp["id"] for wp in consolidated["work_packages"]}
    assert "P1-A1-WP1" in output_ids
    assert "P1-A2-WP1" in output_ids
    assert len(consolidated["work_packages"]) == 2


def test_fallback_caps_group_size(tmp_path: Path) -> None:
    wps = [make_wp_result(f"P1-A1-WP{i}", files_touched=["src/big.py"]) for i in range(1, 9)]
    refined_path = _make_refined_wps(tmp_path, wps)

    result = consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    assert len(consolidated["work_packages"]) == 2
    assert result["merged_count"] == "2"


# ---------------------------------------------------------------------------
# Write-back tests (T1-T7)
# ---------------------------------------------------------------------------


def test_writeback_overwrites_primary_result_file(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1", deliverables=["src/a.py"])
    wp2 = make_wp_result("P1-A1-WP2", deliverables=["src/b.py"])
    refined_path = _make_refined_wps(tmp_path, [wp1, wp2])
    wp_dir = tmp_path / "work_packages"
    write_json(wp_dir / "P1-A1-WP1_result.json", wp1)
    write_json(wp_dir / "P1-A1-WP2_result.json", wp2)
    consolidation_dir = wp_dir / "consolidation"
    _make_manifest(
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
    refined_path = _make_refined_wps(tmp_path, [wp1, wp2])
    wp_dir = tmp_path / "work_packages"
    write_json(wp_dir / "P1-A1-WP1_result.json", wp1)
    write_json(wp_dir / "P1-A1-WP2_result.json", wp2)
    consolidation_dir = wp_dir / "consolidation"
    _make_manifest(
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
    refined_path = _make_refined_wps(tmp_path, [wp1, wp2, wp3])
    wp_dir = tmp_path / "work_packages"
    write_json(wp_dir / "P1-A1-WP1_result.json", wp1)
    write_json(wp_dir / "P1-A1-WP2_result.json", wp2)
    write_json(wp_dir / "P1-A1-WP3_result.json", wp3)
    consolidation_dir = wp_dir / "consolidation"
    _make_manifest(
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
    refined_path = _make_refined_wps(tmp_path, wps)
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
    refined_path = _make_refined_wps(tmp_path, wps)
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
    refined_path = _make_refined_wps(tmp_path, [wp1, wp2, wp3])

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
    _make_manifest(
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


def test_consolidate_then_compile_emits_correct_issue_count(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1", depends_on=[])
    wp2 = make_wp_result("P1-A1-WP2", depends_on=[])
    wp3 = make_wp_result("P1-A1-WP3", depends_on=["P1-A1-WP1"])
    refined_path = _make_refined_wps(tmp_path, [wp1, wp2, wp3])

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
    _make_manifest(
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
    """Rebuilt wp_index.json must land in work_packages/, not planner root."""
    wps = [make_wp_result("P1-A1-WP1")]
    refined_path = _make_refined_wps(tmp_path, wps)

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    assert (tmp_path / "work_packages" / "wp_index.json").exists()
    assert not (tmp_path / "wp_index.json").exists()
