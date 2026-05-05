"""Tests for ProviderOutcome typed container construction contract."""

from __future__ import annotations

import pytest

from autoskillit.core.types._type_results import ProviderOutcome

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestProviderOutcomeConstruction:
    """ProviderOutcome must require all fields — no silent defaults."""

    def test_missing_provider_used_raises_type_error(self):
        with pytest.raises(TypeError):
            ProviderOutcome(fallback_activated=False)  # type: ignore[call-arg]

    def test_missing_fallback_activated_raises_type_error(self):
        with pytest.raises(TypeError):
            ProviderOutcome(provider_used="minimax")  # type: ignore[call-arg]

    def test_complete_construction_succeeds(self):
        outcome = ProviderOutcome(provider_used="minimax", fallback_activated=False)
        assert outcome.provider_used == "minimax"
        assert outcome.fallback_activated is False

    def test_none_used_sentinel(self):
        outcome = ProviderOutcome.none_used()
        assert outcome.provider_used == ""
        assert outcome.fallback_activated is False

    def test_frozen_rejects_mutation(self):
        outcome = ProviderOutcome(provider_used="minimax", fallback_activated=False)
        with pytest.raises((AttributeError, TypeError)):
            outcome.provider_used = "other"  # type: ignore[misc]
