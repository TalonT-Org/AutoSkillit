from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.planner.conftest import make_assignment_result, make_phase_result, make_wp_result

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def _make_manifest(items: list[dict], result_dir: str) -> dict:
    return {
        "pass_name": "phases",
        "result_dir": result_dir,
        "created_at": "2026-04-24T00:00:00Z",
        "items": [
            {
                "id": item["id"],
                "name": item.get("name", item["id"]),
                "status": item.get("status", "pending"),
                "result_path": item.get("result_path", None),
                "metadata": item.get("metadata", {}),
            }
            for item in items
        ],
    }


def test_check_remaining_pending_to_processing(tmp_path):
    """First call marks first pending item as processing and returns it."""
    from autoskillit.planner import check_remaining

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    manifest = {
        "pass_name": "assignments",
        "result_dir": str(assignments_dir),
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

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (assignments_dir / "P1-A1_result.json").write_text('{"ok": true}')

    manifest = {
        "pass_name": "assignments",
        "result_dir": str(assignments_dir),
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

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    # No result file present

    manifest = {
        "pass_name": "assignments",
        "result_dir": str(assignments_dir),
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

    with patch("time.sleep"):
        result = check_remaining(str(manifest_path), "assignments", str(output_dir))

    updated = json.loads(manifest_path.read_text())
    assert updated["items"][0]["status"] == "failed"
    assert result["has_remaining"] == "true"
    ctx = json.loads(Path(result["current_item_path"]).read_text())
    assert ctx["id"] == "P1-A2"


def test_check_remaining_processing_does_not_fail_on_first_miss(tmp_path):
    """processing item: result_path.exists() returns False only on first call, True after.
    Item must become done, not failed."""
    from autoskillit.planner import check_remaining

    manifest = _make_manifest([{"id": "A1", "status": "processing"}], result_dir=str(tmp_path))
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    result_path = tmp_path / "A1_result.json"

    call_count = 0
    original_exists = Path.exists

    def lagged_exists(self):
        nonlocal call_count
        if str(self) == str(result_path):
            call_count += 1
            if call_count == 1:
                return False  # first check: FS lag
            result_path.write_text('{"id":"A1","name":"","summary":""}')
            return True
        return original_exists(self)

    with patch("time.sleep"), patch.object(Path, "exists", lagged_exists):
        check_remaining(str(manifest_path), "phases", str(tmp_path))

    updated = json.loads(manifest_path.read_text())
    assert updated["items"][0]["status"] == "done"


def test_check_remaining_processing_fails_after_all_retries_exhausted(tmp_path):
    """processing item with no result file at all: must become failed after retries."""
    from autoskillit.planner import check_remaining

    manifest = _make_manifest(
        [
            {"id": "A1", "status": "processing"},
            {"id": "A2", "status": "pending"},
        ],
        result_dir=str(tmp_path),
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    # No result file for A1

    with patch("time.sleep") as mock_sleep:
        check_remaining(str(manifest_path), "phases", str(tmp_path))

    updated = json.loads(manifest_path.read_text())
    items = {i["id"]: i for i in updated["items"]}
    assert items["A1"]["status"] == "failed"
    assert mock_sleep.call_count == 2  # one sleep per range(2) iteration


def test_check_remaining_sleep_called_with_one_second(tmp_path):
    """Each retry sleep must be exactly 1 second."""
    from autoskillit.planner import check_remaining

    manifest = _make_manifest([{"id": "A1", "status": "processing"}], result_dir=str(tmp_path))
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    with patch("time.sleep") as mock_sleep:
        check_remaining(str(manifest_path), "phases", str(tmp_path))

    for call in mock_sleep.call_args_list:
        assert call.args[0] == 1


def test_check_remaining_all_done_returns_false(tmp_path):
    """When no pending items remain, has_remaining is 'false' and current_item_path is empty."""
    from autoskillit.planner import check_remaining

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (assignments_dir / "P1-A1_result.json").write_text('{"ok": true}')

    manifest = {
        "pass_name": "assignments",
        "result_dir": str(assignments_dir),
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

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    manifest = {
        "pass_name": "assignments",
        "result_dir": str(assignments_dir),
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

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    manifest = {
        "pass_name": "assignments",
        "result_dir": str(assignments_dir),
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

    wps_dir = tmp_path / "work_packages"
    wps_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    wp_id = "P1-A1-WP1"
    (wps_dir / f"{wp_id}_result.json").write_text(
        json.dumps({"id": wp_id, "name": "First WP", "summary": "done"})
    )

    # wp_index.json exists but is missing the entry for P1-A1-WP1
    wp_index_path = output_dir / "wp_index.json"
    wp_index_path.write_text("[]")

    manifest = {
        "pass_name": "work_packages",
        "result_dir": str(wps_dir),
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


def test_check_remaining_finds_result_in_subdir_produced_by_build_manifest(tmp_path):
    """Round-trip: result written to subdirectory designated by build_assignment_manifest
    must be found by check_remaining."""
    from autoskillit.planner import build_assignment_manifest, check_remaining

    phases_dir = tmp_path / "phases"
    assignments_dir = tmp_path / "assignments"
    output_dir = tmp_path
    phases_dir.mkdir()
    assignments_dir.mkdir()

    (phases_dir / "P1_result.json").write_text(
        json.dumps(
            {
                "phase_number": 1,
                "phase_name": "Alpha",
                "id": "P1",
                "name": "Alpha",
                "ordering": 1,
                "assignments": [
                    {"assignment_number": 1, "title": "Do X", "name": "Do X", "metadata": {}}
                ],
                "assignments_preview": ["Do X"],
            }
        )
    )

    result = build_assignment_manifest(
        phases_dir=str(phases_dir),
        assignments_dir=str(assignments_dir),
        output_dir=str(output_dir),
    )
    manifest_path = result["manifest_path"]

    cr1 = check_remaining(manifest_path, "assignments", str(output_dir))
    assert cr1["has_remaining"] == "true"

    (assignments_dir / "P1-A1_result.json").write_text(json.dumps({"ok": True}))

    check_remaining(manifest_path, "assignments", str(output_dir))
    manifest = json.loads(Path(manifest_path).read_text())
    done_item = next(i for i in manifest["items"] if i["id"] == "P1-A1")
    assert done_item["status"] == "done"
    assert done_item["result_path"] == str(assignments_dir / "P1-A1_result.json")


def test_check_remaining_prior_results_populated_from_done_items(tmp_path):
    """After one item transitions to done, the next item's context file must include
    that item's result_path in prior_results."""
    from autoskillit.planner import build_assignment_manifest, check_remaining

    phases_dir = tmp_path / "phases"
    assignments_dir = tmp_path / "assignments"
    phases_dir.mkdir()
    assignments_dir.mkdir()

    (phases_dir / "P1_result.json").write_text(
        json.dumps(
            {
                "id": "P1",
                "name": "Alpha",
                "ordering": 1,
                "assignments_preview": ["A1", "A2"],
            }
        )
    )
    result = build_assignment_manifest(
        phases_dir=str(phases_dir),
        assignments_dir=str(assignments_dir),
        output_dir=str(tmp_path),
    )
    manifest_path = result["manifest_path"]

    check_remaining(manifest_path, "assignments", str(tmp_path))
    p1a1_result = assignments_dir / "P1-A1_result.json"
    p1a1_result.write_text(json.dumps({"ok": True}))
    cr2 = check_remaining(manifest_path, "assignments", str(tmp_path))
    context = json.loads(Path(cr2["current_item_path"]).read_text())
    assert str(p1a1_result) in context["prior_results"]


def test_check_remaining_raises_on_missing_result_dir(tmp_path):
    """Manifests without result_dir must fail loudly, not silently mark items failed."""
    from autoskillit.planner import check_remaining

    manifest = {
        "pass_name": "assignments",
        "items": [
            {
                "id": "P1-A1",
                "name": "X",
                "status": "processing",
                "metadata": {},
                "result_path": None,
            }
        ],
    }
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="result_dir"):
        check_remaining(str(mf), "assignments", str(tmp_path))


def test_build_assignment_manifest_stores_result_dir(tmp_path):
    """build_assignment_manifest embeds result_dir in the manifest it produces."""
    from autoskillit.planner import build_assignment_manifest

    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    (phases_dir / "P1_result.json").write_text(
        json.dumps(
            {
                "id": "P1",
                "name": "A",
                "ordering": 1,
                "assignments_preview": ["X"],
            }
        )
    )
    result = build_assignment_manifest(
        phases_dir=str(phases_dir),
        assignments_dir=str(assignments_dir),
        output_dir=str(tmp_path),
    )
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["result_dir"] == str(assignments_dir.resolve())


def test_build_wp_manifest_stores_result_dir(tmp_path):
    """build_wp_manifest embeds result_dir in the manifest it produces."""
    from autoskillit.planner import build_wp_manifest

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir()
    (assignments_dir / "P1-A1_result.json").write_text(
        json.dumps(
            {
                "id": "P1-A1",
                "name": "A1",
                "proposed_work_packages": [{"title": "WP1", "name": "WP1", "metadata": {}}],
            }
        )
    )
    result = build_wp_manifest(
        assignments_dir=str(assignments_dir),
        work_packages_dir=str(wp_dir),
        output_dir=str(tmp_path),
    )
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["result_dir"] == str(wp_dir.resolve())


def test_build_assignment_manifest_basic(tmp_path):
    """Phase results with assignments produce a valid manifest."""
    from autoskillit.planner import build_assignment_manifest

    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    (phases_dir / "phase_1_result.json").write_text(
        json.dumps(make_phase_result(1, assignments_preview=["First assignment"]))
    )

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
        json.dumps(make_phase_result(2, assignments_preview=["Phase2-A1"]))
    )
    (phases_dir / "phase_1_result.json").write_text(
        json.dumps(make_phase_result(1, assignments_preview=["Phase1-A2", "Phase1-A1"]))
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

    (assignments_dir / "P1-A1_result.json").write_text(
        json.dumps(
            make_assignment_result(
                1,
                1,
                proposed_work_packages=[
                    {
                        "id_suffix": "WP1",
                        "name": "First WP",
                        "scope": "do thing",
                        "estimated_files": ["a.py"],
                    }
                ],
            )
        )
    )

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
            make_assignment_result(
                1,
                2,
                proposed_work_packages=[
                    {"id_suffix": "WP1", "name": "P1A2WP1", "scope": "", "estimated_files": []}
                ],
            )
        )
    )
    (assignments_dir / "P1-A1_result.json").write_text(
        json.dumps(
            make_assignment_result(
                1,
                1,
                proposed_work_packages=[
                    {"id_suffix": "WP2", "name": "P1A1WP2", "scope": "", "estimated_files": []},
                    {"id_suffix": "WP1", "name": "P1A1WP1", "scope": "", "estimated_files": []},
                ],
            )
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


def test_build_phase_assignment_manifest_creates_one_item_per_phase(tmp_path):
    """P1 with 3 assignments and P2 with 2 assignments produce a 2-item manifest."""
    from autoskillit.planner import build_phase_assignment_manifest

    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    output_dir = tmp_path / "assignments"
    output_dir.mkdir()

    (phases_dir / "P1_result.json").write_text(
        json.dumps(
            make_phase_result(1, assignments_preview=["Auth Setup", "DB Schema", "API Layer"])
        )
    )
    (phases_dir / "P2_result.json").write_text(
        json.dumps(make_phase_result(2, assignments_preview=["CLI Integration", "Config Loading"]))
    )

    result = build_phase_assignment_manifest(str(phases_dir), str(output_dir))

    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert len(manifest["items"]) == 2

    p1_item = next(i for i in manifest["items"] if i["id"] == "P1")
    p2_item = next(i for i in manifest["items"] if i["id"] == "P2")
    assert p1_item["metadata"]["assignment_count"] == 3
    assert len(p1_item["metadata"]["assignment_names"]) == 3
    assert p2_item["metadata"]["assignment_count"] == 2
    assert len(p2_item["metadata"]["assignment_names"]) == 2


def test_build_phase_assignment_manifest_item_ids_match_phase_ids(tmp_path):
    """Item IDs in the manifest match phase IDs (P1, P2) not assignment IDs."""
    from autoskillit.planner import build_phase_assignment_manifest

    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    output_dir = tmp_path / "assignments"
    output_dir.mkdir()

    (phases_dir / "P1_result.json").write_text(
        json.dumps(make_phase_result(1, assignments_preview=["Task A"]))
    )
    (phases_dir / "P2_result.json").write_text(
        json.dumps(make_phase_result(2, assignments_preview=["Task B"]))
    )

    result = build_phase_assignment_manifest(str(phases_dir), str(output_dir))

    manifest = json.loads(Path(result["manifest_path"]).read_text())
    item_ids = {i["id"] for i in manifest["items"]}
    assert item_ids == {"P1", "P2"}


def test_build_phase_assignment_manifest_items_start_pending(tmp_path):
    """All items in the manifest start with status 'pending'."""
    from autoskillit.planner import build_phase_assignment_manifest

    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    output_dir = tmp_path / "assignments"
    output_dir.mkdir()

    (phases_dir / "P1_result.json").write_text(
        json.dumps(make_phase_result(1, assignments_preview=["Task X", "Task Y"]))
    )

    result = build_phase_assignment_manifest(str(phases_dir), str(output_dir))

    manifest = json.loads(Path(result["manifest_path"]).read_text())
    for item in manifest["items"]:
        assert item["status"] == "pending", f"item {item['id']} status was {item['status']!r}"


# ---------------------------------------------------------------------------
# build_phase_wp_manifest tests (T1–T7)
# ---------------------------------------------------------------------------


def test_build_phase_wp_manifest_groups_by_phase(tmp_path):
    """T1: Two phases produce a manifest with exactly 2 items."""
    from autoskillit.planner import build_phase_wp_manifest

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    (assignments_dir / "P1-A1_result.json").write_text(
        json.dumps(
            make_assignment_result(
                1,
                1,
                proposed_work_packages=[
                    {"name": "WP1", "scope": "s", "estimated_files": ["a.py"]},
                    {"name": "WP2", "scope": "s", "estimated_files": ["b.py"]},
                    {"name": "WP3", "scope": "s", "estimated_files": ["c.py"]},
                ],
            )
        )
    )
    (assignments_dir / "P1-A2_result.json").write_text(
        json.dumps(
            make_assignment_result(
                1,
                2,
                proposed_work_packages=[
                    {"name": "WP1", "scope": "s", "estimated_files": ["d.py"]},
                    {"name": "WP2", "scope": "s", "estimated_files": ["e.py"]},
                    {"name": "WP3", "scope": "s", "estimated_files": ["f.py"]},
                ],
            )
        )
    )
    (assignments_dir / "P2-A1_result.json").write_text(
        json.dumps(
            make_assignment_result(
                2,
                1,
                proposed_work_packages=[
                    {"name": "WP1", "scope": "s", "estimated_files": ["g.py"]},
                    {"name": "WP2", "scope": "s", "estimated_files": ["h.py"]},
                ],
            )
        )
    )

    result = build_phase_wp_manifest(str(assignments_dir), str(output_dir))

    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert len(manifest["items"]) == 2
    item_ids = [i["id"] for i in manifest["items"]]
    assert item_ids == ["P1", "P2"]


def test_build_phase_wp_manifest_metadata_carries_wp_details(tmp_path):
    """T2: Phase metadata contains wp_count, wp_ids, wp_names, wp_scopes, wp_estimated_files."""
    from autoskillit.planner import build_phase_wp_manifest

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    (assignments_dir / "P1-A1_result.json").write_text(
        json.dumps(
            make_assignment_result(
                1,
                1,
                proposed_work_packages=[
                    {"name": "Alpha", "scope": "scope-a", "estimated_files": ["a.py"]},
                    {"name": "Beta", "scope": "scope-b", "estimated_files": ["b.py", "c.py"]},
                ],
            )
        )
    )
    (assignments_dir / "P1-A2_result.json").write_text(
        json.dumps(
            make_assignment_result(
                1,
                2,
                proposed_work_packages=[
                    {"name": "Gamma", "scope": "scope-c", "estimated_files": ["d.py"]},
                ],
            )
        )
    )

    result = build_phase_wp_manifest(str(assignments_dir), str(output_dir))

    manifest = json.loads(Path(result["manifest_path"]).read_text())
    p1 = manifest["items"][0]
    meta = p1["metadata"]
    assert meta["wp_count"] == 3
    assert meta["wp_ids"] == ["P1-A1-WP1", "P1-A1-WP2", "P1-A2-WP1"]
    assert meta["wp_names"] == ["Alpha", "Beta", "Gamma"]
    assert meta["wp_scopes"] == ["scope-a", "scope-b", "scope-c"]
    assert meta["wp_estimated_files"] == [["a.py"], ["b.py", "c.py"], ["d.py"]]


def test_build_phase_wp_manifest_pass_name_and_result_dir(tmp_path):
    """T3: Manifest has pass_name 'phase_work_packages' and result_dir pointing to wp_sentinels."""
    from autoskillit.planner import build_phase_wp_manifest

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    (assignments_dir / "P1-A1_result.json").write_text(
        json.dumps(
            make_assignment_result(
                1,
                1,
                proposed_work_packages=[{"name": "WP1", "scope": "", "estimated_files": []}],
            )
        )
    )

    result = build_phase_wp_manifest(str(assignments_dir), str(output_dir))

    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["pass_name"] == "phase_work_packages"
    assert "wp_sentinels" in manifest["result_dir"]
    assert Path(manifest["result_dir"]).exists()


def test_build_phase_wp_manifest_initializes_wp_index(tmp_path):
    """T4: wp_index.json exists and contains [] after calling build_phase_wp_manifest."""
    from autoskillit.planner import build_phase_wp_manifest

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    (assignments_dir / "P1-A1_result.json").write_text(
        json.dumps(
            make_assignment_result(
                1,
                1,
                proposed_work_packages=[{"name": "WP1", "scope": "", "estimated_files": []}],
            )
        )
    )

    build_phase_wp_manifest(str(assignments_dir), str(output_dir))

    wp_index = output_dir / "wp_index.json"
    assert wp_index.exists()
    assert json.loads(wp_index.read_text()) == []


def test_build_phase_wp_manifest_sorts_by_phase_number(tmp_path):
    """T5: Items are sorted by phase number regardless of input order."""
    from autoskillit.planner import build_phase_wp_manifest

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    for pn in (3, 1, 2):
        (assignments_dir / f"P{pn}-A1_result.json").write_text(
            json.dumps(
                make_assignment_result(
                    pn,
                    1,
                    proposed_work_packages=[
                        {"name": f"WP-P{pn}", "scope": "", "estimated_files": []}
                    ],
                )
            )
        )

    result = build_phase_wp_manifest(str(assignments_dir), str(output_dir))

    manifest = json.loads(Path(result["manifest_path"]).read_text())
    ids = [i["id"] for i in manifest["items"]]
    assert ids == ["P1", "P2", "P3"]


def test_build_phase_wp_manifest_empty_assignments_dir(tmp_path):
    """T6: Empty assignments directory produces manifest with items: []."""
    from autoskillit.planner import build_phase_wp_manifest

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    result = build_phase_wp_manifest(str(assignments_dir), str(output_dir))

    assert result["total_count"] == "0"
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["items"] == []


def test_build_phase_wp_manifest_creates_sentinel_dir(tmp_path):
    """T7: The callable creates wp_sentinels/ directory."""
    from autoskillit.planner import build_phase_wp_manifest

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    build_phase_wp_manifest(str(assignments_dir), str(output_dir))

    sentinel_dir = output_dir / "work_packages" / "wp_sentinels"
    assert sentinel_dir.is_dir()


def test_check_remaining_phase_work_packages_no_backstop(tmp_path):
    """T15: phase_work_packages pass_name does NOT call _backstop_wp_index."""
    from autoskillit.planner import check_remaining

    sentinel_dir = tmp_path / "sentinels"
    sentinel_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (sentinel_dir / "P1_result.json").write_text('{"ok": true}')

    manifest = {
        "pass_name": "phase_work_packages",
        "result_dir": str(sentinel_dir),
        "created_at": "2026-04-24T00:00:00Z",
        "items": [
            {
                "id": "P1",
                "name": "Phase 1",
                "status": "processing",
                "result_path": None,
                "metadata": {},
            },
        ],
    }
    manifest_path = tmp_path / "phase_wp_manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    with patch("autoskillit.planner.manifests._backstop_wp_index") as mock_backstop:
        check_remaining(str(manifest_path), "phase_work_packages", str(output_dir))

    mock_backstop.assert_not_called()


# ---------------------------------------------------------------------------
# finalize_wp_manifest tests (T8–T11)
# ---------------------------------------------------------------------------


def test_finalize_wp_manifest_from_result_files(tmp_path):
    """T8: 4 result files produce wp_manifest.json with 4 items, all status done."""
    from autoskillit.planner import finalize_wp_manifest

    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    for i in range(1, 5):
        wp_id = f"P1-A1-WP{i}"
        (wp_dir / f"{wp_id}_result.json").write_text(json.dumps(make_wp_result(wp_id)))

    result = finalize_wp_manifest(str(wp_dir), str(output_dir))

    assert result["total_count"] == "4"
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert len(manifest["items"]) == 4
    for item in manifest["items"]:
        assert item["status"] == "done"
        assert item["result_path"]
        assert item["id"]
        assert item["name"]


def test_finalize_wp_manifest_skips_non_result_files(tmp_path):
    """T9: Non-result files (wp_manifest.json, wp_index.json, sentinel subdir) are skipped."""
    from autoskillit.planner import finalize_wp_manifest

    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    wp_id = "P1-A1-WP1"
    (wp_dir / f"{wp_id}_result.json").write_text(json.dumps(make_wp_result(wp_id)))
    (wp_dir / "wp_manifest.json").write_text('{"pass_name": "old"}')
    (wp_dir / "wp_index.json").write_text("[]")
    sentinel_dir = wp_dir / "wp_sentinels"
    sentinel_dir.mkdir()
    (sentinel_dir / "P1_result.json").write_text('{"ok": true}')

    result = finalize_wp_manifest(str(wp_dir), str(output_dir))

    assert result["total_count"] == "1"
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert len(manifest["items"]) == 1
    assert manifest["items"][0]["id"] == wp_id


def test_finalize_wp_manifest_empty_dir(tmp_path):
    """T10: Empty work_packages/ produces manifest with items: []."""
    from autoskillit.planner import finalize_wp_manifest

    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    result = finalize_wp_manifest(str(wp_dir), str(output_dir))

    assert result["total_count"] == "0"
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["items"] == []


def test_finalize_wp_manifest_regenerates_wp_index(tmp_path):
    """T11: wp_index.json is regenerated with compact entries sorted by WP ID."""
    from autoskillit.planner import finalize_wp_manifest

    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    for wp_id in ("P2-A1-WP1", "P1-A1-WP1", "P1-A2-WP1"):
        (wp_dir / f"{wp_id}_result.json").write_text(json.dumps(make_wp_result(wp_id)))

    finalize_wp_manifest(str(wp_dir), str(output_dir))

    index = json.loads((output_dir / "wp_index.json").read_text())
    assert len(index) == 3
    ids = [e["id"] for e in index]
    assert ids == ["P1-A1-WP1", "P1-A2-WP1", "P2-A1-WP1"]
    for entry in index:
        assert "id" in entry
        assert "name" in entry
        assert "summary" in entry
