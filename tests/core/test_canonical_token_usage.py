"""Tests for CanonicalTokenUsage type."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from autoskillit.core.types import CanonicalTokenUsage, TokenMeasure, TokenMeasureState

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


@pytest.mark.parametrize(
    ("value", "state"),
    [(7, TokenMeasureState.MEASURED), (0, TokenMeasureState.MEASURED_ZERO)],
)
def test_observed_token_measure_records_observation_state(
    value: int, state: TokenMeasureState
) -> None:
    measure = TokenMeasure.observed(value)

    assert measure.state is state
    assert measure.value == value
    assert TokenMeasure.from_dict(measure.to_dict()) == measure


@pytest.mark.parametrize(
    "measure",
    [TokenMeasure.unavailable(), TokenMeasure.unknown(), TokenMeasure.not_applicable()],
)
def test_non_numeric_token_measure_states_reject_values(measure: TokenMeasure) -> None:
    assert measure.value is None
    with pytest.raises(ValueError, match="cannot carry a value"):
        TokenMeasure(measure.state, 1)


def test_token_measure_refuses_incompatible_combination() -> None:
    with pytest.raises(ValueError, match="incompatible availability"):
        TokenMeasure.observed(1).combine(TokenMeasure.unknown())


@pytest.mark.parametrize(
    ("backend", "anthropic_provider_capable", "expected"),
    [("claude-code", True, "anthropic"), ("codex", False, "codex")],
)
def test_provider_resolution_uses_backend_capability(
    backend: str, anthropic_provider_capable: bool, expected: str
) -> None:
    from autoskillit.core import resolve_provider_used

    assert resolve_provider_used(backend, anthropic_provider_capable) == expected


def _usage(
    *,
    backend: str = "claude-code",
    provider_used: str = "anthropic",
    input_tokens: int | TokenMeasure = 100,
    output_tokens: int | TokenMeasure = 50,
    cache_read_tokens: int | TokenMeasure = 10,
    cache_write_tokens: int | TokenMeasure = 5,
    raw: dict[str, object] | None = None,
) -> CanonicalTokenUsage:
    return CanonicalTokenUsage(
        backend=backend,
        provider_used=provider_used,
        input_tokens=(
            input_tokens
            if isinstance(input_tokens, TokenMeasure)
            else TokenMeasure.observed(input_tokens)
        ),
        output_tokens=(
            output_tokens
            if isinstance(output_tokens, TokenMeasure)
            else TokenMeasure.observed(output_tokens)
        ),
        cache_read_tokens=(
            cache_read_tokens
            if isinstance(cache_read_tokens, TokenMeasure)
            else TokenMeasure.observed(cache_read_tokens)
        ),
        cache_write_tokens=(
            cache_write_tokens
            if isinstance(cache_write_tokens, TokenMeasure)
            else TokenMeasure.observed(cache_write_tokens)
        ),
        raw={} if raw is None else raw,
    )


class TestFromAnthropicDictRoundTrip:
    def test_full_payload(self):
        raw = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 20,
        }
        result = CanonicalTokenUsage.from_anthropic_dict(raw)
        assert result.input_tokens == TokenMeasure.observed(100)
        assert result.output_tokens == TokenMeasure.observed(50)
        assert result.cache_write_tokens == TokenMeasure.observed(10)
        assert result.cache_read_tokens == TokenMeasure.observed(20)
        assert result.backend == "claude-code"
        assert result.provider_used == "anthropic"
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
        assert d["input_tokens"] == {"state": "measured", "value": 100}
        assert d["output_tokens"] == {"state": "measured", "value": 50}
        assert d["cache_write_tokens"] == {"state": "measured", "value": 10}
        assert d["cache_read_tokens"] == {"state": "measured", "value": 20}
        assert d["backend"] == "claude-code"
        assert d["provider_used"] == "anthropic"


class TestFromCodexDictRoundTrip:
    def test_full_payload(self):
        raw = {"input_tokens": 200, "output_tokens": 80, "cached_input_tokens": 30}
        result = CanonicalTokenUsage.from_codex_dict(raw)
        assert result.input_tokens == TokenMeasure.observed(200)
        assert result.output_tokens == TokenMeasure.observed(80)
        assert result.cache_read_tokens == TokenMeasure.observed(30)
        assert result.cache_write_tokens == TokenMeasure.unavailable()
        assert result.backend == "codex"
        assert result.provider_used == "codex"
        assert result.raw == raw


class TestMergeCommutativity:
    def test_merge_sums_fields(self):
        a = _usage(raw={"a": 1})
        b = _usage(
            input_tokens=200,
            output_tokens=80,
            cache_read_tokens=20,
            cache_write_tokens=10,
            raw={"b": 2},
        )
        merged = CanonicalTokenUsage.merge(a, b)
        assert merged is not None
        assert merged.input_tokens == TokenMeasure.observed(300)
        assert merged.output_tokens == TokenMeasure.observed(130)
        assert merged.cache_read_tokens == TokenMeasure.observed(30)
        assert merged.cache_write_tokens == TokenMeasure.observed(15)
        assert merged.provider_used == "anthropic"
        assert merged.raw == {"a": 1, "b": 2}


class TestMeasureMerging:
    def test_incompatible_cache_measure_states_do_not_coerce_to_zero(self):
        a = _usage(cache_read_tokens=TokenMeasure.unknown())
        b = _usage(cache_read_tokens=20, cache_write_tokens=10)

        with pytest.raises(ValueError, match="incompatible availability"):
            CanonicalTokenUsage.merge(a, b)

    def test_common_non_numeric_cache_state_is_preserved(self):
        a = _usage(cache_write_tokens=TokenMeasure.unavailable())
        b = _usage(
            input_tokens=200,
            output_tokens=80,
            cache_write_tokens=TokenMeasure.unavailable(),
        )

        merged = CanonicalTokenUsage.merge(a, b)

        assert merged is not None
        assert merged.cache_write_tokens == TokenMeasure.unavailable()

    def test_merge_none_base_returns_other(self):
        b = _usage(
            input_tokens=200,
            output_tokens=80,
            cache_read_tokens=20,
            cache_write_tokens=10,
        )
        assert CanonicalTokenUsage.merge(None, b) is b

    def test_merge_none_other_returns_base(self):
        a = _usage()
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
        a = _usage()
        b = _usage(backend="codex", provider_used="codex")
        with pytest.raises(ValueError, match="mismatched source pairs"):
            CanonicalTokenUsage.merge(a, b)


@pytest.mark.parametrize("field", ["backend", "provider_used"])
def test_canonical_usage_rejects_empty_source_pair_member(field: str) -> None:
    kwargs: dict[str, object] = {field: ""}

    with pytest.raises(ValueError, match="non-empty"):
        _usage(**kwargs)  # type: ignore[arg-type]


class TestToDictStructuredMeasures:
    def test_codex_to_dict_preserves_unavailable_cache_write(self):
        raw = {"input_tokens": 200, "output_tokens": 80, "cached_input_tokens": 50}
        ctu = CanonicalTokenUsage.from_codex_dict(raw)
        d = ctu.to_dict()
        assert d["cache_write_tokens"] == {"state": "unavailable", "value": None}
        assert d["cache_read_tokens"] == {"state": "measured", "value": 50}


class TestFrozen:
    def test_cannot_mutate_fields(self):
        usage = _usage()
        with pytest.raises(FrozenInstanceError):
            usage.input_tokens = TokenMeasure.observed(999)  # type: ignore[misc]


def test_token_types_importable_via_types_gateway():
    from autoskillit.core.types import (
        CanonicalTokenUsage,
        TokenMeasure,
        TokenMeasureState,
        TurnTokenEntry,
    )

    assert CanonicalTokenUsage is not None
    assert TokenMeasure is not None
    assert TokenMeasureState is not None
    assert TurnTokenEntry is not None


def test_token_types_importable_from_core():
    from autoskillit.core import (
        CanonicalTokenUsage,
        TokenMeasure,
        TokenMeasureState,
        TurnTokenEntry,
    )

    assert CanonicalTokenUsage is not None
    assert TokenMeasure is not None
    assert TokenMeasureState is not None
    assert TurnTokenEntry is not None


def test_token_types_in_types_all():
    from autoskillit.core.types import __all__ as types_all

    assert "CanonicalTokenUsage" in types_all
    assert "TokenMeasure" in types_all
    assert "TokenMeasureState" in types_all
    assert "TurnTokenEntry" in types_all


def test_token_types_in_core_all():
    import autoskillit.core as core

    assert "CanonicalTokenUsage" in core.__all__
    assert "TokenMeasure" in core.__all__
    assert "TokenMeasureState" in core.__all__
    assert "TurnTokenEntry" in core.__all__


def test_token_types_not_in_private_reexports():
    import autoskillit.core as core

    assert "CanonicalTokenUsage" not in core._PRIVATE_REEXPORTS
    assert "TokenMeasure" not in core._PRIVATE_REEXPORTS
    assert "TokenMeasureState" not in core._PRIVATE_REEXPORTS
    assert "TurnTokenEntry" not in core._PRIVATE_REEXPORTS
