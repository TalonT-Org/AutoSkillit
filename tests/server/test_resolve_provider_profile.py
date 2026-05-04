"""Tests for _resolve_provider_profile four-tier provider resolution."""

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
    result = _resolve_provider_profile("bedrock", "my_recipe", cfg)
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


def test_empty_recipe_name_skips_overrides():
    from autoskillit.server._guards import _resolve_provider_profile

    cfg = _make_config(
        step_overrides={"my_step": "vertex"},
        profiles={"vertex": {"K": "V"}},
    )
    result = _resolve_provider_profile("my_step", "", cfg)
    assert result == ("my_step", {})
