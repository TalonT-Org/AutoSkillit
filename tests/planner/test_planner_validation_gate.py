"""Tests for the planner L1 subpackage scaffold — ARCH-010 validation gate: validate_refined_*, resolve_wp_id."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_validate_refined_assignments_rejects_empty_id() -> None:
    from autoskillit.planner.schema import validate_refined_assignments

    data = {
        "assignments": [
            {
                "id": "P1-A1",
                "phase_id": "P1",
                "phase_number": 1,
                "assignment_number": 1,
                "proposed_work_packages": [{"name": "No ID", "scope": "s"}],
            }
        ]
    }
    with pytest.raises(ValueError, match="neither 'id' nor 'id_suffix'"):
        validate_refined_assignments(data)


def test_validate_refined_assignments_resolves_id_suffix() -> None:
    from autoskillit.planner.schema import validate_refined_assignments

    data = {
        "assignments": [
            {
                "id": "P1-A1",
                "phase_id": "P1",
                "phase_number": 1,
                "assignment_number": 1,
                "proposed_work_packages": [
                    {"id_suffix": "WP1", "name": "First", "scope": "s1"},
                ],
            }
        ]
    }
    result = validate_refined_assignments(data)
    wp = result["assignments"][0]["proposed_work_packages"][0]
    assert wp["id"] == "P1-A1-WP1"


def test_validate_refined_assignments_rejects_unresolvable_assign_id() -> None:
    from autoskillit.planner.schema import validate_refined_assignments

    data = {
        "assignments": [
            {
                "phase_name": "Phase 1",
                "proposed_work_packages": [{"id_suffix": "WP1", "name": "X"}],
            }
        ]
    }
    with pytest.raises(ValueError, match="assignment.*resolvable id"):
        validate_refined_assignments(data)


def test_validate_refined_plan_rejects_empty_phase_id() -> None:
    from autoskillit.planner.schema import validate_refined_plan

    data = {
        "phases": [
            {
                "id": "",
                "name": "Bad Phase",
                "assignments_preview": [],
            }
        ]
    }
    with pytest.raises(ValueError, match="empty.*id"):
        validate_refined_plan(data)


def test_expand_assignments_rejects_empty_preview_ids(tmp_path) -> None:
    from autoskillit.planner.manifests import expand_assignments

    refined = tmp_path / "refined_plan.json"
    refined.write_text(
        json.dumps(
            {
                "phases": [
                    {
                        "id": "P1",
                        "name": "Phase 1",
                        "assignments_preview": [{"name": "A1"}],
                    }
                ]
            }
        )
    )
    result = expand_assignments(str(refined), str(tmp_path))
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    ids = manifest["items"][0]["metadata"]["assignment_ids"]
    assert all(id_val != "" for id_val in ids)


def test_resolve_wp_id_from_id() -> None:
    from autoskillit.planner.schema import resolve_wp_id

    assert resolve_wp_id({"id": "P1-A1-WP1"}, "P1-A1") == "P1-A1-WP1"


def test_resolve_wp_id_from_suffix() -> None:
    from autoskillit.planner.schema import resolve_wp_id

    assert resolve_wp_id({"id_suffix": "WP2"}, "P1-A1") == "P1-A1-WP2"


def test_resolve_wp_id_raises_on_neither() -> None:
    from autoskillit.planner.schema import resolve_wp_id

    with pytest.raises(ValueError, match="neither 'id' nor 'id_suffix'"):
        resolve_wp_id({"name": "No ID"}, "P1-A1")


def test_resolve_wp_id_rejects_empty_string_id() -> None:
    from autoskillit.planner.schema import resolve_wp_id

    assert resolve_wp_id({"id": "", "id_suffix": "WP1"}, "P1-A1") == "P1-A1-WP1"


def test_expand_wps_resolves_id_suffix(tmp_path) -> None:
    from autoskillit.planner.manifests import expand_wps

    refined = tmp_path / "refined_assignments.json"
    refined.write_text(
        json.dumps(
            {
                "assignments": [
                    {
                        "id": "P1-A1",
                        "phase_id": "P1",
                        "phase_number": 1,
                        "assignment_number": 1,
                        "proposed_work_packages": [
                            {"id_suffix": "WP1", "name": "First", "scope": "s1"},
                            {"id_suffix": "WP2", "name": "Second", "scope": "s2"},
                        ],
                    }
                ]
            }
        )
    )
    result = expand_wps(str(refined), str(tmp_path))
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    wp_ids = manifest["items"][0]["metadata"]["wp_ids"]
    assert wp_ids == ["P1-A1-WP1", "P1-A1-WP2"]


def test_expand_wps_synthesizes_assign_id_from_numbers(tmp_path) -> None:
    from autoskillit.planner.manifests import expand_wps

    refined = tmp_path / "refined_assignments.json"
    refined.write_text(
        json.dumps(
            {
                "assignments": [
                    {
                        "phase_number": 2,
                        "assignment_number": 3,
                        "proposed_work_packages": [
                            {"id_suffix": "WP1", "name": "WP", "scope": "s"},
                        ],
                    }
                ]
            }
        )
    )
    result = expand_wps(str(refined), str(tmp_path))
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    wp_ids = manifest["items"][0]["metadata"]["wp_ids"]
    assert wp_ids == ["P2-A3-WP1"]


def test_expand_wps_raises_on_missing_id_and_suffix(tmp_path) -> None:
    from autoskillit.planner.manifests import expand_wps

    refined = tmp_path / "refined_assignments.json"
    refined.write_text(
        json.dumps(
            {
                "assignments": [
                    {
                        "id": "P1-A1",
                        "phase_id": "P1",
                        "phase_number": 1,
                        "assignment_number": 1,
                        "proposed_work_packages": [
                            {"name": "No ID WP", "scope": "s1"},
                        ],
                    }
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="neither 'id' nor 'id_suffix'"):
        expand_wps(str(refined), str(tmp_path))
