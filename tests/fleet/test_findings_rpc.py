"""Tests for autoskillit.fleet._findings_rpc (T15–T21)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.fleet._findings_rpc import load_execution_map, parse_and_resume
from autoskillit.fleet._sidecar_rpc import write_sidecar_entry

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


# T15
def test_parse_and_resume_empty_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", "test-dispatch-001")
    result = parse_and_resume(
        "https://github.com/o/r/issues/1,https://github.com/o/r/issues/2",
        project_dir=str(tmp_path),
    )
    assert json.loads(result["remaining_urls_json"]) == [
        "https://github.com/o/r/issues/1",
        "https://github.com/o/r/issues/2",
    ]
    assert result["completed_count"] == "0"


# T16
def test_parse_and_resume_with_completed_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch_id = "test-dispatch-002"
    monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", dispatch_id)
    write_sidecar_entry(
        dispatch_id,
        "https://github.com/o/r/issues/1",
        "completed",
        project_dir=str(tmp_path),
    )
    result = parse_and_resume(
        "https://github.com/o/r/issues/1,https://github.com/o/r/issues/2",
        project_dir=str(tmp_path),
    )
    remaining = json.loads(result["remaining_urls_json"])
    assert remaining == ["https://github.com/o/r/issues/2"]
    assert result["completed_count"] == "1"


# T17
def test_parse_and_resume_single_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", "test-dispatch-003")
    result = parse_and_resume(
        "https://github.com/o/r/issues/5",
        project_dir=str(tmp_path),
    )
    remaining = json.loads(result["remaining_urls_json"])
    assert remaining == ["https://github.com/o/r/issues/5"]


# T18
def test_load_execution_map_valid(tmp_path: Path) -> None:
    map_data = {
        "groups": [
            {
                "name": "g1",
                "parallel": True,
                "issues": ["https://github.com/o/r/issues/1"],
            },
        ]
    }
    map_file = tmp_path / "bem_map.json"
    map_file.write_text(json.dumps(map_data))
    result = load_execution_map(str(map_file))
    groups = json.loads(result["groups_json"])
    assert len(groups) == 1
    assert groups[0]["name"] == "g1"
    assert groups[0]["parallel"] is True
    assert result["total_groups"] == "1"


# T19
def test_load_execution_map_missing_file(tmp_path: Path) -> None:
    result = load_execution_map(str(tmp_path / "nonexistent.json"))
    assert "error" in result


# T20
def test_load_execution_map_invalid_json(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("not json {{{")
    result = load_execution_map(str(bad_file))
    assert "error" in result


# T21
def test_parse_and_resume_reads_dispatch_id_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", "env-dispatch-99")
    result = parse_and_resume(
        "https://github.com/o/r/issues/7",
        project_dir=str(tmp_path),
    )
    assert "remaining_urls_json" in result
