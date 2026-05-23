"""Tests for the planner L1 subpackage scaffold — atomic-write guards, task context propagation, ID contract enforcement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_merge_files_does_not_use_atomic_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """merge_files must write directly under flock, not via atomic_write/os.replace."""
    from unittest.mock import MagicMock

    from autoskillit.planner.merge import merge_files

    spy = MagicMock(side_effect=AssertionError("atomic_write must not be called"))
    monkeypatch.setattr("autoskillit.planner.merge.write_versioned_json", spy)

    results_dir = tmp_path / "phases"
    results_dir.mkdir()
    (results_dir / "P1_result.json").write_text(
        json.dumps({"id": "P1", "name": "Phase 1", "ordering": 1})
    )

    out = tmp_path / "combined.json"
    result = merge_files([str(results_dir / "P1_result.json")], str(out), "phases")
    assert result["item_count"] == "1"
    merged = json.loads(out.read_text())
    assert merged["schema_version"] == 1
    assert len(merged["phases"]) == 1
    spy.assert_not_called()


def test_replace_item_does_not_use_atomic_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """replace_item must write directly under flock, not via atomic_write/os.replace."""
    from unittest.mock import MagicMock

    from autoskillit.planner.merge import replace_item

    spy = MagicMock(side_effect=AssertionError("atomic_write must not be called"))
    monkeypatch.setattr("autoskillit.planner.merge.write_versioned_json", spy)

    src = tmp_path / "combined.json"
    src.write_text(json.dumps({"phases": [{"id": "P1", "name": "Old"}], "schema_version": 1}))
    repl = tmp_path / "replacement.json"
    repl.write_text(json.dumps({"id": "P1", "name": "New"}))

    result = replace_item(str(src), "P1", str(repl))
    assert result["replaced_id"] == "P1"
    data = json.loads(src.read_text())
    assert data["phases"][0]["name"] == "New"
    assert data["schema_version"] == 1
    spy.assert_not_called()


def test_expand_assignments_includes_task_file_path_in_context(tmp_path: Path) -> None:
    from autoskillit.planner.manifests import expand_assignments

    task_file = tmp_path / "task_input.md"
    task_file.write_text("Split research.yaml into 4 sub-recipes")
    refined = {
        "task": "Split research.yaml into 4 sub-recipes",
        "phases": [
            {
                "id": "P1",
                "name": "Recipe Split",
                "assignments_preview": [{"name": "Sub-recipe A"}, {"name": "Sub-recipe B"}],
            }
        ],
    }
    plan_path = tmp_path / "refined_plan.json"
    plan_path.write_text(json.dumps(refined))
    result = expand_assignments(
        refined_plan_path=str(plan_path),
        output_dir=str(tmp_path),
        task_file_path=str(task_file),
    )
    context_paths = [p for p in result["context_paths"].split(",") if p.strip()]
    assert context_paths, "Must produce at least one context path"
    for cp in context_paths:
        context = json.loads(Path(cp).read_text())
        assert "task_file_path" in context, "Context file must include task_file_path field"
        assert context["task_file_path"] == str(task_file)
        assert "task" not in context, "Context file must not include inline task text"


def test_expand_assignments_without_task_file_path_uses_empty(tmp_path: Path) -> None:
    from autoskillit.planner.manifests import expand_assignments

    refined = {
        "task": "Some task",
        "phases": [{"id": "P1", "name": "Phase", "assignments_preview": [{"name": "A"}]}],
    }
    plan_path = tmp_path / "refined_plan.json"
    plan_path.write_text(json.dumps(refined))
    result = expand_assignments(refined_plan_path=str(plan_path), output_dir=str(tmp_path))
    context_paths = [p for p in result["context_paths"].split(",") if p.strip()]
    assert context_paths, "Must produce at least one context path"
    for cp in context_paths:
        context = json.loads(Path(cp).read_text())
        assert context.get("task_file_path", "") == ""
        assert "task" not in context, "Context file must not include inline task text"


def test_expand_wps_includes_task_file_path_in_context(tmp_path: Path) -> None:
    from autoskillit.planner.manifests import expand_wps

    task_file = tmp_path / "task_input.md"
    task_file.write_text("Add telemetry to fleet dispatch")
    refined = {
        "schema_version": 2,
        "task": "Add telemetry to fleet dispatch",
        "source_dir": "/fake",
        "assignments": [
            {
                "id": "P1-A1",
                "phase_id": "P1",
                "phase_number": 1,
                "assignment_number": 1,
                "name": "Telemetry",
                "goal": "Add metrics",
                "scope": ["fleet"],
                "deliverables": ["fleet/_api.py"],
                "proposed_work_packages": [
                    {
                        "id": "P1-A1-WP1",
                        "name": "WP: Instrument dispatch",
                        "scope": "fleet/_api.py",
                        "estimated_files": ["fleet/_api.py"],
                    }
                ],
            }
        ],
    }
    plan_path = tmp_path / "refined_assignments.json"
    plan_path.write_text(json.dumps(refined))
    result = expand_wps(
        refined_assignments_path=str(plan_path),
        output_dir=str(tmp_path),
        task_file_path=str(task_file),
    )
    context_paths = [p for p in result["context_paths"].split(",") if p.strip()]
    assert context_paths, "Must produce at least one context path"
    for cp in context_paths:
        context = json.loads(Path(cp).read_text())
        assert "task_file_path" in context, "Context file must include task_file_path field"
        assert context["task_file_path"] == str(task_file)
        assert "task" not in context, "Context file must not include inline task text"


def test_expand_assignments_normalizes_plain_string_previews(tmp_path: Path) -> None:
    """Test 1a: plain-string previews are normalized to canonical P{N}-A{M} IDs."""
    from autoskillit.planner.manifests import expand_assignments

    refined = tmp_path / "refined_plan.json"
    refined.write_text(
        json.dumps(
            {
                "phases": [
                    {
                        "id": "P3",
                        "name": "Phase 3",
                        "ordering": 3,
                        "assignments_preview": ["WP-1: Define Backend", "WP-2: Implement API"],
                    },
                ]
            }
        )
    )
    result = expand_assignments(str(refined), str(tmp_path))
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assignment_ids = manifest["items"][0]["metadata"]["assignment_ids"]
    assignment_names = manifest["items"][0]["metadata"]["assignment_names"]
    assert assignment_ids == ["P3-A1", "P3-A2"], (
        f"Expected canonical IDs ['P3-A1', 'P3-A2'], got {assignment_ids}"
    )
    assert assignment_names == ["WP-1: Define Backend", "WP-2: Implement API"], (
        f"Raw string names should be preserved in assignment_names, got {assignment_names}"
    )


def test_expand_assignments_normalizes_dict_with_noncanonical_id(tmp_path: Path) -> None:
    """Test 1b: dict previews with non-canonical id are normalized to canonical format."""
    from autoskillit.planner.manifests import expand_assignments

    refined = tmp_path / "refined_plan.json"
    refined.write_text(
        json.dumps(
            {
                "phases": [
                    {
                        "id": "P1",
                        "name": "Phase 1",
                        "ordering": 1,
                        "assignments_preview": [{"id": "setup-infra", "name": "Setup Infra"}],
                    },
                ]
            }
        )
    )
    result = expand_assignments(str(refined), str(tmp_path))
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assignment_ids = manifest["items"][0]["metadata"]["assignment_ids"]
    assert assignment_ids == ["P1-A1"], (
        f"Expected canonical ['P1-A1'], got {assignment_ids} — non-canonical must be normalized"
    )


@pytest.mark.parametrize(
    "preview_input,expected_ids",
    [
        # Plain strings with WP-style prefixes
        (
            ["WP-1: Task A", "WP-2: Task B"],
            ["P1-A1", "P1-A2"],
        ),
        # Dicts with canonical IDs
        (
            [{"id": "P1-A1", "name": "A"}, {"id": "P1-A2", "name": "B"}],
            ["P1-A1", "P1-A2"],
        ),
        # Dicts without IDs
        (
            [{"name": "First"}, {"name": "Second"}],
            ["P1-A1", "P1-A2"],
        ),
        # Mixed: dict with canonical id + plain string
        (
            [{"id": "P1-A1", "name": "First"}, "Task B"],
            ["P1-A1", "P1-A2"],
        ),
        # Dict with non-canonical id
        (
            [{"id": "setup-task", "name": "Setup"}],
            ["P1-A1"],
        ),
    ],
)
def test_expand_assignments_post_generation_all_ids_match_regex(
    tmp_path: Path, preview_input: list, expected_ids: list[str]
) -> None:
    """Test 1c: every generated assignment ID matches ASSIGN_ID_RE."""
    from autoskillit.planner.manifests import expand_assignments
    from autoskillit.planner.schema import ASSIGN_ID_RE

    refined = tmp_path / "refined_plan.json"
    refined.write_text(
        json.dumps(
            {
                "phases": [
                    {
                        "id": "P1",
                        "name": "Phase 1",
                        "ordering": 1,
                        "assignments_preview": preview_input,
                    },
                ]
            }
        )
    )
    result = expand_assignments(str(refined), str(tmp_path))
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assignment_ids = manifest["items"][0]["metadata"]["assignment_ids"]
    assert assignment_ids == expected_ids, f"Expected {expected_ids}, got {assignment_ids}"
    for aid in assignment_ids:
        assert ASSIGN_ID_RE.match(aid), (
            f"Assignment ID {aid!r} does not match canonical format ^P\\d+-A\\d+$"
        )


def test_validate_refined_plan_accepts_non_empty_plain_string_preview() -> None:
    """Test 1g: validate_refined_plan accepts plain-string entries that are non-empty strings."""
    from autoskillit.planner.schema import validate_refined_plan

    data = {
        "phases": [
            {
                "id": "P1",
                "name": "Phase 1",
                "ordering": 1,
                "assignments_preview": ["WP-1: Task A", {"id": "P1-A2", "name": "Task B"}],
            },
        ]
    }
    # Should not raise — plain strings are valid previews
    result = validate_refined_plan(data)
    assert result["phases"][0]["assignments_preview"] == data["phases"][0]["assignments_preview"]
