"""Tests verifying SessionIndexEntry TypedDict matches the actual JSONL output."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestSessionIndexEntryCompleteness:
    """SessionIndexEntry TypedDict must declare every field written to sessions.jsonl."""

    def test_provider_used_declared(self):
        from autoskillit.core.types._type_results import SessionIndexEntry

        assert "provider_used" in SessionIndexEntry.__annotations__

    def test_provider_fallback_declared(self):
        from autoskillit.core.types._type_results import SessionIndexEntry

        assert "provider_fallback" in SessionIndexEntry.__annotations__
