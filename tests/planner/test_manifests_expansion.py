"""Tests for autoskillit.planner — expand_assignments, expand_wps, resolve_task_input."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_resolve_task_input_file_with_heading(tmp_path):
    from autoskillit.planner import resolve_task_input

    task_file = tmp_path / "task.md"
    task_file.write_text("# Deploy Auth Service\n\nDetailed description...")
    planner_dir = tmp_path / "planner"
    planner_dir.mkdir()
    result = resolve_task_input(str(task_file), str(planner_dir))
    assert list(planner_dir.iterdir()) == []
    assert result["task_file_path"] == str(task_file)
    assert result["task_label"] == "Deploy Auth Service"


def test_resolve_task_input_file_no_heading(tmp_path):
    from autoskillit.planner import resolve_task_input

    task_file = tmp_path / "task.txt"
    task_file.write_text("Implement the feature flag system for gradual rollout")
    planner_dir = tmp_path / "planner"
    planner_dir.mkdir()
    result = resolve_task_input(str(task_file), str(planner_dir))
    assert result["task_file_path"] == str(task_file)
    assert result["task_label"] == "Implement the feature flag system for gradual rollout"


def test_resolve_task_input_inline_text(tmp_path):
    from autoskillit.planner import resolve_task_input

    planner_dir = tmp_path / "planner"
    planner_dir.mkdir()
    result = resolve_task_input("Add dark mode toggle", str(planner_dir))
    assert result["task_file_path"] == str(planner_dir / "task_input.md")
    assert Path(result["task_file_path"]).read_text() == "Add dark mode toggle"
    assert result["task_label"] == "Add dark mode toggle"


def test_resolve_task_input_inline_with_heading(tmp_path):
    from autoskillit.planner import resolve_task_input

    planner_dir = tmp_path / "planner"
    planner_dir.mkdir()
    text = "# Auth Overhaul\n\nRebuild the entire authentication layer..."
    result = resolve_task_input(text, str(planner_dir))
    assert result["task_label"] == "Auth Overhaul"
    assert Path(result["task_file_path"]).read_text() == text


def test_resolve_task_input_long_inline_truncates_label(tmp_path):
    from autoskillit.planner import resolve_task_input

    planner_dir = tmp_path / "planner"
    planner_dir.mkdir()
    text = "A" * 120
    result = resolve_task_input(text, str(planner_dir))
    assert len(result["task_label"]) <= 80
    assert text.startswith(result["task_label"])


def test_expand_wps_result_dir_points_to_wp_sentinels(tmp_path):
    from autoskillit.planner import expand_wps

    refined = {
        "assignments": [
            {
                "id": "P1-A1",
                "name": "Assignment 1",
                "phase_id": "P1",
                "phase_name": "Phase 1",
                "goal": "test",
                "technical_approach": "test",
                "proposed_work_packages": [
                    {
                        "id_suffix": "WP1",
                        "name": "WP 1",
                        "scope": "core",
                        "estimated_files": ["f.py"],
                    }
                ],
            }
        ],
        "task": "test task",
    }
    refined_path = tmp_path / "refined_assignments.json"
    refined_path.write_text(json.dumps(refined))

    result = expand_wps(str(refined_path), str(tmp_path))

    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["result_dir"].endswith("wp_sentinels")
    assert (tmp_path / "work_packages" / "wp_sentinels").is_dir()


def test_expand_wps_wp_index_path_in_work_packages(tmp_path):
    from autoskillit.planner import expand_wps

    refined = {
        "task": "test",
        "assignments": [
            {
                "id": "P1-A1",
                "name": "Assignment 1",
                "phase_id": "P1",
                "phase_name": "Phase 1",
                "goal": "test",
                "technical_approach": "test",
                "proposed_work_packages": [
                    {"id_suffix": "WP1", "name": "WP1", "scope": "s", "estimated_files": ["f.py"]}
                ],
            }
        ],
    }
    ra_path = tmp_path / "refined_assignments.json"
    ra_path.write_text(json.dumps(refined))

    expand_wps(str(ra_path), str(tmp_path))

    wp_dir = tmp_path / "work_packages"
    assert (wp_dir / "wp_index.json").exists()
    assert not (tmp_path / "wp_index.json").exists()

    ctx_files = list(wp_dir.glob("context_*.json"))
    assert len(ctx_files) >= 1
    ctx = json.loads(ctx_files[0].read_text())
    assert "work_packages" in ctx["wp_index_path"]


def test_expand_assignments_result_dir_points_to_sentinel_subdir(tmp_path):
    from autoskillit.planner import expand_assignments

    refined = {
        "phases": [
            {
                "id": "P1",
                "name": "Phase 1",
                "assignments_preview": [
                    {"id": "P1-A1", "name": "Assignment 1"},
                ],
            }
        ],
        "task": "test task",
    }
    refined_path = tmp_path / "refined_plan.json"
    refined_path.write_text(json.dumps(refined))
    result = expand_assignments(str(refined_path), str(tmp_path))

    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert Path(manifest["result_dir"]).name == "assign_sentinels"
    assert Path(manifest["result_dir"]).is_dir()


def test_expand_wps_result_dir_points_to_sentinel_subdir(tmp_path):
    from autoskillit.planner import expand_wps

    refined = {
        "assignments": [
            {
                "id": "P1-A1",
                "name": "Assignment 1",
                "phase_id": "P1",
                "phase_name": "Phase 1",
                "goal": "test",
                "technical_approach": "test",
                "proposed_work_packages": [
                    {
                        "id_suffix": "WP1",
                        "name": "WP 1",
                        "scope": "core",
                        "estimated_files": ["f.py"],
                    }
                ],
            }
        ],
        "task": "test task",
    }
    refined_path = tmp_path / "refined_assignments.json"
    refined_path.write_text(json.dumps(refined))
    result = expand_wps(str(refined_path), str(tmp_path))

    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert Path(manifest["result_dir"]).name == "wp_sentinels"
    assert Path(manifest["result_dir"]).is_dir()


def test_expand_assignments_records_voided_phases(tmp_path):
    """expand_assignments writes voided_phases to lifecycle_registry.json."""
    from autoskillit.planner import expand_assignments

    refined = {
        "phases": [
            {
                "id": "P1",
                "name": "Phase 1",
                "assignments_preview": [
                    {"id": "P1-A1", "name": "Assignment 1"},
                ],
            },
            {
                "id": "P2",
                "name": "Phase 2",
                "assignments_preview": [],
            },
        ],
        "task": "test task",
    }
    refined_path = tmp_path / "refined_plan.json"
    refined_path.write_text(json.dumps(refined))

    expand_assignments(str(refined_path), str(tmp_path))

    registry_path = tmp_path / "work_packages" / "lifecycle_registry.json"
    assert registry_path.exists()
    registry = json.loads(registry_path.read_text())
    assert "P2" in registry["voided_phases"]
    assert "P1" not in registry["voided_phases"]
