"""Tests for autoskillit.planner.consolidation.consolidate_wps — cycle-breaking via greedy FAS."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.planner._dag_ops import topological_sort
from autoskillit.planner.consolidation import consolidate_wps
from autoskillit.planner.validation import validate_plan
from tests.planner.conftest import (
    make_assignment_result,
    make_manifest,
    make_phase_result,
    make_refined_wps,
    make_wp_result,
    write_json,
)

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_rewrite_deps_breaks_cross_group_mutual_cycle(tmp_path: Path) -> None:
    wp_a1 = make_wp_result("P2-A4-WP1")
    wp_a2 = make_wp_result("P2-A4-WP2", depends_on=["P2-A9-WP1"])
    wp_b1 = make_wp_result("P2-A9-WP1")
    wp_b2 = make_wp_result("P2-A9-WP2", depends_on=["P2-A4-WP1"])
    refined_path = make_refined_wps(tmp_path, [wp_a1, wp_a2, wp_b1, wp_b2])

    consolidation_dir = tmp_path / "work_packages" / "consolidation"
    make_manifest(
        consolidation_dir,
        "P2-A4",
        [
            {
                "merged_id": "P2-A4-WP1",
                "source_wp_ids": ["P2-A4-WP1", "P2-A4-WP2"],
                "merge_order": ["P2-A4-WP1", "P2-A4-WP2"],
                "name": None,
                "goal": None,
            },
        ],
    )
    make_manifest(
        consolidation_dir,
        "P2-A9",
        [
            {
                "merged_id": "P2-A9-WP1",
                "source_wp_ids": ["P2-A9-WP1", "P2-A9-WP2"],
                "merge_order": ["P2-A9-WP1", "P2-A9-WP2"],
                "name": None,
                "goal": None,
            },
        ],
    )

    result = consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    output_ids = {wp["id"] for wp in consolidated["work_packages"]}
    assert output_ids == {"P2-A4-WP1", "P2-A9-WP1"}
    output_wps_dict = {wp["id"]: wp for wp in consolidated["work_packages"]}
    assert isinstance(topological_sort(output_wps_dict), list)

    assert "cycles_broken" in result
    assert int(result["cycles_broken"]) >= 1

    edges_path = tmp_path / "broken_cycle_edges.json"
    assert edges_path.exists()
    edges_data = json.loads(edges_path.read_text())
    assert len(edges_data) >= 1
    assert all(
        isinstance(e, list) and len(e) == 2 and all(isinstance(s, str) for s in e)
        for e in edges_data
    )


def test_rewrite_deps_breaks_three_node_cycle(tmp_path: Path) -> None:
    wp_a1 = make_wp_result("P1-A1-WP1")
    wp_a2 = make_wp_result("P1-A1-WP2", depends_on=["P1-A2-WP1"])
    wp_b1 = make_wp_result("P1-A2-WP1")
    wp_b2 = make_wp_result("P1-A2-WP2", depends_on=["P1-A3-WP1"])
    wp_c1 = make_wp_result("P1-A3-WP1")
    wp_c2 = make_wp_result("P1-A3-WP2", depends_on=["P1-A1-WP1"])
    refined_path = make_refined_wps(tmp_path, [wp_a1, wp_a2, wp_b1, wp_b2, wp_c1, wp_c2])

    consolidation_dir = tmp_path / "work_packages" / "consolidation"
    for phase_id, prefix in [
        ("P1", "P1-A1"),
        ("P2", "P1-A2"),
        ("P3", "P1-A3"),
    ]:
        make_manifest(
            consolidation_dir,
            phase_id,
            [
                {
                    "merged_id": f"{prefix}-WP1",
                    "source_wp_ids": [f"{prefix}-WP1", f"{prefix}-WP2"],
                    "merge_order": [f"{prefix}-WP1", f"{prefix}-WP2"],
                    "name": None,
                    "goal": None,
                },
            ],
        )

    result = consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    consolidated = json.loads((tmp_path / "consolidated_wps.json").read_text())
    output_ids = {wp["id"] for wp in consolidated["work_packages"]}
    assert output_ids == {"P1-A1-WP1", "P1-A2-WP1", "P1-A3-WP1"}
    output_wps_dict = {wp["id"]: wp for wp in consolidated["work_packages"]}
    assert isinstance(topological_sort(output_wps_dict), list)

    assert "cycles_broken" in result
    assert int(result["cycles_broken"]) >= 1

    edges_path = tmp_path / "broken_cycle_edges.json"
    assert edges_path.exists()
    edges_data = json.loads(edges_path.read_text())
    assert len(edges_data) >= 1
    assert all(
        isinstance(e, list) and len(e) == 2 and all(isinstance(s, str) for s in e)
        for e in edges_data
    )


def test_rewrite_deps_removes_self_references(tmp_path: Path) -> None:
    wp1 = make_wp_result("P1-A1-WP1")
    wp2 = make_wp_result("P1-A1-WP2", depends_on=["P1-A1-WP1"])
    wp3 = make_wp_result("P1-A1-WP3", depends_on=["P1-A1-WP2"])
    refined_path = make_refined_wps(tmp_path, [wp1, wp2, wp3])
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
    for wp in consolidated["work_packages"]:
        assert wp["id"] not in wp.get("depends_on", []), (
            f"Self-reference detected: {wp['id']} depends on itself"
        )


def test_consolidate_wps_noop_cycles_broken_zero_for_acyclic(tmp_path: Path) -> None:
    wps = [make_wp_result(f"P1-A1-WP{i}") for i in range(1, 4)]
    refined_path = make_refined_wps(tmp_path, wps)

    result = consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))

    assert result["cycles_broken"] == "0"
    assert not (tmp_path / "broken_cycle_edges.json").exists()


def test_consolidate_then_validate_passes_for_previously_cyclic_input(tmp_path: Path) -> None:
    wp_a1 = make_wp_result("P2-A4-WP1")
    wp_a2 = make_wp_result("P2-A4-WP2", depends_on=["P2-A9-WP1"])
    wp_b1 = make_wp_result("P2-A9-WP1")
    wp_b2 = make_wp_result("P2-A9-WP2", depends_on=["P2-A4-WP1"])
    refined_path = make_refined_wps(tmp_path, [wp_a1, wp_a2, wp_b1, wp_b2])

    phases_dir = tmp_path / "phases"
    assigns_dir = tmp_path / "assignments"
    wp_dir = tmp_path / "work_packages"

    write_json(phases_dir / "P2_result.json", make_phase_result(2))
    write_json(
        assigns_dir / "P2-A4_result.json",
        make_assignment_result(2, 4, proposed_work_packages=["P2-A4-WP1", "P2-A4-WP2"]),
    )
    write_json(
        assigns_dir / "P2-A9_result.json",
        make_assignment_result(2, 9, proposed_work_packages=["P2-A9-WP1", "P2-A9-WP2"]),
    )
    write_json(wp_dir / "P2-A4-WP1_result.json", wp_a1)
    write_json(wp_dir / "P2-A4-WP2_result.json", wp_a2)
    write_json(wp_dir / "P2-A9-WP1_result.json", wp_b1)
    write_json(wp_dir / "P2-A9-WP2_result.json", wp_b2)
    write_json(
        wp_dir / "wp_manifest.json",
        {
            "pass_name": "work_packages",
            "items": [
                {"id": "P2-A4-WP1", "status": "done"},
                {"id": "P2-A4-WP2", "status": "done"},
                {"id": "P2-A9-WP1", "status": "done"},
                {"id": "P2-A9-WP2", "status": "done"},
            ],
        },
    )
    consolidation_dir = wp_dir / "consolidation"
    make_manifest(
        consolidation_dir,
        "P2-A4",
        [
            {
                "merged_id": "P2-A4-WP1",
                "source_wp_ids": ["P2-A4-WP1", "P2-A4-WP2"],
                "merge_order": ["P2-A4-WP1", "P2-A4-WP2"],
                "name": None,
                "goal": None,
            },
        ],
    )
    make_manifest(
        consolidation_dir,
        "P2-A9",
        [
            {
                "merged_id": "P2-A9-WP1",
                "source_wp_ids": ["P2-A9-WP1", "P2-A9-WP2"],
                "merge_order": ["P2-A9-WP1", "P2-A9-WP2"],
                "name": None,
                "goal": None,
            },
        ],
    )

    consolidate_wps(refined_wps_path=str(refined_path), planner_dir=str(tmp_path))
    result = validate_plan(str(tmp_path))

    assert result["verdict"] == "pass"
