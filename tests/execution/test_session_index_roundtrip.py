"""Tests verifying sessions.jsonl keys match SessionIndexEntry annotations."""

from __future__ import annotations

import json

import pytest

from tests.execution.conftest import _flush

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


class TestSessionIndexRoundtrip:
    """Written sessions.jsonl keys must exactly match SessionIndexEntry annotations."""

    def test_written_keys_match_typeddict_annotations(self, tmp_path):
        """Every key in sessions.jsonl must be declared in SessionIndexEntry and vice versa."""
        from autoskillit.core.types._type_results import SessionIndexEntry

        _flush(tmp_path, session_id="roundtrip-check", proc_snapshots=None)
        entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
        written_keys = set(entry.keys())
        declared_keys = set(SessionIndexEntry.__annotations__)
        extra = written_keys - declared_keys
        missing = declared_keys - written_keys
        assert not extra, f"Keys written to sessions.jsonl but not in SessionIndexEntry: {extra}"
        assert not missing, f"SessionIndexEntry fields never written to sessions.jsonl: {missing}"

    def test_appending_v9_row_preserves_retained_v8_row_bytes(self, tmp_path):
        _flush(tmp_path, session_id="retained-v8", proc_snapshots=None)
        retained_session_dir = tmp_path / "sessions" / "retained-v8"
        assert retained_session_dir.is_dir()

        index_path = tmp_path / "sessions.jsonl"
        retained = json.loads(index_path.read_text().strip())
        retained["schema_version"] = 8
        retained.pop("subagent_model_outcomes")
        retained_line = json.dumps(retained, sort_keys=True, separators=(",", ":"))
        index_path.write_text(retained_line + "\n")

        outcome = {
            "model": "claude-sonnet-5",
            "final_model": "claude-sonnet-5",
            "model_swapped": False,
        }
        _flush(
            tmp_path,
            session_id="new-v9",
            subagent_model_outcomes=(outcome,),
            proc_snapshots=None,
        )

        lines = index_path.read_text().splitlines()
        assert lines[0] == retained_line
        old_row, new_row = map(json.loads, lines)
        assert old_row["schema_version"] == 8
        assert "subagent_model_outcomes" not in old_row
        assert new_row["schema_version"] == 9
        assert new_row["subagent_model_outcomes"] == [outcome]
