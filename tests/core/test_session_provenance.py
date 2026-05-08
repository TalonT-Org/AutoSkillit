"""Tests for core/runtime/session_provenance.py — provenance store for L2 sessions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core.runtime.session_provenance import (
    ProvenanceRecord,
    provenance_path,
    read_provenance_for_session,
    write_provenance_record,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _make_record(**overrides: str) -> ProvenanceRecord:
    defaults = {
        "session_id": "sess-1",
        "caller_session_id": "caller-1",
        "kitchen_id": "kitchen-1",
        "dispatch_id": "dispatch-1",
        "recipe_name": "test-recipe",
        "step_name": "step-a",
        "timestamp": "2026-01-01T00:00:00Z",
    }
    defaults.update(overrides)
    return ProvenanceRecord(**defaults)


class TestProvenancePath:
    def test_default_resolution(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_STATE_DIR", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
        result = provenance_path()
        assert result == tmp_path / ".autoskillit" / "temp" / "session_provenance.jsonl"

    def test_state_dir_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
        result = provenance_path()
        assert result == tmp_path / "session_provenance.jsonl"

    def test_campaign_id_namespacing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AUTOSKILLIT_STATE_DIR", raising=False)
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "camp-42")
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
        result = provenance_path()
        assert (
            result == tmp_path / ".autoskillit" / "temp" / "camp-42" / "session_provenance.jsonl"
        )

    def test_project_dir_argument(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_STATE_DIR", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        result = provenance_path(project_dir=tmp_path)
        assert result == tmp_path / ".autoskillit" / "temp" / "session_provenance.jsonl"


class TestWriteProvenanceRecord:
    def test_creates_parent_dirs_and_appends(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
        rec = _make_record()
        write_provenance_record(rec)
        path = tmp_path / "session_provenance.jsonl"
        assert path.exists()
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["session_id"] == "sess-1"
        assert data["caller_session_id"] == "caller-1"

    def test_appends_multiple_records(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
        write_provenance_record(_make_record(session_id="a"))
        write_provenance_record(_make_record(session_id="b"))
        path = tmp_path / "session_provenance.jsonl"
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["session_id"] == "a"
        assert json.loads(lines[1])["session_id"] == "b"

    def test_oserror_is_swallowed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path / "no-write"))
        read_only = tmp_path / "no-write"
        read_only.mkdir()
        read_only.chmod(0o444)
        write_provenance_record(_make_record())
        read_only.chmod(0o755)


class TestReadProvenanceForSession:
    def test_finds_matching_record(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
        write_provenance_record(_make_record(session_id="target"))
        write_provenance_record(_make_record(session_id="other"))
        result = read_provenance_for_session("target")
        assert result is not None
        assert result["session_id"] == "target"

    def test_returns_none_for_missing_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
        write_provenance_record(_make_record(session_id="other"))
        assert read_provenance_for_session("nonexistent") is None

    def test_returns_none_when_file_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
        assert read_provenance_for_session("any") is None

    def test_skips_malformed_lines(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
        path = tmp_path / "session_provenance.jsonl"
        path.write_text("not-json\n" + json.dumps({"session_id": "found"}) + "\n")
        result = read_provenance_for_session("found")
        assert result is not None
        assert result["session_id"] == "found"

    def test_skips_blank_lines(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
        path = tmp_path / "session_provenance.jsonl"
        path.write_text("\n\n" + json.dumps({"session_id": "found"}) + "\n\n")
        result = read_provenance_for_session("found")
        assert result is not None
