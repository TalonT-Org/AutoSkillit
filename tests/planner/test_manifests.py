from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small]


def test_check_remaining_pending_to_processing(tmp_path):
    """First call marks first pending item as processing and returns it."""
    from autoskillit.planner import check_remaining

    manifest = {
        "pass_name": "assignments",
        "created_at": "2026-04-24T00:00:00Z",
        "items": [
            {
                "id": "P1-A1",
                "name": "A1",
                "status": "pending",
                "result_path": None,
                "metadata": {},
            },
        ],
    }
    manifest_path = tmp_path / "assignment_manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    result = check_remaining(str(manifest_path), "assignments", str(output_dir))

    assert result["has_remaining"] == "true"
    assert result["current_item_path"] != ""
    updated = json.loads(manifest_path.read_text())
    assert updated["items"][0]["status"] == "processing"


def test_check_remaining_processing_becomes_done_when_result_exists(tmp_path):
    """processing item with a result file is marked done."""
    from autoskillit.planner import check_remaining

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "P1-A1_result.json").write_text('{"ok": true}')

    manifest = {
        "pass_name": "assignments",
        "created_at": "2026-04-24T00:00:00Z",
        "items": [
            {
                "id": "P1-A1",
                "name": "A1",
                "status": "processing",
                "result_path": None,
                "metadata": {},
            },
            {
                "id": "P1-A2",
                "name": "A2",
                "status": "pending",
                "result_path": None,
                "metadata": {},
            },
        ],
    }
    manifest_path = tmp_path / "assignment_manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    result = check_remaining(str(manifest_path), "assignments", str(output_dir))

    updated = json.loads(manifest_path.read_text())
    assert updated["items"][0]["status"] == "done"
    assert updated["items"][1]["status"] == "processing"
    assert result["has_remaining"] == "true"


def test_check_remaining_processing_becomes_failed_when_no_result(tmp_path):
    """processing item without a result file is marked failed."""
    from autoskillit.planner import check_remaining

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    # No result file present

    manifest = {
        "pass_name": "assignments",
        "created_at": "2026-04-24T00:00:00Z",
        "items": [
            {
                "id": "P1-A1",
                "name": "A1",
                "status": "processing",
                "result_path": None,
                "metadata": {},
            },
            {
                "id": "P1-A2",
                "name": "A2",
                "status": "pending",
                "result_path": None,
                "metadata": {},
            },
        ],
    }
    manifest_path = tmp_path / "assignment_manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    result = check_remaining(str(manifest_path), "assignments", str(output_dir))

    updated = json.loads(manifest_path.read_text())
    assert updated["items"][0]["status"] == "failed"
    assert result["has_remaining"] == "true"
    ctx = json.loads(Path(result["current_item_path"]).read_text())
    assert ctx["id"] == "P1-A2"


def test_check_remaining_all_done_returns_false(tmp_path):
    """When no pending items remain, has_remaining is 'false' and current_item_path is empty."""
    from autoskillit.planner import check_remaining

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "P1-A1_result.json").write_text('{"ok": true}')

    manifest = {
        "pass_name": "assignments",
        "created_at": "2026-04-24T00:00:00Z",
        "items": [
            {
                "id": "P1-A1",
                "name": "A1",
                "status": "processing",
                "result_path": None,
                "metadata": {},
            },
        ],
    }
    manifest_path = tmp_path / "assignment_manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    result = check_remaining(str(manifest_path), "assignments", str(output_dir))

    assert result["has_remaining"] == "false"
    assert result["current_item_path"] == ""


def test_check_remaining_context_file_written(tmp_path):
    """A context file is written for the newly-processing item."""
    from autoskillit.planner import check_remaining

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    manifest = {
        "pass_name": "assignments",
        "created_at": "2026-04-24T00:00:00Z",
        "items": [
            {
                "id": "P1-A1",
                "name": "A1",
                "status": "pending",
                "result_path": None,
                "metadata": {"phase": 1},
            },
        ],
    }
    manifest_path = tmp_path / "assignment_manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    result = check_remaining(str(manifest_path), "assignments", str(output_dir))

    context_path = Path(result["current_item_path"])
    assert context_path.exists()
    ctx = json.loads(context_path.read_text())
    assert ctx["id"] == "P1-A1"
    assert "wp_index_path" in ctx
    assert ctx["wp_index_path"] == str(output_dir / "wp_index.json")


def test_check_remaining_return_values_are_strings(tmp_path):
    """All returned values are plain strings, never booleans or other types."""
    from autoskillit.planner import check_remaining

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    manifest = {
        "pass_name": "assignments",
        "created_at": "2026-04-24T00:00:00Z",
        "items": [
            {
                "id": "P1-A1",
                "name": "A1",
                "status": "pending",
                "result_path": None,
                "metadata": {},
            },
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    result = check_remaining(str(manifest_path), "assignments", str(output_dir))

    for key, val in result.items():
        assert isinstance(val, str), f"key {key!r} has non-string value {val!r}"


def test_check_remaining_wp_backstop_rebuilds_missing_index_entry(tmp_path):
    """Recovery backstop: if a WP result exists but isn't in wp_index.json, rebuild the entry."""
    from autoskillit.planner import check_remaining

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    wp_id = "P1-A1-WP1"
    (output_dir / f"{wp_id}_result.json").write_text(
        json.dumps({"id": wp_id, "name": "First WP", "summary": "done"})
    )

    # wp_index.json exists but is missing the entry for P1-A1-WP1
    wp_index_path = output_dir / "wp_index.json"
    wp_index_path.write_text("[]")

    manifest = {
        "pass_name": "work_packages",
        "created_at": "2026-04-24T00:00:00Z",
        "items": [
            {
                "id": wp_id,
                "name": "WP1",
                "status": "processing",
                "result_path": None,
                "metadata": {},
            },
        ],
    }
    manifest_path = tmp_path / "wp_manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    check_remaining(str(manifest_path), "work_packages", str(output_dir))

    index = json.loads(wp_index_path.read_text())
    indexed_ids = {entry["id"] for entry in index}
    assert wp_id in indexed_ids
    entry = next(e for e in index if e["id"] == wp_id)
    assert entry["name"] == "First WP"
    assert entry["summary"] == "done"


def test_build_assignment_manifest_basic(tmp_path):
    """Phase results with assignments produce a valid manifest."""
    from autoskillit.planner import build_assignment_manifest

    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    phase_result = {
        "phase_number": 1,
        "assignments": [
            {"id_suffix": "A1", "name": "First assignment", "metadata": {}},
        ],
    }
    (phases_dir / "phase_1_result.json").write_text(json.dumps(phase_result))

    result = build_assignment_manifest(str(phases_dir), str(assignments_dir), str(output_dir))

    assert result["total_count"] == "1"
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["pass_name"] == "assignments"
    assert len(manifest["items"]) == 1
    assert manifest["items"][0]["status"] == "pending"


def test_build_assignment_manifest_ordering(tmp_path):
    """Items are ordered by phase number then assignment sequence."""
    from autoskillit.planner import build_assignment_manifest

    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    (phases_dir / "phase_2_result.json").write_text(
        json.dumps(
            {
                "phase_number": 2,
                "assignments": [{"id_suffix": "A1", "name": "Phase2-A1", "metadata": {}}],
            }
        )
    )
    (phases_dir / "phase_1_result.json").write_text(
        json.dumps(
            {
                "phase_number": 1,
                "assignments": [
                    {"id_suffix": "A2", "name": "Phase1-A2", "metadata": {}},
                    {"id_suffix": "A1", "name": "Phase1-A1", "metadata": {}},
                ],
            }
        )
    )

    result = build_assignment_manifest(
        str(phases_dir), str(tmp_path / "assignments"), str(output_dir)
    )

    manifest = json.loads(Path(result["manifest_path"]).read_text())
    ids = [item["id"] for item in manifest["items"]]
    assert ids == ["P1-A1", "P1-A2", "P2-A1"]


def test_build_assignment_manifest_empty_phases(tmp_path):
    """No phase results → manifest with zero items."""
    from autoskillit.planner import build_assignment_manifest

    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    result = build_assignment_manifest(
        str(phases_dir), str(tmp_path / "assignments"), str(output_dir)
    )

    assert result["total_count"] == "0"
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["items"] == []


def test_build_assignment_manifest_return_values_are_strings(tmp_path):
    """All returned values are plain strings."""
    from autoskillit.planner import build_assignment_manifest

    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    result = build_assignment_manifest(
        str(phases_dir), str(tmp_path / "assignments"), str(output_dir)
    )

    for key, val in result.items():
        assert isinstance(val, str), f"key {key!r} has non-string value {val!r}"


def test_build_wp_manifest_basic(tmp_path):
    """Assignment results with proposed_work_packages produce a valid WP manifest."""
    from autoskillit.planner import build_wp_manifest

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    assign_result = {
        "phase_number": 1,
        "assignment_number": 1,
        "proposed_work_packages": [
            {
                "id_suffix": "WP1",
                "name": "First WP",
                "scope": "do thing",
                "estimated_files": ["a.py"],
            },
        ],
    }
    (assignments_dir / "P1-A1_result.json").write_text(json.dumps(assign_result))

    result = build_wp_manifest(str(assignments_dir), str(output_dir))

    assert result["total_count"] == "1"
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["pass_name"] == "work_packages"
    assert len(manifest["items"]) == 1
    assert manifest["items"][0]["id"] == "P1-A1-WP1"
    assert manifest["items"][0]["status"] == "pending"


def test_build_wp_manifest_hierarchical_ids(tmp_path):
    """WP IDs follow P{N}-A{N}-WP{N} format and are ordered by phase→assignment→WP."""
    from autoskillit.planner import build_wp_manifest

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    (assignments_dir / "P1-A2_result.json").write_text(
        json.dumps(
            {
                "phase_number": 1,
                "assignment_number": 2,
                "proposed_work_packages": [
                    {"id_suffix": "WP1", "name": "P1A2WP1", "scope": "", "estimated_files": []},
                ],
            }
        )
    )
    (assignments_dir / "P1-A1_result.json").write_text(
        json.dumps(
            {
                "phase_number": 1,
                "assignment_number": 1,
                "proposed_work_packages": [
                    {"id_suffix": "WP2", "name": "P1A1WP2", "scope": "", "estimated_files": []},
                    {"id_suffix": "WP1", "name": "P1A1WP1", "scope": "", "estimated_files": []},
                ],
            }
        )
    )

    result = build_wp_manifest(str(assignments_dir), str(output_dir))

    manifest = json.loads(Path(result["manifest_path"]).read_text())
    ids = [item["id"] for item in manifest["items"]]
    assert ids == ["P1-A1-WP1", "P1-A1-WP2", "P1-A2-WP1"]


def test_build_wp_manifest_wp_index_initialized(tmp_path):
    """build_wp_manifest creates an empty wp_index.json."""
    from autoskillit.planner import build_wp_manifest

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    build_wp_manifest(str(assignments_dir), str(output_dir))

    wp_index = output_dir / "wp_index.json"
    assert wp_index.exists()
    assert json.loads(wp_index.read_text()) == []


def test_build_wp_manifest_return_values_are_strings(tmp_path):
    """All returned values are plain strings."""
    from autoskillit.planner import build_wp_manifest

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    result = build_wp_manifest(str(assignments_dir), str(output_dir))

    for key, val in result.items():
        assert isinstance(val, str), f"key {key!r} has non-string value {val!r}"
