"""Recovery token totals retain source-pair identity and availability."""

from __future__ import annotations

import pytest

from autoskillit.execution.headless import _merge_token_usage
from tests._helpers import observed_measure as _observed

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _usage(**counts: object) -> dict[str, object]:
    return {"backend": "claude-code", "provider_used": "anthropic", **counts}


def test_observed_canonical_totals_combine_within_source_pair() -> None:
    base = _usage(input_tokens=_observed(100), output_tokens=_observed(50))
    nudge = _usage(input_tokens=_observed(200), output_tokens=_observed(100))

    result = _merge_token_usage(base, nudge)

    assert result is not None
    assert result["input_tokens"] == _observed(300)
    assert result["output_tokens"] == _observed(150)
    assert result["backend"] == "claude-code"
    assert result["provider_used"] == "anthropic"


@pytest.mark.parametrize(
    ("base_cache", "nudge_cache"),
    [
        ({"cache_creation_input_tokens": 10}, {"cache_creation_input_tokens": 5}),
        ({"cache_write_tokens": 10}, {"cache_creation_input_tokens": 5}),
        ({"cache_creation_input_tokens": 10}, {"cache_write_tokens": 5}),
    ],
)
def test_legacy_cache_aliases_normalize_before_combining(
    base_cache: dict[str, int], nudge_cache: dict[str, int]
) -> None:
    result = _merge_token_usage(
        _usage(input_tokens=100, output_tokens=50, **base_cache),
        _usage(input_tokens=200, output_tokens=100, **nudge_cache),
    )

    assert result is not None
    assert result["cache_write_tokens"] == _observed(15)
    assert "cache_creation_input_tokens" not in result


def test_canonical_cache_key_wins_over_legacy_alias() -> None:
    result = _merge_token_usage(
        _usage(cache_write_tokens=10, cache_creation_input_tokens=5),
        _usage(cache_write_tokens=3),
    )

    assert result is not None
    assert result["cache_write_tokens"] == _observed(13)
    assert "cache_creation_input_tokens" not in result


def test_absent_peer_returns_original_usage_unchanged() -> None:
    usage = _usage(input_tokens=_observed(2))
    assert _merge_token_usage(None, usage) is usage
    assert _merge_token_usage(usage, None) is usage
    assert _merge_token_usage(None, None) is None


@pytest.mark.parametrize("invalid_input", [None, 1.5, [], "", "not_a_number", True, -1])
def test_invalid_counter_does_not_become_zero(invalid_input: object) -> None:
    result = _merge_token_usage(
        _usage(input_tokens=invalid_input, output_tokens=50),
        _usage(input_tokens=200, output_tokens=100),
    )

    assert result is not None
    assert result["input_tokens"] == {"state": "unknown", "value": None}
    assert result["output_tokens"] == _observed(150)
    assert result["cache_read_tokens"] == {"state": "unknown", "value": None}


def test_cross_provider_recovery_is_rejected() -> None:
    with pytest.raises(ValueError, match="different source pairs"):
        _merge_token_usage(
            _usage(input_tokens=_observed(10)),
            {"backend": "claude-code", "provider_used": "MiniMax", "input_tokens": _observed(2)},
        )


def test_extra_metadata_is_preserved_without_pooled_model_breakdown() -> None:
    result = _merge_token_usage(
        _usage(input_tokens=100, context_window_tokens=200_000, model_breakdown={"old": 1}),
        _usage(input_tokens=200),
    )

    assert result is not None
    assert result["context_window_tokens"] == 200_000
    assert "model_breakdown" not in result
