"""Tests for autoskillit.planner.merge — build_plan_snapshot."""

from __future__ import annotations

import json

import pytest

from autoskillit.planner.merge import build_plan_snapshot
from tests.planner.conftest import make_phase_result, write_task_file

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_build_plan_snapshot_produces_phase_ids(tmp_path):
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    for phase_id in ["P1", "P2"]:
        r = {
            "id": phase_id,
            "name": f"Phase {phase_id[1:]}",
            "ordering": int(phase_id[1:]),
        }
        (phases_dir / f"{phase_id}_result.json").write_text(json.dumps(r))
    out = tmp_path / "snapshot.json"

    result = build_plan_snapshot(
        phases_dir=str(phases_dir),
        output_path=str(out),
        task_file_path=write_task_file(tmp_path, "my task"),
        source_dir="/src",
    )

    assert result["snapshot_path"] == str(out)
    assert "P1" in result["phase_ids"]
    assert "P2" in result["phase_ids"]


def test_build_plan_snapshot_writes_short_form_only(tmp_path):
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    r = {
        "id": "P1",
        "name": "Phase One",
        "goal": "some goal",
        "ordering": 1,
        "scope": ["s1"],
        "relationship_notes": "should not appear",
        "assignments_preview": ["A1"],
    }
    (phases_dir / "P1_result.json").write_text(json.dumps(r))
    out = tmp_path / "snapshot.json"

    build_plan_snapshot(
        phases_dir=str(phases_dir),
        output_path=str(out),
        task_file_path=write_task_file(tmp_path, "t"),
        source_dir="/s",
    )

    data = json.loads(out.read_text())
    assert data["task"] == "t"
    assert data["source_dir"] == "/s"
    assert data["schema_version"] == 1
    phase = data["phases"][0]
    assert set(phase.keys()) == {"id", "name", "goal", "scope", "ordering"}


def test_build_plan_snapshot_projects_ordering(tmp_path) -> None:
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    result = {
        "id": "P1",
        "name": "Foundation",
        "goal": "Setup base",
        "scope": ["core"],
        "ordering": 1,
        "assignments_preview": [],
        "relationship_notes": "",
    }
    (phases_dir / "P1_result.json").write_text(json.dumps(result))
    out = tmp_path / "snapshot.json"

    build_plan_snapshot(
        str(phases_dir),
        str(out),
        task_file_path=write_task_file(tmp_path, "test"),
        source_dir="/src",
    )

    doc = json.loads(out.read_text())
    phase = doc["phases"][0]
    assert phase["ordering"] == 1


def test_build_plan_snapshot_happy_path_two_phases_sorted(tmp_path) -> None:
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    for phase_num, _ in [(2, 2), (1, 1)]:
        (phases_dir / f"P{phase_num}_result.json").write_text(
            json.dumps(make_phase_result(phase_num))
        )
    out = tmp_path / "snapshot.json"

    result = build_plan_snapshot(str(phases_dir), str(out))

    data = json.loads(out.read_text())
    assert len(data["phases"]) == 2
    assert data["phases"][0]["ordering"] == 1
    assert data["phases"][1]["ordering"] == 2
    assert "P1" in result["phase_ids"]
    assert "P2" in result["phase_ids"]


def test_build_plan_snapshot_warns_on_unrecognized_result_filename(tmp_path) -> None:
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    (phases_dir / "P1_result.json").write_text(json.dumps(make_phase_result(1)))
    (phases_dir / "bad_result.json").write_text("{not json")
    out = tmp_path / "snapshot.json"

    result = build_plan_snapshot(str(phases_dir), str(out))
    assert "P1" in result["phase_ids"]


def test_build_plan_snapshot_skips_corrupt_canonical_json(tmp_path) -> None:
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    (phases_dir / "P1_result.json").write_text(json.dumps(make_phase_result(1)))
    (phases_dir / "P2_result.json").write_text("{not valid json")
    out = tmp_path / "snapshot.json"

    result = build_plan_snapshot(str(phases_dir), str(out))

    data = json.loads(out.read_text())
    assert len(data["phases"]) == 1
    assert "P1" in result["phase_ids"]
    assert "P2" not in result["phase_ids"]


def test_build_plan_snapshot_nonexistent_phases_dir(tmp_path) -> None:
    out = tmp_path / "snapshot.json"

    result = build_plan_snapshot(str(tmp_path / "nonexistent"), str(out))

    data = json.loads(out.read_text())
    assert data["phases"] == []
    assert result["phase_ids"] == ""


def test_build_plan_snapshot_empty_dir_produces_empty_phases(tmp_path) -> None:
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    out = tmp_path / "snapshot.json"

    result = build_plan_snapshot(str(phases_dir), str(out))

    data = json.loads(out.read_text())
    assert data["phases"] == []
    assert result["phase_ids"] == ""


def test_build_plan_snapshot_warns_on_non_canonical_phase_filename(tmp_path) -> None:
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    out = tmp_path / "snapshot.json"

    (phases_dir / "P1_result.json").write_text(
        json.dumps({"id": "P1", "name": "Phase 1", "ordering": 1})
    )
    (phases_dir / "Phase1_result.json").write_text(
        json.dumps({"id": "Phase1", "name": "Phase One", "ordering": 1})
    )

    result = build_plan_snapshot(str(phases_dir), str(out))
    assert "P1" in result["phase_ids"]
    assert "Phase1" not in result["phase_ids"]


def test_build_plan_snapshot_reads_task_from_task_file_path(tmp_path) -> None:
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    (phases_dir / "P1_result.json").write_text(json.dumps(make_phase_result(1)))
    out = tmp_path / "snapshot.json"
    task_file = tmp_path / "task_desc.txt"
    task_file.write_text("Full task from file")

    build_plan_snapshot(str(phases_dir), str(out), task_file_path=str(task_file))

    data = json.loads(out.read_text())
    assert data["task"] == "Full task from file"
