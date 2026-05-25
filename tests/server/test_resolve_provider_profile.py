"""Tests for _resolve_provider_profile six-tier provider resolution."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_config(**kwargs):
    from autoskillit.config._config_dataclasses import ProvidersConfig

    return ProvidersConfig(**kwargs)


def test_step_override_wins():
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(
        step_overrides={"my_step": "bedrock"},
        profiles={"bedrock": {"AWS_REGION": "us-east-1"}},
    )
    result = _resolve_provider_profile("my_step", "my_recipe", cfg)
    assert result == ("bedrock", {"AWS_REGION": "us-east-1"})


def test_recipe_wildcard_wins_when_no_step_override():
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(
        step_overrides={"*": "vertex"},
        profiles={"vertex": {"GOOGLE_CLOUD_PROJECT": "proj"}},
    )
    result = _resolve_provider_profile("other_step", "my_recipe", cfg)
    assert result == ("vertex", {"GOOGLE_CLOUD_PROJECT": "proj"})


def test_step_yaml_provider_wins_when_no_config_overrides():
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(
        profiles={"bedrock": {"AWS_REGION": "eu-west-1"}},
    )
    result = _resolve_provider_profile("implement", "my_recipe", cfg, step_provider="bedrock")
    assert result == ("bedrock", {"AWS_REGION": "eu-west-1"})


def test_default_anthropic_when_all_tiers_absent():
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config()
    result = _resolve_provider_profile("", "", cfg)
    assert result == ("anthropic", {})


def test_step_override_beats_wildcard_when_both_match():
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(
        step_overrides={"my_step": "bedrock", "*": "vertex"},
        profiles={
            "bedrock": {"AWS_REGION": "us-east-1"},
            "vertex": {"GOOGLE_CLOUD_PROJECT": "proj"},
        },
    )
    result = _resolve_provider_profile("my_step", "my_recipe", cfg)
    assert result == ("bedrock", {"AWS_REGION": "us-east-1"})


def test_anthropic_profile_returns_empty_env_regardless():
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(
        default_provider="anthropic",
        profiles={"anthropic": {"SHOULD_IGNORE": "this"}},
    )
    result = _resolve_provider_profile("", "", cfg)
    assert result == ("anthropic", {})


def test_non_anthropic_profile_returns_correct_env_dict():
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(
        step_overrides={"my_step": "bedrock"},
        profiles={"bedrock": {"AWS_ACCESS_KEY_ID": "test"}},
    )
    result = _resolve_provider_profile("my_step", "my_recipe", cfg)
    assert result == ("bedrock", {"AWS_ACCESS_KEY_ID": "test"})


def test_no_recipe_name_skips_step_override():
    # Without recipe context, Tiers 1/2 are bypassed. Without step_provider,
    # Tier 3 is also skipped. Falls through to Tier 4 (default).
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(
        step_overrides={"bedrock": "vertex"},
        profiles={"vertex": {"K": "V"}, "bedrock": {"X": "Y"}},
    )
    result = _resolve_provider_profile("bedrock", "", cfg)
    assert result == ("anthropic", {})


def test_recipe_override_wins_over_global_step_override():
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(
        recipe_overrides={"remediation": {"implement": "anthropic"}},
        step_overrides={"implement": "minimax"},
    )
    result = _resolve_provider_profile("implement", "remediation", cfg)
    assert result == ("anthropic", {})


def test_recipe_override_does_not_affect_other_recipes():
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(
        recipe_overrides={"remediation": {"implement": "anthropic"}},
        step_overrides={"implement": "minimax"},
        profiles={"minimax": {"K": "V"}},
    )
    result = _resolve_provider_profile("implement", "implementation", cfg)
    assert result == ("minimax", {"K": "V"})


def test_recipe_override_step_beats_recipe_wildcard():
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(
        recipe_overrides={"remediation": {"implement": "anthropic", "*": "vertex"}},
    )
    result = _resolve_provider_profile("implement", "remediation", cfg)
    assert result == ("anthropic", {})


def test_recipe_wildcard_override_wins_over_global_step():
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(
        recipe_overrides={"remediation": {"*": "anthropic"}},
        step_overrides={"implement": "minimax"},
    )
    result = _resolve_provider_profile("implement", "remediation", cfg)
    assert result == ("anthropic", {})


def test_recipe_wildcard_override_applies_to_all_steps():
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(
        recipe_overrides={"remediation": {"*": "vertex"}},
        profiles={"vertex": {"K": "V"}},
    )
    result = _resolve_provider_profile("investigate", "remediation", cfg)
    assert result == ("vertex", {"K": "V"})


def test_recipe_override_non_anthropic_returns_env_dict():
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(
        recipe_overrides={"remediation": {"implement": "bedrock"}},
        profiles={"bedrock": {"AWS_REGION": "us-east-1"}},
    )
    result = _resolve_provider_profile("implement", "remediation", cfg)
    assert result == ("bedrock", {"AWS_REGION": "us-east-1"})


def test_recipe_override_requires_recipe_context():
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(
        recipe_overrides={"remediation": {"implement": "vertex"}},
        profiles={"vertex": {"K": "V"}},
    )
    result = _resolve_provider_profile("implement", "", cfg)
    assert result == ("anthropic", {})


def test_recipe_override_requires_step_name():
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(
        recipe_overrides={"remediation": {"implement": "vertex"}},
        profiles={"vertex": {"K": "V"}},
    )
    result = _resolve_provider_profile("", "remediation", cfg)
    assert result == ("anthropic", {})
    assert result[0] != "vertex"  # override bypassed because step_name is empty


def test_recipe_override_requires_step_name_with_step_name():
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(
        recipe_overrides={"remediation": {"implement": "vertex"}},
        profiles={"vertex": {"K": "V"}},
    )
    result = _resolve_provider_profile("implement", "remediation", cfg)
    assert result == ("vertex", {"K": "V"})


def test_provider_result_filters_none_values():
    from autoskillit.server._guards import _provider_result

    profiles = {"custom": {"base_url": None, "api_key_env": "MY_KEY", "timeout_seconds": None}}
    name, extras = _provider_result("custom", profiles)
    assert name == "custom"
    assert None not in extras.values()
    assert extras == {"api_key_env": "MY_KEY"}


def test_resolve_provider_profile_step_override_resolves_api_key(monkeypatch):
    from autoskillit.server._guards import _resolve_provider_profile

    monkeypatch.setenv("MY_KEY", "secret-value")
    cfg = _make_config(
        profiles={"custom": {"base_url": None, "api_key_env": "MY_KEY"}},
        step_overrides={"fetch": "custom"},
    )
    name, extras = _resolve_provider_profile("fetch", "my_recipe", cfg)
    assert name == "custom"
    assert None not in extras.values()
    assert extras == {"ANTHROPIC_API_KEY": "secret-value"}


def test_tier3_step_name_not_used_as_profile_when_no_matching_profile():
    """step_name='plan' with no 'plan' profile should fall through to Tier 4."""
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config()
    result = _resolve_provider_profile("plan", "", cfg)
    assert result == ("anthropic", {}), (
        "step_name='plan' should NOT be returned as a profile name "
        "when 'plan' is not a configured profile"
    )


def test_tier3_only_fires_for_explicit_provider_declaration():
    """Tier 3 should only use step_provider (the YAML provider: field), not step_name."""
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(profiles={"bedrock": {"AWS_REGION": "us-east-1"}})
    result = _resolve_provider_profile("implement", "", cfg, step_provider="")
    assert result == ("anthropic", {}), (
        "Without an explicit provider: field, Tier 3 should not fire"
    )


def test_tier3_uses_explicit_provider_field():
    """When step_provider='bedrock' is explicitly set, Tier 3 resolves it."""
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(profiles={"bedrock": {"AWS_REGION": "us-east-1"}})
    result = _resolve_provider_profile("implement", "", cfg, step_provider="bedrock")
    assert result == ("bedrock", {"AWS_REGION": "us-east-1"})


def test_tier3_unresolvable_step_provider_falls_to_anthropic():
    """Unknown step_provider should warn and return anthropic, not propagate."""
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config()
    result = _resolve_provider_profile("implement", "", cfg, step_provider="nonexistent")
    assert result == ("anthropic", {}), "Unresolvable step_provider should fall back to anthropic"


def test_unresolvable_step_override_logs_warning():
    """A step_overrides value that doesn't match a profile should log a warning."""
    import structlog.testing

    from autoskillit.server._guards import _resolve_provider_profile

    with structlog.testing.capture_logs() as cap_logs:
        cfg = _make_config(
            step_overrides={"plan": "nonexistent_profile"},
            profiles={},
        )
        result = _resolve_provider_profile("plan", "my_recipe", cfg)
    assert result == ("nonexistent_profile", {})
    warning_events = [e for e in cap_logs if e.get("log_level") == "warning"]
    assert any("nonexistent_profile" in str(e.get("provider", "")) for e in warning_events)
