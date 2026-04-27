from __future__ import annotations

import json

import pytest

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
    from autoskillit.planner.merge import merge_files

    result = merge_files(
        file_paths=file_paths,
        output_path=str(out),
        key="phases",
        task="my task",
        source_dir="/src",
    )

    assert result["merged_path"] == str(out)
    assert result["item_count"] == "2"
    data = json.loads(out.read_text())
    assert data["task"] == "my task"
    assert data["source_dir"] == "/src"
    assert {p["id"] for p in data["phases"]} == {"P1", "P2"}


def test_merge_files_schema_version_1(tmp_path):
    """Output always carries schema_version: 1."""
    p = tmp_path / "p1.json"
    p.write_text(json.dumps({"id": "P1", "name": "x"}))
    out = tmp_path / "combined.json"

    from autoskillit.planner.merge import merge_files

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

    from autoskillit.planner.merge import merge_files

    result = merge_files(file_paths=[str(new_file)], output_path=str(out), key="phases")

    data = json.loads(out.read_text())
    assert len(data["phases"]) == 2
    assert result["item_count"] == "2"


def test_merge_files_deduplicates_by_id(tmp_path):
    """Re-merging a file with same id does not create duplicates."""
    item = {"id": "P1", "name": "Phase 1"}
    existing = {"task": "", "source_dir": "", "phases": [item], "schema_version": 1}
    out = tmp_path / "combined.json"
    out.write_text(json.dumps(existing))
    dup_file = tmp_path / "p1_dup.json"
    dup_file.write_text(json.dumps(item))

    from autoskillit.planner.merge import merge_files

    merge_files(file_paths=[str(dup_file)], output_path=str(out), key="phases")

    assert len(json.loads(out.read_text())["phases"]) == 1


def test_merge_files_strict_raises_on_missing_file(tmp_path):
    """strict=True (default) raises ValueError for nonexistent input file."""
    from autoskillit.planner.merge import merge_files

    with pytest.raises(ValueError, match="File not found"):
        merge_files(
            file_paths=["/nonexistent/path.json"],
            output_path=str(tmp_path / "out.json"),
            key="phases",
        )


def test_merge_files_non_strict_collects_errors(tmp_path):
    """strict=False collects errors for missing files and continues."""
    from autoskillit.planner.merge import merge_files

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
    from autoskillit.planner.merge import merge_files

    result = merge_files(
        file_paths=[str(bad)],
        output_path=str(tmp_path / "out.json"),
        key="phases",
        strict=False,
    )
    assert "errors" in result


def test_extract_item_writes_extracted_item(tmp_path):
    phases = [{"id": "P1", "name": "Phase 1"}, {"id": "P2", "name": "Phase 2"}]
    doc = {"task": "", "source_dir": "", "phases": phases, "schema_version": 1}
    src = tmp_path / "combined.json"
    src.write_text(json.dumps(doc))
    out = tmp_path / "extracted.json"

    from autoskillit.planner.merge import extract_item

    result = extract_item(source_path=str(src), item_id="P2", output_path=str(out))

    assert result["extracted_path"] == str(out)
    assert json.loads(out.read_text())["id"] == "P2"


def test_extract_item_missing_id_raises(tmp_path):
    doc = {"task": "", "source_dir": "", "phases": [], "schema_version": 1}
    src = tmp_path / "combined.json"
    src.write_text(json.dumps(doc))

    from autoskillit.planner.merge import extract_item

    with pytest.raises(ValueError, match="not found"):
        extract_item(
            source_path=str(src),
            item_id="MISSING",
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

    from autoskillit.planner.merge import extract_item

    extract_item(source_path=str(src), item_id="P1-A1-WP1", output_path=str(out))
    assert json.loads(out.read_text())["id"] == "P1-A1-WP1"


def test_replace_item_updates_combined_document(tmp_path):
    phases = [{"id": "P1", "name": "Old"}, {"id": "P2", "name": "Phase 2"}]
    doc = {"task": "", "source_dir": "", "phases": phases, "schema_version": 1}
    src = tmp_path / "combined.json"
    src.write_text(json.dumps(doc))
    rep_file = tmp_path / "rep.json"
    rep_file.write_text(json.dumps({"id": "P1", "name": "New", "goal": "updated"}))

    from autoskillit.planner.merge import replace_item

    result = replace_item(
        source_path=str(src), item_id="P1", replacement_path=str(rep_file)
    )

    assert result["replaced_id"] == "P1"
    assert result["updated_path"] == str(src)
    data = json.loads(src.read_text())
    p1 = next(p for p in data["phases"] if p["id"] == "P1")
    assert p1["name"] == "New"
    assert p1["goal"] == "updated"
    assert len(data["phases"]) == 2


def test_replace_item_missing_id_raises(tmp_path):
    doc = {"task": "", "source_dir": "", "phases": [], "schema_version": 1}
    src = tmp_path / "combined.json"
    src.write_text(json.dumps(doc))
    rep_file = tmp_path / "rep.json"
    rep_file.write_text(json.dumps({"id": "MISSING"}))

    from autoskillit.planner.merge import replace_item

    with pytest.raises(ValueError, match="not found"):
        replace_item(
            source_path=str(src), item_id="MISSING", replacement_path=str(rep_file)
        )


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

    from autoskillit.planner.merge import replace_item

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

    from autoskillit.planner.merge import build_plan_snapshot

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
    """Snapshot phases contain only PhaseShort fields: id, name, goal, scope."""
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    r = {
        "id": "P1",
        "name": "Phase One",
        "ordering": 1,
        "scope": ["s1"],
        "relationship_notes": "should not appear",
        "assignments_preview": ["A1"],
    }
    (phases_dir / "P1_result.json").write_text(json.dumps(r))
    out = tmp_path / "snapshot.json"

    from autoskillit.planner.merge import build_plan_snapshot

    build_plan_snapshot(
        phases_dir=str(phases_dir), output_path=str(out), task="t", source_dir="/s"
    )

    data = json.loads(out.read_text())
    assert data["task"] == "t"
    assert data["source_dir"] == "/s"
    assert data["schema_version"] == 1
    phase = data["phases"][0]
    assert set(phase.keys()) == {"id", "name", "goal", "scope"}
