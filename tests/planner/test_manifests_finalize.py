"""Tests for autoskillit.planner — finalize_wp_manifest and collect_tier_result_files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.planner.conftest import make_wp_result, write_json

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def _raw_wp(wp_id: str, **overrides: Any) -> dict[str, Any]:
    """Build a raw WP dict WITHOUT running validate_wp_result (allows invalid data)."""
    base: dict[str, Any] = {
        "id": wp_id,
        "name": f"WP {wp_id}",
        "summary": "",
        "goal": "",
        "deliverables": [f"src/mod_{wp_id}.py"],
        "technical_steps": [],
        "files_touched": [],
        "apis_defined": [],
        "apis_consumed": [],
        "depends_on": [],
        "acceptance_criteria": [],
    }
    base.update(overrides)
    return base


def test_finalize_wp_manifest_from_result_files(tmp_path):
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


def test_finalize_wp_manifest_warns_on_non_canonical_wp_filename(tmp_path):
    from autoskillit.planner import finalize_wp_manifest

    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    (wp_dir / "P1-A1-WP1_result.json").write_text(json.dumps(make_wp_result("P1-A1-WP1")))
    (wp_dir / "P1-A1-WPa_result.json").write_text(
        json.dumps(make_wp_result("P1-A1-WP1", name="NonCanonical"))
    )

    result = finalize_wp_manifest(str(wp_dir), str(output_dir))
    assert result["total_count"] == "1"


def test_finalize_wp_manifest_tolerates_known_non_result_files(tmp_path):
    from autoskillit.planner import finalize_wp_manifest

    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    (wp_dir / "P1-A1-WP1_result.json").write_text(json.dumps(make_wp_result("P1-A1-WP1")))
    (wp_dir / "wp_manifest.json").write_text('{"pass_name": "old"}')
    (wp_dir / "wp_index.json").write_text("[]")
    (wp_dir / "context_P1.json").write_text('{"id": "P1"}')
    sentinel_dir = wp_dir / "wp_sentinels"
    sentinel_dir.mkdir()
    (sentinel_dir / "P1_result.json").write_text('{"ok": true}')

    result = finalize_wp_manifest(str(wp_dir), str(output_dir))
    assert result["total_count"] == "1"


def test_collect_tier_result_files_multiple_non_canonical(tmp_path):
    from autoskillit.planner.schema import WP_RESULT_FILE_RE, collect_tier_result_files

    results_dir = tmp_path / "work_packages"
    results_dir.mkdir()

    (results_dir / "P1-A1-WP1_result.json").write_text(json.dumps(make_wp_result("P1-A1-WP1")))
    (results_dir / "P1-A1-WPa_result.json").write_text(
        json.dumps(make_wp_result("P1-A1-WP1", name="Alpha"))
    )
    (results_dir / "P1-A1-WP2a_result.json").write_text(
        json.dumps(make_wp_result("P1-A1-WP1", name="Alpha2"))
    )

    with pytest.raises(ValueError, match="P1-A1-WPa_result.json") as exc_info:
        collect_tier_result_files(results_dir, WP_RESULT_FILE_RE)
    error_msg = str(exc_info.value)
    assert "P1-A1-WPa_result.json" in error_msg
    assert "P1-A1-WP2a_result.json" in error_msg


def test_finalize_wp_manifest_empty_dir(tmp_path):
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
    from autoskillit.planner import finalize_wp_manifest

    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    for wp_id in ("P2-A1-WP1", "P1-A1-WP1", "P1-A2-WP1"):
        (wp_dir / f"{wp_id}_result.json").write_text(json.dumps(make_wp_result(wp_id)))

    finalize_wp_manifest(str(wp_dir), str(output_dir))

    index = json.loads((wp_dir / "wp_index.json").read_text())
    assert len(index) == 3
    ids = [e["id"] for e in index]
    assert ids == ["P1-A1-WP1", "P1-A2-WP1", "P2-A1-WP1"]
    for entry in index:
        assert "id" in entry
        assert "name" in entry
        assert "summary" in entry


def test_finalize_wp_manifest_corrupt_json_raises(tmp_path):
    from autoskillit.planner import finalize_wp_manifest

    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (wp_dir / "P1-A1-WP1_result.json").write_text("{not json")

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


def test_finalize_wp_manifest_writes_to_work_packages_dir(tmp_path):
    from autoskillit.planner import finalize_wp_manifest

    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir()
    (wp_dir / "P1-A1-WP1_result.json").write_text(json.dumps(make_wp_result("P1-A1-WP1")))

    result = finalize_wp_manifest(str(wp_dir), str(tmp_path))

    assert (wp_dir / "wp_manifest.json").exists()
    assert not (tmp_path / "wp_manifest.json").exists()
    assert "work_packages" in result["manifest_path"]


def test_finalize_wp_manifest_writes_wp_index_to_work_packages_dir(tmp_path):
    from autoskillit.planner import finalize_wp_manifest

    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir()
    (wp_dir / "P1-A1-WP1_result.json").write_text(json.dumps(make_wp_result("P1-A1-WP1")))

    finalize_wp_manifest(str(wp_dir), str(tmp_path))

    assert (wp_dir / "wp_index.json").exists()
    assert not (tmp_path / "wp_index.json").exists()


def test_finalize_wp_manifest_accumulates_all_validation_errors(tmp_path: Path) -> None:
    from autoskillit.planner.manifests import finalize_wp_manifest

    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir()

    write_json(wp_dir / "P1-A1-WP1_result.json", _raw_wp("P1-A1-WP1", deliverables=[]))
    write_json(
        wp_dir / "P1-A1-WP2_result.json",
        _raw_wp("P1-A1-WP2", deliverables=[f"f{i}.py" for i in range(6)]),
    )
    write_json(wp_dir / "P1-A1-WP3_result.json", make_wp_result("P1-A1-WP3"))

    with pytest.raises(ValueError, match=r"1 WP validation error") as exc_info:
        finalize_wp_manifest(str(wp_dir), str(tmp_path))

    msg = str(exc_info.value)
    assert "P1-A1-WP1_result.json" in msg
    assert "P1-A1-WP2_result.json" not in msg


def test_finalize_wp_manifest_upper_bound_violation_warns_not_fails(tmp_path: Path) -> None:
    from autoskillit.planner.manifests import finalize_wp_manifest

    wp_dir = tmp_path / "work_packages"
    wp_dir.mkdir()

    write_json(
        wp_dir / "P1-A1-WP1_result.json",
        _raw_wp("P1-A1-WP1", deliverables=[f"f{i}.py" for i in range(6)]),
    )

    with pytest.warns(UserWarning, match="has 6 deliverables"):
        result = finalize_wp_manifest(str(wp_dir), str(tmp_path))

    assert "manifest_path" in result
