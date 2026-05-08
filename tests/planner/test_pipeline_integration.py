"""End-to-end pipeline integration tests.

These tests are the architectural backstop: any future change that breaks the schema
alignment between SKILL.md output, the Python validation boundary, and the consumers
will cause at least one test here to fail.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.planner.conftest import (
    make_assignment_result,
    make_phase_result,
    make_wp_result,
    write_json,
    write_task_file,
)

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_multi_phase_pipeline_end_to_end(tmp_path: Path) -> None:
    """Two-phase pipeline: SKILL.md-compliant data flows through every pipeline stage."""
    from autoskillit.planner import (
        compile_plan,
        expand_assignments,
        expand_wps,
        finalize_wp_manifest,
        validate_plan,
    )

    # Phase results (needed by validate_plan/compile_plan)
    write_json(
        tmp_path / "phases" / "P1_result.json",
        {
            "id": "P1",
            "name": "Foundation",
            "ordering": 1,
            "goal": "Establish core infrastructure",
            "scope": ["core"],
            "relationship_notes": "No prior dependencies",
            "assignments_preview": ["Core setup"],
        },
    )
    write_json(
        tmp_path / "phases" / "P2_result.json",
        {
            "id": "P2",
            "name": "Application Layer",
            "ordering": 2,
            "goal": "Implement application logic on top of foundation",
            "scope": ["app"],
            "relationship_notes": "Depends on Phase 1",
            "assignments_preview": ["App module"],
        },
    )

    write_json(
        tmp_path / "refined_plan.json",
        {
            "phases": [
                {
                    "id": "P1",
                    "name": "Foundation",
                    "ordering": 1,
                    "assignments_preview": [{"id": "P1-A1", "name": "Core setup"}],
                },
                {
                    "id": "P2",
                    "name": "Application Layer",
                    "ordering": 2,
                    "assignments_preview": [{"id": "P2-A1", "name": "App module"}],
                },
            ]
        },
    )

    expand_assignments(str(tmp_path / "refined_plan.json"), str(tmp_path))

    write_json(
        tmp_path / "assignments" / "P1-A1_result.json",
        {
            "id": "P1-A1",
            "name": "Core setup",
            "phase_id": "P1",
            "goal": "Set up core modules",
            "technical_approach": "Direct implementation",
            "proposed_work_packages": [
                {
                    "id_suffix": "WP1",
                    "name": "Core module",
                    "scope": "core",
                    "estimated_files": ["core.py"],
                }
            ],
        },
    )
    write_json(
        tmp_path / "assignments" / "P2-A1_result.json",
        {
            "id": "P2-A1",
            "name": "App module",
            "phase_id": "P2",
            "goal": "Build application layer",
            "technical_approach": "Build on core",
            "proposed_work_packages": [
                {
                    "id_suffix": "WP1",
                    "name": "App module",
                    "scope": "app",
                    "estimated_files": ["app.py"],
                }
            ],
        },
    )

    write_json(
        tmp_path / "refined_assignments.json",
        {
            "assignments": [
                {
                    "id": "P1-A1",
                    "phase_id": "P1",
                    "phase_name": "Foundation",
                    "phase_number": 1,
                    "assignment_number": 1,
                    "proposed_work_packages": [
                        {"id": "P1-A1-WP1", "name": "Core module", "scope": "core"},
                    ],
                },
                {
                    "id": "P2-A1",
                    "phase_id": "P2",
                    "phase_name": "Application Layer",
                    "phase_number": 2,
                    "assignment_number": 1,
                    "proposed_work_packages": [
                        {"id": "P2-A1-WP1", "name": "App module", "scope": "app"},
                    ],
                },
            ]
        },
    )

    expand_wps(str(tmp_path / "refined_assignments.json"), str(tmp_path))

    wp_dir = tmp_path / "work_packages"
    write_json(
        wp_dir / "P1-A1-WP1_result.json",
        make_wp_result("P1-A1-WP1", deliverables=["src/core.py"]),
    )
    write_json(
        wp_dir / "P2-A1-WP1_result.json",
        make_wp_result("P2-A1-WP1", deliverables=["src/app.py"], depends_on=["P1-A1-WP1"]),
    )

    finalize_wp_manifest(str(wp_dir), str(tmp_path))

    validate_result = validate_plan(str(tmp_path))
    assert validate_result["verdict"] == "pass", validate_result

    compile_result = compile_plan(
        str(tmp_path), write_task_file(tmp_path, "integration test task"), "/src"
    )

    plan_path = Path(compile_result["plan_path"])
    assert plan_path.exists()
    plan_md = plan_path.read_text()
    assert "## Phase 1: Foundation" in plan_md
    assert "## Phase 2: Application Layer" in plan_md

    milestones = json.loads((tmp_path / "milestones.json").read_text())
    slugs = [m["name_slug"] for m in milestones["milestones"]]
    assert "foundation" in slugs
    assert "application-layer" in slugs

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    order = manifest["execution_order"]
    assert order.index("P1-A1-WP1") < order.index("P2-A1-WP1")


def test_pipeline_factory_fixtures_are_schema_compliant(tmp_path: Path) -> None:
    """Factory-built fixtures pass through the full pipeline without errors."""
    from autoskillit.planner import compile_plan, validate_plan

    # Build everything using factory functions (SKILL.md-derived, not raw backend fields)
    write_json(
        tmp_path / "phases" / "P1_result.json",
        make_phase_result(1, name="Schema Alignment"),
    )
    write_json(
        tmp_path / "assignments" / "P1-A1_result.json",
        make_assignment_result(1, 1, name="Implement schema"),
    )
    write_json(
        tmp_path / "work_packages" / "P1-A1-WP1_result.json",
        make_wp_result("P1-A1-WP1"),
    )
    write_json(
        tmp_path / "work_packages" / "wp_manifest.json",
        {"pass_name": "work_packages", "items": [{"id": "P1-A1-WP1", "status": "done"}]},
    )
    write_json(
        tmp_path / "validation.json",
        {"verdict": "pass", "findings": [], "warnings": [], "schema_version": 2},
    )

    validate_result = validate_plan(str(tmp_path))
    assert validate_result["verdict"] == "pass"

    compile_result = compile_plan(str(tmp_path), write_task_file(tmp_path, "factory test"), "/src")
    assert Path(compile_result["plan_path"]).exists()

    milestones = json.loads((tmp_path / "milestones.json").read_text())
    assert milestones["milestones"][0]["name_slug"] == "schema-alignment"
