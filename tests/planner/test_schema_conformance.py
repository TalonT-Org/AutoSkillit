"""Schema conformance tests: SKILL.md-compliant data flows correctly through the pipeline.

Tests 1a–1d document that SKILL.md field names are accepted and normalized at the
validation boundary. Test 1f is the end-to-end integration backstop.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.planner.validation import _load_assignment_results, _load_phase_results

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# 1a: SKILL.md-compliant phase data accepted and normalized by _load_phase_results
# ---------------------------------------------------------------------------


def test_skill_compliant_phase_data_accepted_and_normalized(tmp_path: Path) -> None:
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    _write_json(
        phases_dir / "P1_result.json",
        {
            "id": "P1",
            "name": "Phase One",
            "ordering": 1,
            "goal": "Build the foundation",
            "scope": ["core"],
            "relationship_notes": "",
            "assignments_preview": ["Schema design", "Implementation"],
        },
    )

    results = _load_phase_results(tmp_path)

    assert "P1" in results
    assert results["P1"]["phase_number"] == 1
    assert results["P1"]["name_slug"] == "phase-one"
    assert results["P1"]["assignments"] == [
        {"name": "Schema design", "metadata": {}},
        {"name": "Implementation", "metadata": {}},
    ]


# ---------------------------------------------------------------------------
# 1b: SKILL.md-compliant assignment data accepted and normalized
# ---------------------------------------------------------------------------


def test_skill_compliant_assignment_data_accepted_and_normalized(tmp_path: Path) -> None:
    assign_dir = tmp_path / "assignments"
    assign_dir.mkdir()
    _write_json(
        assign_dir / "P1-A2_result.json",
        {
            "id": "P1-A2",
            "name": "Second assignment",
            "phase_id": "P1",
            "goal": "Design schema",
            "technical_approach": "Use TypedDict",
            "proposed_work_packages": [],
        },
    )

    results = _load_assignment_results(tmp_path)

    assert "P1-A2" in results
    assert results["P1-A2"]["phase_number"] == 1
    assert results["P1-A2"]["assignment_number"] == 2


# ---------------------------------------------------------------------------
# 1c: build_assignment_manifest with SKILL.md field names produces correct items
# ---------------------------------------------------------------------------


def test_build_assignment_manifest_with_skill_md_field_names(tmp_path: Path) -> None:
    from autoskillit.planner import build_assignment_manifest

    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    _write_json(
        phases_dir / "P1_result.json",
        {
            "id": "P1",
            "name": "Phase One",
            "ordering": 1,
            "goal": "test",
            "scope": [],
            "relationship_notes": "",
            "assignments_preview": ["First assignment", "Second assignment"],
        },
    )

    result = build_assignment_manifest(str(phases_dir), str(assignments_dir), str(output_dir))

    assert result["total_count"] == "2"
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert len(manifest["items"]) == 2
    assert manifest["items"][0]["id"] == "P1-A1"
    assert manifest["items"][1]["id"] == "P1-A2"


# ---------------------------------------------------------------------------
# 1d: compile_plan derives name_slug from name when not explicitly provided
# ---------------------------------------------------------------------------


def test_compile_plan_derives_name_slug_from_name(tmp_path: Path) -> None:
    from autoskillit.planner.compiler import compile_plan

    _write_json(
        tmp_path / "phases" / "P1_result.json",
        {
            "id": "P1",
            "name": "Phase One",
            "ordering": 1,
            "goal": "test",
            "scope": [],
            "relationship_notes": "",
            "assignments_preview": [],
        },
    )
    _write_json(
        tmp_path / "assignments" / "P1-A1_result.json",
        {
            "id": "P1-A1",
            "name": "Assignment 1",
            "phase_id": "P1",
            "goal": "test",
            "technical_approach": "test",
            "proposed_work_packages": [],
        },
    )
    _write_json(
        tmp_path / "work_packages" / "P1-A1-WP1_result.json",
        {
            "id": "P1-A1-WP1",
            "name": "WP 1",
            "goal": "test",
            "deliverables": ["src/mod.py"],
            "technical_steps": ["step 1"],
            "acceptance_criteria": ["criterion 1"],
            "depends_on": [],
        },
    )
    _write_json(
        tmp_path / "work_packages" / "wp_manifest.json",
        {"pass_name": "work_packages", "items": [{"id": "P1-A1-WP1", "status": "done"}]},
    )
    _write_json(
        tmp_path / "validation.json",
        {"verdict": "pass", "findings": [], "schema_version": 1},
    )

    compile_plan(str(tmp_path), "test task", "/src")

    issue = (tmp_path / "issues" / "P1-A1-WP1_issue.md").read_text()
    assert "phase-one" in issue


# ---------------------------------------------------------------------------
# 1f: Full pipeline integration test with SKILL.md-compliant data
# (this is the architectural backstop — any future schema drift breaks this)
# ---------------------------------------------------------------------------


def test_skill_output_through_full_pipeline(tmp_path: Path) -> None:
    from autoskillit.planner import build_assignment_manifest, build_wp_manifest, compile_plan, validate_plan

    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir()

    _write_json(
        phases_dir / "P1_result.json",
        {
            "id": "P1",
            "name": "Foundation",
            "ordering": 1,
            "goal": "Build foundation",
            "scope": ["core"],
            "relationship_notes": "",
            "assignments_preview": ["Schema design", "Implementation"],
        },
    )

    build_assignment_manifest(str(phases_dir), str(assignments_dir), str(tmp_path))

    _write_json(
        assignments_dir / "P1-A1_result.json",
        {
            "id": "P1-A1",
            "name": "Schema design",
            "phase_id": "P1",
            "goal": "Design the schema",
            "technical_approach": "Use TypedDict",
            "proposed_work_packages": [
                {
                    "id_suffix": "WP1",
                    "name": "Schema module",
                    "scope": "core",
                    "estimated_files": ["schema.py"],
                }
            ],
        },
    )
    _write_json(
        assignments_dir / "P1-A2_result.json",
        {
            "id": "P1-A2",
            "name": "Implementation",
            "phase_id": "P1",
            "goal": "Implement the feature",
            "technical_approach": "Code it",
            "proposed_work_packages": [
                {
                    "id_suffix": "WP1",
                    "name": "Implementation module",
                    "scope": "core",
                    "estimated_files": ["impl.py"],
                }
            ],
        },
    )

    build_wp_manifest(str(assignments_dir), str(wp_dir))

    for wp_id in ["P1-A1-WP1", "P1-A2-WP1"]:
        _write_json(
            wp_dir / f"{wp_id}_result.json",
            {
                "id": wp_id,
                "name": f"WP {wp_id}",
                "goal": "test goal",
                "deliverables": [f"src/{wp_id}.py"],
                "technical_steps": ["step 1"],
                "acceptance_criteria": ["criterion 1"],
                "depends_on": [],
            },
        )

    validate_result = validate_plan(str(tmp_path))
    assert validate_result["verdict"] == "pass"

    compile_result = compile_plan(str(tmp_path), "test task", "/src")
    assert Path(compile_result["plan_path"]).exists()
    milestones = json.loads((tmp_path / "milestones.json").read_text())
    assert milestones["milestones"][0]["name_slug"] == "foundation"
