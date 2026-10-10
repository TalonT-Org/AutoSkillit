"""Tests verifying sessions.jsonl keys match SessionIndexEntry annotations."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from autoskillit.execution.session_log.session_index import iter_tolerant_session_index_lines
from tests.execution.conftest import _flush

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


class TestSessionIndexRoundtrip:
    """Written sessions.jsonl keys must exactly match SessionIndexEntry annotations."""

    def test_written_keys_match_typeddict_annotations(self, tmp_path):
        """Every key in sessions.jsonl must be declared in SessionIndexEntry and vice versa."""
        from autoskillit.core.types.results._type_results import SessionIndexEntry

        _flush(tmp_path, session_id="roundtrip-check", proc_snapshots=None)
        entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
        written_keys = set(entry.keys())
        declared_keys = set(SessionIndexEntry.__annotations__)
        extra = written_keys - declared_keys
        missing = declared_keys - written_keys
        assert not extra, f"Keys written to sessions.jsonl but not in SessionIndexEntry: {extra}"
        assert not missing, f"SessionIndexEntry fields never written to sessions.jsonl: {missing}"
        assert entry["adjudication_verdict"] is None

    def test_appending_current_schema_row_preserves_retained_v8_row_bytes(self, tmp_path):
        from autoskillit.core import SESSION_INDEX_SCHEMA_VERSION

        _flush(tmp_path, session_id="retained-v8", proc_snapshots=None)
        retained_session_dir = tmp_path / "sessions" / "retained-v8"
        assert retained_session_dir.is_dir()

        index_path = tmp_path / "sessions.jsonl"
        retained = json.loads(index_path.read_text().strip())
        retained["schema_version"] = 8
        retained.pop("rollout")
        retained.pop("subagent_model_outcomes")
        retained_line = json.dumps(retained, sort_keys=True, separators=(",", ":"))
        index_path.write_text(retained_line + "\n")

        outcome = {
            "model": "claude-sonnet-5",
            "final_model": "claude-sonnet-5",
            "model_swapped": False,
            "agent_type": "general-purpose",
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
        assert "rollout" not in old_row
        assert "subagent_model_outcomes" not in old_row
        assert new_row["schema_version"] == SESSION_INDEX_SCHEMA_VERSION
        assert new_row["subagent_model_outcomes"] == [outcome]

    def test_streaming_reader_classifies_exact_rollout_cutover_boundary(self, tmp_path):
        index_path = tmp_path / "sessions.jsonl"
        effective_at = "2026-11-01T18:00:00.500000+00:00"
        starts = (
            ("before", "2026-11-01T18:00:00.499999+00:00"),
            ("equal", "2026-11-01T18:00:00.500000+00:00"),
            ("after", "2026-11-01T18:00:00.500001+00:00"),
        )
        marker = {
            "change_id": "prompt-cost-study",
            "effective_at": effective_at,
            "scope": {"skills": [], "backends": [], "roles": []},
            "expected_effect": "Reduce planning input tokens.",
        }
        rows = [{"timestamp": timestamp, "rollout": marker} for _name, timestamp in starts]
        rows.extend(
            [
                {"timestamp": "2026-11-01T18:00:00.500000+00:00"},
                {"timestamp": "2026-11-01T18:00:00.500000+00:00", "rollout": None},
            ]
        )
        index_path.write_text("".join(json.dumps(row) + "\n" for row in rows))

        cohorts = []
        unassigned = 0
        for _end_offset, row in iter_tolerant_session_index_lines(index_path):
            if row is None:
                continue
            rollout = row.get("rollout")
            if rollout is None:
                unassigned += 1
                continue
            started_at = datetime.fromisoformat(row["timestamp"])
            cutover_at = datetime.fromisoformat(rollout["effective_at"])
            if started_at < cutover_at:
                cohorts.append("before")
            elif started_at >= cutover_at:
                cohorts.append("after")
            else:
                raise AssertionError("timezone-aware timestamps must have an ordering")

        assert cohorts == ["before", "after", "after"]
        assert unassigned == 2
