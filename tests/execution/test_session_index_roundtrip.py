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
