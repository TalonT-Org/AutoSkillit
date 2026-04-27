from __future__ import annotations

import json

import pytest

from autoskillit.planner.merge import build_plan_snapshot, extract_item, merge_files, replace_item

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_merge_files_creates_combined_document(tmp_path):
    """merge_files writes a PlanDocument with the given key populated."""
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
        task="my task",
        source_dir="/src",
    )

    assert result["merged_path"] == str(out)
    assert result["item_count"] == "2"  # item_count is always str per MCP tool contract
    data = json.loads(out.read_text())
    assert data["task"] == "my task"
    assert data["source_dir"] == "/src"
    assert {p["id"] for p in data["phases"]} == {"P1", "P2"}


def test_merge_files_schema_version_1(tmp_path):
    """Output always carries schema_version: 1."""
    p = tmp_path / "p1.json"
    p.write_text(json.dumps({"id": "P1", "name": "x"}))
    out = tmp_path / "combined.json"

    merge_files(file_paths=[str(p)], output_path=str(out), key="phases")

    assert json.loads(out.read_text())["schema_version"] == 1


def test_merge_files_accumulates_existing(tmp_path):
    """merge_files appends to existing key list when output already exists."""
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
    assert result["item_count"] == "2"  # item_count is always str per MCP tool contract


def test_merge_files_deduplicates_by_id(tmp_path):
    """Re-merging a file with same id does not create duplicates."""
    item = {"id": "P1", "name": "Phase 1"}
    existing = {"task": "", "source_dir": "", "phases": [item], "schema_version": 1}
    out = tmp_path / "combined.json"
    out.write_text(json.dumps(existing))
    dup_file = tmp_path / "p1_dup.json"
    dup_file.write_text(json.dumps(item))

    merge_files(file_paths=[str(dup_file)], output_path=str(out), key="phases")

    assert len(json.loads(out.read_text())["phases"]) == 1


def test_merge_files_strict_raises_on_missing_file(tmp_path):
    """strict=True (default) raises ValueError for nonexistent input file."""
    with pytest.raises(ValueError, match="File not found"):
        merge_files(
            file_paths=["/nonexistent/path.json"],
            output_path=str(tmp_path / "out.json"),
            key="phases",
        )


def test_merge_files_non_strict_collects_errors(tmp_path):
    """strict=False collects errors for missing files and continues."""
    result = merge_files(
        file_paths=["/nonexistent/path.json"],
        output_path=str(tmp_path / "out.json"),
        key="phases",
        strict=False,
    )
    assert "errors" in result
    assert len(result["errors"]) == 1


def test_merge_files_non_strict_invalid_json(tmp_path):
    """strict=False collects errors for malformed JSON and continues."""
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
    """merge_files raises ValueError for an unrecognised tier key."""
    with pytest.raises(ValueError, match="Invalid key"):
        merge_files(
            file_paths=[],
            output_path=str(tmp_path / "out.json"),
            key="unknown_tier",
        )


def test_extract_item_writes_extracted_item(tmp_path):
    phases = [{"id": "P1", "name": "Phase 1"}, {"id": "P2", "name": "Phase 2"}]
    doc = {"task": "", "source_dir": "", "phases": phases, "schema_version": 1}
    src = tmp_path / "combined.json"
    src.write_text(json.dumps(doc))
    out = tmp_path / "extracted.json"

    result = extract_item(source_path=str(src), item_id="P2", output_path=str(out))

    assert result["extracted_path"] == str(out)
    extracted = json.loads(out.read_text())
    assert extracted["id"] == "P2"
    assert extracted["schema_version"] == 1


def test_extract_item_missing_id_raises(tmp_path):
    doc = {"task": "", "source_dir": "", "phases": [], "schema_version": 1}
    src = tmp_path / "combined.json"
    src.write_text(json.dumps(doc))

    with pytest.raises(ValueError, match="not found"):
        extract_item(
            source_path=str(src),
            item_id="MISSING",
            output_path=str(tmp_path / "out.json"),
        )


def test_extract_item_missing_file_raises(tmp_path):
    with pytest.raises(ValueError, match="Source file not found"):
        extract_item(
            source_path=str(tmp_path / "nonexistent.json"),
            item_id="P1",
            output_path=str(tmp_path / "out.json"),
        )


def test_extract_item_searches_all_tiers(tmp_path):
    """extract_item finds items in phases, assignments, and work_packages."""
    doc = {
        "task": "",
        "source_dir": "",
        "phases": [{"id": "P1"}],
        "assignments": [{"id": "P1-A1"}],
        "work_packages": [{"id": "P1-A1-WP1"}],
        "schema_version": 1,
    }
    src = tmp_path / "combined.json"
    src.write_text(json.dumps(doc))
    out = tmp_path / "extracted.json"

    extract_item(source_path=str(src), item_id="P1-A1-WP1", output_path=str(out))
    assert json.loads(out.read_text())["id"] == "P1-A1-WP1"


def test_replace_item_updates_combined_document(tmp_path):
    phases = [{"id": "P1", "name": "Old"}, {"id": "P2", "name": "Phase 2"}]
    doc = {"task": "", "source_dir": "", "phases": phases, "schema_version": 1}
    src = tmp_path / "combined.json"
    src.write_text(json.dumps(doc))
    rep_file = tmp_path / "rep.json"
    rep_file.write_text(json.dumps({"id": "P1", "name": "New", "goal": "updated"}))

    result = replace_item(source_path=str(src), item_id="P1", replacement_path=str(rep_file))

    assert result["replaced_id"] == "P1"
    assert result["updated_path"] == str(src)
    data = json.loads(src.read_text())
    p1 = next(p for p in data["phases"] if p["id"] == "P1")
    assert p1["name"] == "New"
    assert p1["goal"] == "updated"
    assert len(data["phases"]) == 2


def test_replace_item_in_assignments_tier(tmp_path):
    """replace_item correctly updates items in the assignments tier."""
    doc = {
        "task": "",
        "source_dir": "",
        "phases": [],
        "assignments": [{"id": "P1-A1", "name": "Old assignment"}],
        "schema_version": 1,
    }
    src = tmp_path / "combined.json"
    src.write_text(json.dumps(doc))
    rep_file = tmp_path / "rep.json"
    rep_file.write_text(json.dumps({"id": "P1-A1", "name": "Updated assignment"}))

    result = replace_item(source_path=str(src), item_id="P1-A1", replacement_path=str(rep_file))

    assert result["replaced_id"] == "P1-A1"
    data = json.loads(src.read_text())
    a1 = next(a for a in data["assignments"] if a["id"] == "P1-A1")
    assert a1["name"] == "Updated assignment"


def test_replace_item_in_work_packages_tier(tmp_path):
    """replace_item correctly updates items in the work_packages tier."""
    doc = {
        "task": "",
        "source_dir": "",
        "phases": [],
        "work_packages": [{"id": "P1-A1-WP1", "name": "Old WP"}],
        "schema_version": 1,
    }
    src = tmp_path / "combined.json"
    src.write_text(json.dumps(doc))
    rep_file = tmp_path / "rep.json"
    rep_file.write_text(json.dumps({"id": "P1-A1-WP1", "name": "Updated WP"}))

    result = replace_item(
        source_path=str(src), item_id="P1-A1-WP1", replacement_path=str(rep_file)
    )

    assert result["replaced_id"] == "P1-A1-WP1"
    data = json.loads(src.read_text())
    wp1 = next(w for w in data["work_packages"] if w["id"] == "P1-A1-WP1")
    assert wp1["name"] == "Updated WP"


def test_replace_item_missing_id_raises(tmp_path):
    doc = {"task": "", "source_dir": "", "phases": [], "schema_version": 1}
    src = tmp_path / "combined.json"
    src.write_text(json.dumps(doc))
    rep_file = tmp_path / "rep.json"
    rep_file.write_text(json.dumps({"id": "MISSING"}))

    with pytest.raises(ValueError, match="not found"):
        replace_item(source_path=str(src), item_id="MISSING", replacement_path=str(rep_file))


def test_replace_item_preserves_schema_version(tmp_path):
    doc = {
        "task": "",
        "source_dir": "",
        "phases": [{"id": "P1", "name": "x"}],
        "schema_version": 1,
    }
    src = tmp_path / "combined.json"
    src.write_text(json.dumps(doc))
    rep_file = tmp_path / "rep.json"
    rep_file.write_text(json.dumps({"id": "P1", "name": "updated"}))

    replace_item(source_path=str(src), item_id="P1", replacement_path=str(rep_file))

    assert json.loads(src.read_text())["schema_version"] == 1


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
        task="my task",
        source_dir="/src",
    )

    assert result["snapshot_path"] == str(out)
    assert "P1" in result["phase_ids"]
    assert "P2" in result["phase_ids"]


def test_build_plan_snapshot_writes_short_form_only(tmp_path):
    """PhaseShort must not include elaborated fields; parallel workers receive only these keys."""
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
        phases_dir=str(phases_dir), output_path=str(out), task="t", source_dir="/s"
    )

    data = json.loads(out.read_text())
    assert data["task"] == "t"
    assert data["source_dir"] == "/s"
    assert data["schema_version"] == 1
    phase = data["phases"][0]
    assert set(phase.keys()) == {"id", "name", "goal", "scope", "ordering"}


def test_build_plan_snapshot_projects_ordering(tmp_path) -> None:
    """ordering is the sort key in build_plan_snapshot; validate_phase_result raises if absent."""
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

    build_plan_snapshot(str(phases_dir), str(out), task="test", source_dir="/src")

    doc = json.loads(out.read_text())
    phase = doc["phases"][0]
    assert phase["ordering"] == 1
