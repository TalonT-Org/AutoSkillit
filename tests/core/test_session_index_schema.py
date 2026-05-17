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
}


class TestSessionIndexEntryCompleteness:
    """SessionIndexEntry TypedDict must declare every field written to sessions.jsonl."""

    def test_required_fields_declared(self):
        from autoskillit.core.types._type_results import SessionIndexEntry

        declared = set(SessionIndexEntry.__annotations__)
        missing = _REQUIRED_INDEX_FIELDS - declared
        assert not missing, f"SessionIndexEntry missing fields: {missing}"


class TestCanonicalCacheFields:
    """Token entry TypedDicts use canonical short-form field names."""

    def test_canonical_cache_fields_in_model_total_entry(self):
        from autoskillit.core.types._type_results import ModelTotalEntry

        annotations = set(ModelTotalEntry.__annotations__)
        assert "cache_creation" in annotations
        assert "cache_read" in annotations
        assert "cache_creation_input_tokens" not in annotations
        assert "cache_read_input_tokens" not in annotations

    def test_canonical_cache_fields_in_session_index_entry(self):
        from autoskillit.core.types._type_results import SessionIndexEntry

        annotations = set(SessionIndexEntry.__annotations__)
        assert "cache_creation" in annotations
        assert "cache_read" in annotations
        assert "cache_creation_input_tokens" not in annotations
        assert "cache_read_input_tokens" not in annotations

    def test_canonical_cache_fields_in_token_usage_file_entry(self):
        from autoskillit.core.types._type_results import TokenUsageFileEntry

        annotations = set(TokenUsageFileEntry.__annotations__)
        assert "cache_creation" in annotations
        assert "cache_read" in annotations
        assert "cache_creation_input_tokens" not in annotations
        assert "cache_read_input_tokens" not in annotations

    def test_schema_version_in_token_usage_file_entry(self):
        from typing import NotRequired, get_args, get_origin, get_type_hints

        from autoskillit.core.types._type_results import TokenUsageFileEntry

        hints = get_type_hints(TokenUsageFileEntry, include_extras=True)
        assert "schema_version" in hints
        anno = hints["schema_version"]
        assert get_origin(anno) is NotRequired
        assert get_args(anno)[0] is int
