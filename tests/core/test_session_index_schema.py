"""Tests verifying SessionIndexEntry TypedDict matches the actual JSONL output."""

from __future__ import annotations

import json

import pytest

from tests.execution.conftest import _flush

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_REQUIRED_INDEX_FIELDS = {
    "provider_used",
    "provider_fallback",
    "model_identifier",
    "configured_model",
    "recipe_name",
    "recipe_content_hash",
    "recipe_composite_hash",
    "recipe_version",
    "schema_version",
    "caller_session_id",
    "fs_writes_detected",
    "git_writes_detected",
    "file_changes_count",
    "api_retry_count",
    "api_retry_exhausted",
    "codex_version",
    "codex_log",
    "skill_command",
}


@pytest.mark.small
class TestSessionIndexEntryCompleteness:
    """SessionIndexEntry TypedDict must declare every field written to sessions.jsonl."""

    def test_required_fields_declared(self):
        from autoskillit.core.types._type_results import SessionIndexEntry

        declared = set(SessionIndexEntry.__annotations__)
        missing = _REQUIRED_INDEX_FIELDS - declared
        assert not missing, f"SessionIndexEntry missing fields: {missing}"

    def test_has_schema_version(self):
        from typing import get_type_hints

        from autoskillit.core.types._type_results import SessionIndexEntry

        hints = get_type_hints(SessionIndexEntry)
        assert "schema_version" in hints
        assert hints["schema_version"] is int

    def test_has_caller_session_id(self):
        from typing import get_type_hints

        from autoskillit.core.types._type_results import SessionIndexEntry

        hints = get_type_hints(SessionIndexEntry)
        assert "caller_session_id" in hints
        assert hints["caller_session_id"] is str

    def test_canonical_cache_fields(self):
        """SessionIndexEntry must use canonical cache field names, not v1 API names."""
        from autoskillit.core.types._type_results import SessionIndexEntry

        declared = set(SessionIndexEntry.__annotations__)
        assert "cache_write_tokens" in declared
        assert "cache_read_tokens" in declared
        assert "cache_creation_input_tokens" not in declared
        assert "cache_read_input_tokens" not in declared


@pytest.mark.small
class TestTokenUsageFileEntrySchema:
    """TokenUsageFileEntry must use canonical cache fields and include schema_version."""

    def test_canonical_cache_fields(self):
        from autoskillit.core.types._type_results import TokenUsageFileEntry

        declared = set(TokenUsageFileEntry.__annotations__)
        assert "cache_write_tokens" in declared
        assert "cache_read_tokens" in declared
        assert "cache_creation_input_tokens" not in declared
        assert "cache_read_input_tokens" not in declared

    def test_has_schema_version(self):
        from typing import get_type_hints

        from autoskillit.core.types._type_results import TokenUsageFileEntry

        hints = get_type_hints(TokenUsageFileEntry)
        assert "schema_version" in hints
        assert hints["schema_version"] is int


@pytest.mark.medium
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
