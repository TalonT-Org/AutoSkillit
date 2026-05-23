"""Tests for autoskillit.planner.merge — extract_item and replace_item (item-level CRUD)."""

from __future__ import annotations

import json

import pytest

from autoskillit.planner.merge import extract_item, replace_item

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


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


def test_replace_item_nonexistent_replacement_file_raises(tmp_path) -> None:
    src = tmp_path / "combined.json"
    src.write_text(json.dumps({"phases": [{"id": "P1", "name": "x"}], "schema_version": 1}))

    with pytest.raises(ValueError, match="Replacement file not found"):
        replace_item(str(src), "P1", str(tmp_path / "nonexistent.json"))


def test_replace_item_corrupt_replacement_json_raises(tmp_path) -> None:
    src = tmp_path / "combined.json"
    src.write_text(json.dumps({"phases": [{"id": "P1", "name": "x"}], "schema_version": 1}))
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")

    with pytest.raises(ValueError, match="Invalid JSON"):
        replace_item(str(src), "P1", str(corrupt))
