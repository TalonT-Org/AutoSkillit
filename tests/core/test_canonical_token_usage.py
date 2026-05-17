"""Tests for CanonicalTokenUsage type."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from autoskillit.core.types import CanonicalTokenUsage

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestFromAnthropicDictRoundTrip:
    def test_full_payload(self):
        raw = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 20,
        }
        result = CanonicalTokenUsage.from_anthropic_dict(raw)
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert result.cache_write_tokens == 10
        assert result.cache_read_tokens == 20
        assert result.provider == "anthropic"
        assert result.raw == raw

    def test_round_trip_via_to_dict(self):
        raw = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 20,
        }
        result = CanonicalTokenUsage.from_anthropic_dict(raw)
        d = result.to_dict()
        assert d["input_tokens"] == 100
        assert d["output_tokens"] == 50
        assert d["cache_write_tokens"] == 10
        assert d["cache_read_tokens"] == 20
        assert d["provider"] == "anthropic"


class TestFromCodexDictRoundTrip:
    def test_full_payload(self):
        raw = {"input_tokens": 200, "output_tokens": 80, "cached_input_tokens": 30}
        result = CanonicalTokenUsage.from_codex_dict(raw)
        assert result.input_tokens == 200
        assert result.output_tokens == 80
        assert result.cache_read_tokens == 30
        assert result.cache_write_tokens is None
        assert result.provider == "codex"
        assert result.raw == raw


class TestMergeCommutativity:
    def test_merge_sums_fields(self):
        a = CanonicalTokenUsage(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=10,
            cache_write_tokens=5,
            provider="anthropic",
            raw={"a": 1},
        )
        b = CanonicalTokenUsage(
            input_tokens=200,
            output_tokens=80,
            cache_read_tokens=20,
            cache_write_tokens=10,
            provider="anthropic",
            raw={"b": 2},
        )
        merged = CanonicalTokenUsage.merge(a, b)
        assert merged.input_tokens == 300
        assert merged.output_tokens == 130
        assert merged.cache_read_tokens == 30
        assert merged.cache_write_tokens == 15
        assert merged.provider == "anthropic"
        assert merged.raw == {"a": 1, "b": 2}


class TestNoneCacheFields:
    def test_none_cache_in_merge_treated_as_zero(self):
        a = CanonicalTokenUsage(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=None,
            cache_write_tokens=None,
            provider="anthropic",
            raw={},
        )
        b = CanonicalTokenUsage(
            input_tokens=200,
            output_tokens=80,
            cache_read_tokens=20,
            cache_write_tokens=10,
            provider="anthropic",
            raw={},
        )
        merged = CanonicalTokenUsage.merge(a, b)
        assert merged.cache_read_tokens == 20
        assert merged.cache_write_tokens == 10

    def test_merge_none_with_none_returns_none(self):
        a = CanonicalTokenUsage(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=None,
            cache_write_tokens=None,
            provider="anthropic",
            raw={},
        )
        b = CanonicalTokenUsage(
            input_tokens=200,
            output_tokens=80,
            cache_read_tokens=None,
            cache_write_tokens=None,
            provider="anthropic",
            raw={},
        )
        merged = CanonicalTokenUsage.merge(a, b)
        assert merged.cache_read_tokens is None
        assert merged.cache_write_tokens is None

    def test_merge_none_base_returns_other(self):
        b = CanonicalTokenUsage(
            input_tokens=200,
            output_tokens=80,
            cache_read_tokens=20,
            cache_write_tokens=10,
            provider="anthropic",
            raw={},
        )
        assert CanonicalTokenUsage.merge(None, b) is b

    def test_merge_none_other_returns_base(self):
        a = CanonicalTokenUsage(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=10,
            cache_write_tokens=5,
            provider="anthropic",
            raw={},
        )
        assert CanonicalTokenUsage.merge(a, None) is a


class TestRawSnapshot:
    def test_from_anthropic_dict_snapshots_raw(self):
        raw = {"input_tokens": 1, "output_tokens": 2}
        result = CanonicalTokenUsage.from_anthropic_dict(raw)
        raw["input_tokens"] = 999
        assert result.raw["input_tokens"] == 1

    def test_from_codex_dict_snapshots_raw(self):
        raw = {"input_tokens": 1, "output_tokens": 2}
        result = CanonicalTokenUsage.from_codex_dict(raw)
        raw["input_tokens"] = 999
        assert result.raw["input_tokens"] == 1


class TestMergeProviderGuard:
    def test_merge_raises_on_mismatched_providers(self):
        a = CanonicalTokenUsage(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=None,
            cache_write_tokens=None,
            provider="anthropic",
            raw={},
        )
        b = CanonicalTokenUsage(
            input_tokens=200,
            output_tokens=80,
            cache_read_tokens=None,
            cache_write_tokens=None,
            provider="codex",
            raw={},
        )
        with pytest.raises(ValueError, match="mismatched providers"):
            CanonicalTokenUsage.merge(a, b)


class TestFrozen:
    def test_cannot_mutate_fields(self):
        usage = CanonicalTokenUsage(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=10,
            cache_write_tokens=5,
            provider="anthropic",
            raw={},
        )
        with pytest.raises(FrozenInstanceError):
            usage.input_tokens = 999  # type: ignore[misc]


def test_importable_via_core_gateway():
    from autoskillit.core.types import CanonicalTokenUsage

    assert CanonicalTokenUsage is not None


def test_canonical_token_usage_importable_from_core():
    from autoskillit.core import CanonicalTokenUsage

    assert CanonicalTokenUsage is not None


def test_canonical_token_usage_in_types_all():
    from autoskillit.core.types import __all__ as types_all

    assert "CanonicalTokenUsage" in types_all


def test_canonical_token_usage_in_core_all():
    import autoskillit.core as core

    assert "CanonicalTokenUsage" in core.__all__


def test_canonical_token_usage_not_in_private_reexports():
    import autoskillit.core as core

    assert "CanonicalTokenUsage" not in core._PRIVATE_REEXPORTS
