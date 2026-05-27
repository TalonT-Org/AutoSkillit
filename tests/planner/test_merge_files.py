"""Tests for autoskillit.planner.merge — merge_files and merge_tier_results (core merge operations)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.planner.merge import merge_files, merge_tier_results
from tests.planner.conftest import (
    make_assignment_result,
    make_wp_result,
    write_json,
    write_task_file,
)

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_merge_files_creates_combined_document(tmp_path):
    items = [{"id": "P1", "name": "Phase 1"}, {"id": "P2", "name": "Phase 2"}]
    file_paths = []
    for item in items:
        p = tmp_path / f"{item['id']}_result.json"
        p.write_text(json.dumps(item))
        file_paths.append(str(p))

    out = tmp_path / "combined.json"

    result = merge_files(
        file_paths=file_paths,
        output_path=str(out),
        key="phases",
        task_file_path=write_task_file(tmp_path, "my task"),
        source_dir="/src",
    )

    assert result["merged_path"] == str(out)
    assert result["item_count"] == "2"
    data = json.loads(out.read_text())
    assert data["task"] == "my task"
    assert data["source_dir"] == "/src"
    assert {p["id"] for p in data["phases"]} == {"P1", "P2"}


def test_merge_files_schema_version_1(tmp_path):
    p = tmp_path / "p1.json"
    p.write_text(json.dumps({"id": "P1", "name": "x"}))
    out = tmp_path / "combined.json"

    merge_files(file_paths=[str(p)], output_path=str(out), key="phases")

    assert json.loads(out.read_text())["schema_version"] == 1


def test_merge_files_accumulates_existing(tmp_path):
    existing = {
        "task": "t",
        "source_dir": "/s",
        "phases": [{"id": "P1", "name": "Phase 1"}],
        "schema_version": 1,
    }
    out = tmp_path / "combined.json"
    out.write_text(json.dumps(existing))
    new_file = tmp_path / "p2.json"
    new_file.write_text(json.dumps({"id": "P2", "name": "Phase 2"}))

    result = merge_files(file_paths=[str(new_file)], output_path=str(out), key="phases")

    data = json.loads(out.read_text())
    assert len(data["phases"]) == 2
    assert result["item_count"] == "2"


def test_merge_files_deduplicates_by_id(tmp_path):
    item = {"id": "P1", "name": "Phase 1"}
    existing = {"task": "", "source_dir": "", "phases": [item], "schema_version": 1}
    out = tmp_path / "combined.json"
    out.write_text(json.dumps(existing))
    dup_file = tmp_path / "p1_dup.json"
    dup_file.write_text(json.dumps(item))

    merge_files(file_paths=[str(dup_file)], output_path=str(out), key="phases")

    assert len(json.loads(out.read_text())["phases"]) == 1


def test_merge_files_strict_raises_on_missing_file(tmp_path):
    with pytest.raises(ValueError, match="File not found"):
        merge_files(
            file_paths=["/nonexistent/path.json"],
            output_path=str(tmp_path / "out.json"),
            key="phases",
        )


def test_merge_files_non_strict_collects_errors(tmp_path):
    result = merge_files(
        file_paths=["/nonexistent/path.json"],
        output_path=str(tmp_path / "out.json"),
        key="phases",
        strict=False,
    )
    assert "errors" in result
    assert len(result["errors"]) == 1


def test_merge_files_non_strict_invalid_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json{{{")

    result = merge_files(
        file_paths=[str(bad)],
        output_path=str(tmp_path / "out.json"),
        key="phases",
        strict=False,
    )
    assert "errors" in result


def test_merge_files_invalid_key_raises(tmp_path):
    with pytest.raises(ValueError, match="Invalid key"):
        merge_files(
            file_paths=[],
            output_path=str(tmp_path / "out.json"),
            key="unknown_tier",
        )


def test_merge_tier_results_empty_dir_raises(tmp_path):
    empty_dir = tmp_path / "phases"
    empty_dir.mkdir()
    out = tmp_path / "combined.json"

    with pytest.raises(ValueError, match="No \\*_result.json files found"):
        merge_tier_results(str(empty_dir), str(out), "phases")


def test_merge_tier_results_single_file(tmp_path):
    results_dir = tmp_path / "phases"
    results_dir.mkdir()
    (results_dir / "P1_result.json").write_text(
        json.dumps({"id": "P1", "name": "Phase 1", "ordering": 1})
    )
    out = tmp_path / "combined.json"

    result = merge_tier_results(str(results_dir), str(out), "phases")

    assert result["item_count"] == "1"
    assert out.exists()
    data = json.loads(out.read_text())
    assert len(data["phases"]) == 1
    assert data["phases"][0]["id"] == "P1"


def test_merge_tier_results_reads_task_from_task_file_path(tmp_path):
    results_dir = tmp_path / "phases"
    results_dir.mkdir()
    (results_dir / "P1_result.json").write_text(
        json.dumps({"id": "P1", "name": "Phase 1", "ordering": 1})
    )
    out = tmp_path / "combined.json"
    task_file = tmp_path / "task_desc.txt"
    task_file.write_text("Full task from file")

    merge_tier_results(str(results_dir), str(out), "phases", task_file_path=str(task_file))

    data = json.loads(out.read_text())
    assert data["task"] == "Full task from file"


def test_merge_files_reads_task_from_task_file_path(tmp_path):
    item = {"id": "P1", "name": "Phase 1"}
    p = tmp_path / "P1_result.json"
    p.write_text(json.dumps(item))
    out = tmp_path / "combined.json"
    task_file = tmp_path / "task_desc.txt"
    task_file.write_text("Full task from file")

    merge_files([str(p)], str(out), "phases", task_file_path=str(task_file))

    data = json.loads(out.read_text())
    assert data["task"] == "Full task from file"


def test_merge_tier_results_merges_canonical_assignment_files(tmp_path):
    assign_dir = tmp_path / "assignments"
    assign_dir.mkdir()
    out = tmp_path / "combined.json"

    write_json(
        assign_dir / "P1-A1_result.json",
        make_assignment_result(1, 1),
    )

    task_file = write_task_file(tmp_path)
    merge_tier_results(str(assign_dir), str(out), "assignments", task_file_path=task_file)

    merged = json.loads(out.read_text())
    ids = [a["id"] for a in merged["assignments"]]
    assert "P1-A1" in ids


def test_merge_files_produces_indented_output(tmp_path: Path) -> None:
    out = tmp_path / "combined.json"
    item = tmp_path / "item.json"
    item.write_text(json.dumps({"id": "P1", "name": "Phase 1"}))
    merge_files(file_paths=[str(item)], output_path=str(out), key="phases")
    raw = out.read_text(encoding="utf-8")
    lines = raw.strip().splitlines()
    assert len(lines) > 1, "merge_files output must be multi-line (indented)"


def test_merge_tier_results_raises_on_excluded_assignment_files(tmp_path: Path) -> None:
    results_dir = tmp_path / "assignments"
    results_dir.mkdir()

    write_json(results_dir / "P1-A1_result.json", make_assignment_result(1, 1))
    write_json(
        results_dir / "P1_result.json",
        {"id": "P1", "name": "Phase 1", "ordering": 1, "goal": "x", "scope": []},
    )

    out = tmp_path / "combined.json"

    with pytest.raises(ValueError, match="excluded"):
        merge_tier_results(str(results_dir), str(out), "assignments")


def test_merge_tier_results_work_packages_contain_ancestry_fields(tmp_path: Path) -> None:
    wps_dir = tmp_path / "work_packages"
    wps_dir.mkdir()
    for wp_id in ("P1-A1-WP1", "P1-A1-WP2"):
        write_json(wps_dir / f"{wp_id}_result.json", make_wp_result(wp_id))
    out = tmp_path / "combined_wps.json"

    merge_tier_results(str(wps_dir), str(out), "work_packages")

    data = json.loads(out.read_text())
    for wp in data["work_packages"]:
        assert "phase_id" in wp, f"WP {wp['id']} missing phase_id"
        assert "assignment_id" in wp, f"WP {wp['id']} missing assignment_id"
