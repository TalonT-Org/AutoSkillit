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
