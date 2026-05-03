from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.planner.conftest import make_assignment_result, make_phase_result, make_wp_result

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


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


# ---------------------------------------------------------------------------
# build_phase_assignment_manifest error branches
# ---------------------------------------------------------------------------


def test_build_phase_assignment_manifest_corrupt_json_raises(tmp_path):
    from autoskillit.planner import build_phase_assignment_manifest

    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (phases_dir / "bad_result.json").write_text("{not json")

    with pytest.raises(json.JSONDecodeError, match="Failed to parse"):
        build_phase_assignment_manifest(str(phases_dir), str(output_dir))


def test_build_phase_assignment_manifest_missing_required_keys_raises(tmp_path):
    from autoskillit.planner import build_phase_assignment_manifest

    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (phases_dir / "bad_result.json").write_text(json.dumps({"foo": "bar"}))

    with pytest.raises(ValueError, match="Invalid phase result in"):
        build_phase_assignment_manifest(str(phases_dir), str(output_dir))


def test_build_phase_assignment_manifest_empty_string_dir_raises(tmp_path):
    from autoskillit.planner import build_phase_assignment_manifest

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with pytest.raises(ValueError, match="phases_dir and output_dir must not be empty"):
        build_phase_assignment_manifest("", str(output_dir))


# ---------------------------------------------------------------------------
# build_phase_wp_manifest error branches
# ---------------------------------------------------------------------------


def test_build_phase_wp_manifest_corrupt_json_raises(tmp_path):
    from autoskillit.planner import build_phase_wp_manifest

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (assignments_dir / "bad_result.json").write_text("{not json")

    with pytest.raises(json.JSONDecodeError, match="Failed to parse"):
        build_phase_wp_manifest(str(assignments_dir), str(output_dir))


def test_build_phase_wp_manifest_empty_string_raises(tmp_path):
    from autoskillit.planner import build_phase_wp_manifest

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with pytest.raises(ValueError, match="assignments_dir and output_dir must not be empty"):
        build_phase_wp_manifest("", str(output_dir))


def test_build_phase_wp_manifest_nonexistent_dir_raises(tmp_path):
    from autoskillit.planner import build_phase_wp_manifest

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with pytest.raises(FileNotFoundError, match="assignments_dir does not exist"):
        build_phase_wp_manifest(str(tmp_path / "nonexistent"), str(output_dir))


# ---------------------------------------------------------------------------
# finalize_wp_manifest error branches
# ---------------------------------------------------------------------------


def test_finalize_wp_manifest_corrupt_json_raises(tmp_path):
    from autoskillit.planner import finalize_wp_manifest

    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (wp_dir / "bad_result.json").write_text("{not json")

    with pytest.raises(json.JSONDecodeError, match="Failed to parse"):
        finalize_wp_manifest(str(wp_dir), str(output_dir))


def test_finalize_wp_manifest_empty_string_raises(tmp_path):
    from autoskillit.planner import finalize_wp_manifest

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with pytest.raises(ValueError, match="work_packages_dir and output_dir must not be empty"):
        finalize_wp_manifest("", str(output_dir))


def test_finalize_wp_manifest_nonexistent_dir_raises(tmp_path):
    from autoskillit.planner import finalize_wp_manifest

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with pytest.raises(FileNotFoundError, match="work_packages_dir does not exist"):
        finalize_wp_manifest(str(tmp_path / "nonexistent"), str(output_dir))


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
