"""Tests for compile_plan callable."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.planner.compiler import compile_plan

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _make_valid_output_dir(
    tmp_path: Path,
    *,
    num_phases: int = 1,
    with_dep_graph: bool = False,
    dependency_chain: bool = False,
) -> Path:
    """Build a valid output_dir with validation.json verdict=pass."""
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
        _write_json(
            assigns_dir / f"P{p}-A1_result.json",
            {
                "phase_number": p,
                "assignment_number": 1,
                "name": f"Test Assignment P{p}",
                "proposed_work_packages": [f"P{p}-A1-WP1"],
            },
        )
        deps: list[str] = []
        if dependency_chain and p > 1:
            deps = [f"P{p - 1}-A1-WP1"]
        _write_json(
            wps_dir / f"P{p}-A1-WP1_result.json",
            {
                "id": f"P{p}-A1-WP1",
                "name": f"WP P{p}-A1-WP1",
                "summary": f"Summary P{p}",
                "goal": f"Goal P{p}",
                "deliverables": [f"src/mod_p{p}.py"],
                "technical_steps": [f"step for p{p}"],
                "acceptance_criteria": [f"criterion for p{p}"],
                "depends_on": deps,
            },
        )

    manifest_items = [{"id": f"P{p}-A1-WP1", "status": "done"} for p in range(1, num_phases + 1)]
    _write_json(
        wps_dir / "wp_manifest.json", {"pass_name": "work_packages", "items": manifest_items}
    )

    _write_json(
        tmp_path / "validation.json", {"verdict": "pass", "findings": [], "schema_version": 1}
    )

    if with_dep_graph:
        _write_json(
            tmp_path / "dep_graph.json",
            {
                "added_backward_deps": {},
                "forward_deps": {"P1-A1-WP1": ["P1-A1-WP2"]},
            },
        )

    return tmp_path


def _make_chain_3_wps(tmp_path: Path) -> Path:
    """3 WPs: WP1 → WP2 → WP3."""
    phases_dir = tmp_path / "phases"
    assigns_dir = tmp_path / "assignments"
    wps_dir = tmp_path / "work_packages"

    _write_json(
        phases_dir / "P1_result.json",
        {
            "phase_number": 1,
            "name": "Foundation",
            "name_slug": "foundation",
            "assignments": ["P1-A1"],
        },
    )
    _write_json(
        assigns_dir / "P1-A1_result.json",
        {
            "phase_number": 1,
            "assignment_number": 1,
            "name": "Test Assignment",
            "proposed_work_packages": ["P1-A1-WP1", "P1-A1-WP2", "P1-A1-WP3"],
        },
    )
    for i, deps in [(1, []), (2, ["P1-A1-WP1"]), (3, ["P1-A1-WP2"])]:
        _write_json(
            wps_dir / f"P1-A1-WP{i}_result.json",
            {
                "id": f"P1-A1-WP{i}",
                "name": f"WP {i}",
                "summary": f"Summary {i}",
                "goal": f"Goal {i}",
                "deliverables": [f"src/mod{i}.py"],
                "technical_steps": [f"step {i}"],
                "acceptance_criteria": [f"criterion {i}"],
                "depends_on": deps,
            },
        )
    manifest_items = [{"id": f"P1-A1-WP{i}", "status": "done"} for i in range(1, 4)]
    _write_json(
        wps_dir / "wp_manifest.json", {"pass_name": "work_packages", "items": manifest_items}
    )
    _write_json(
        tmp_path / "validation.json", {"verdict": "pass", "findings": [], "schema_version": 1}
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_compile_plan_topological_sort_order(tmp_path: Path) -> None:
    _make_chain_3_wps(tmp_path)
    compile_plan(str(tmp_path), "test task", "/src")
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["execution_order"] == ["P1-A1-WP1", "P1-A1-WP2", "P1-A1-WP3"]


def test_compile_plan_issue_body_sections(tmp_path: Path) -> None:
    _make_valid_output_dir(tmp_path)
    compile_plan(str(tmp_path), "test task", "/src")
    issue = (tmp_path / "issues" / "P1-A1-WP1_issue.md").read_text()
    for section in [
        "## Goal",
        "## Context",
        "## Deliverables",
        "## Technical Steps",
        "## Acceptance Criteria",
    ]:
        assert section in issue


def test_compile_plan_issue_body_context_fields(tmp_path: Path) -> None:
    _make_valid_output_dir(tmp_path)
    compile_plan(str(tmp_path), "test task", "/src")
    issue = (tmp_path / "issues" / "P1-A1-WP1_issue.md").read_text()
    assert "Phase 1 (Milestone: 1-phase-1)" in issue
    assert "P1-A1 (Test Assignment P1)" in issue


def test_compile_plan_depends_on_cross_ref(tmp_path: Path) -> None:
    _make_chain_3_wps(tmp_path)
    compile_plan(str(tmp_path), "test task", "/src")
    issue_wp2 = (tmp_path / "issues" / "P1-A1-WP2_issue.md").read_text()
    assert "P1-A1-WP1" in issue_wp2


def test_compile_plan_depended_on_by_cross_ref(tmp_path: Path) -> None:
    _make_chain_3_wps(tmp_path)
    _write_json(
        tmp_path / "dep_graph.json",
        {
            "added_backward_deps": {},
            "forward_deps": {"P1-A1-WP1": ["P1-A1-WP2"]},
        },
    )
    compile_plan(str(tmp_path), "test task", "/src")
    issue_wp1 = (tmp_path / "issues" / "P1-A1-WP1_issue.md").read_text()
    assert "P1-A1-WP2" in issue_wp1


def test_compile_plan_milestones_one_per_phase(tmp_path: Path) -> None:
    _make_valid_output_dir(tmp_path, num_phases=2, dependency_chain=True)
    compile_plan(str(tmp_path), "test task", "/src")
    milestones_data = json.loads((tmp_path / "milestones.json").read_text())
    assert len(milestones_data["milestones"]) == 2
    for entry in milestones_data["milestones"]:
        assert "phase_number" in entry
        assert "name" in entry
        assert "name_slug" in entry


def test_compile_plan_plan_md_is_valid_markdown(tmp_path: Path) -> None:
    _make_valid_output_dir(tmp_path)
    result = compile_plan(str(tmp_path), "my task", "/src")
    plan_md = Path(result["plan_path"]).read_text()
    assert plan_md.startswith("#")
    assert "my task" in plan_md


def test_compile_plan_manifest_json_schema(tmp_path: Path) -> None:
    _make_valid_output_dir(tmp_path)
    compile_plan(str(tmp_path), "test task", "/src")
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert "task" in manifest
    assert "source_dir" in manifest
    assert "execution_order" in manifest
    assert "issues" in manifest
    assert isinstance(manifest["execution_order"], list)
    assert isinstance(manifest["issues"], dict)


def test_compile_plan_plan_parts_matches_issue_file_count(tmp_path: Path) -> None:
    _make_chain_3_wps(tmp_path)
    result = compile_plan(str(tmp_path), "test task", "/src")
    parts = [p for p in result["plan_parts"].split("\n") if p]
    assert len(parts) == 3
    for part_path in parts:
        assert Path(part_path).exists()


def test_compile_plan_return_values_are_strings(tmp_path: Path) -> None:
    _make_valid_output_dir(tmp_path)
    result = compile_plan(str(tmp_path), "test task", "/src")
    assert all(isinstance(v, str) for v in result.values())


def test_compile_plan_creates_issues_directory(tmp_path: Path) -> None:
    _make_valid_output_dir(tmp_path)
    compile_plan(str(tmp_path), "task", "/src")
    assert (tmp_path / "issues").is_dir()
