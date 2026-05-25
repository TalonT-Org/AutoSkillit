"""Arch test: _resolve_provider_profile Tier 3 must never return step_name as profile."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_STEP_NAMES = ["plan", "implement", "investigate", "verify", "review", "remediate"]


@pytest.mark.parametrize("step_name", _STEP_NAMES)
def test_tier3_never_returns_step_name_as_profile_without_step_provider(step_name):
    """Tier 3 must not leak step_name as a profile when step_provider is empty."""
    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = ProvidersConfig()
    profile_name, env_dict = _resolve_provider_profile(step_name, "", cfg)
    assert profile_name != step_name, (
        f"step_name={step_name!r} leaked as profile name — "
        f"Tier 3 should only fire for explicit step_provider"
    )
    assert profile_name == "anthropic"
    assert env_dict == {}


@pytest.mark.parametrize("step_name", _STEP_NAMES)
def test_tier3_never_returns_step_name_as_profile_with_recipe_context(step_name):
    """With recipe context but no overrides, step_name must not become a profile."""
    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = ProvidersConfig()
    profile_name, _ = _resolve_provider_profile(step_name, "implementation", cfg)
    assert profile_name != step_name
    assert profile_name == "anthropic"


def test_tier3_returns_step_provider_when_profile_exists():
    """Tier 3 should use step_provider (not step_name) for profile lookup."""
    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = ProvidersConfig(profiles={"bedrock": {"AWS_REGION": "us-east-1"}})
    profile_name, env_dict = _resolve_provider_profile("plan", "", cfg, step_provider="bedrock")
    assert profile_name == "bedrock"
    assert env_dict.get("AWS_REGION") == "us-east-1"


def test_tier3_unresolvable_step_provider_returns_anthropic():
    """Unknown step_provider must return anthropic, not propagate the name."""
    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = ProvidersConfig()
    profile_name, env_dict = _resolve_provider_profile(
        "plan", "", cfg, step_provider="nonexistent"
    )
    assert profile_name == "anthropic"
    assert env_dict == {}
