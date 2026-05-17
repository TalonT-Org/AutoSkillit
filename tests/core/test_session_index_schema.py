"""Tests verifying SessionIndexEntry TypedDict matches the actual JSONL output."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_REQUIRED_INDEX_FIELDS = {
    "provider_used",
    "provider_fallback",
    "recipe_name",
    "recipe_content_hash",
    "recipe_composite_hash",
    "recipe_version",
    "schema_version",
    "caller_session_id",
}


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
