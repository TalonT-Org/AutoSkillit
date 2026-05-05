"""Tests for _resolve_model_as_profile model-as-profile resolution in _guards.py."""

from __future__ import annotations

import pytest
import structlog

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_config(**kwargs):
    from autoskillit.config._config_dataclasses import ProvidersConfig

    return ProvidersConfig(**kwargs)


def test_model_matches_profile_with_anthropic_model():
    from autoskillit.server._guards import _resolve_model_as_profile

    cfg = _make_config(
        profiles={
            "minimax": {
                "ANTHROPIC_MODEL": "MiniMax-M2.7",
                "ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1",
            }
        }
    )
    result = _resolve_model_as_profile("minimax", cfg)
    assert result == (
        "MiniMax-M2.7",
        "minimax",
        {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"},
    )


def test_model_matches_profile_without_anthropic_model():
    from autoskillit.server._guards import _resolve_model_as_profile

    cfg = _make_config(profiles={"broken": {"ANTHROPIC_BASE_URL": "https://example.com"}})
    with structlog.testing.capture_logs() as logs:
        result = _resolve_model_as_profile("broken", cfg)
    assert result == ("", "", None)
    assert any(
        log.get("event") == "provider_profile_no_model" and log.get("profile") == "broken"
        for log in logs
    )


def test_model_no_match_passthrough():
    from autoskillit.server._guards import _resolve_model_as_profile

    cfg = _make_config(profiles={"minimax": {"ANTHROPIC_MODEL": "MiniMax-M2.7"}})
    result = _resolve_model_as_profile("sonnet", cfg)
    assert result == ("sonnet", "", None)


def test_empty_model_passthrough():
    from autoskillit.server._guards import _resolve_model_as_profile

    cfg = _make_config()
    with structlog.testing.capture_logs() as logs:
        result = _resolve_model_as_profile("", cfg)
    assert result == ("", "", None)
    assert not any(log.get("event") == "provider_profile_no_model" for log in logs)


def test_anthropic_model_key_excluded_from_extras():
    from autoskillit.server._guards import _resolve_model_as_profile

    cfg = _make_config(
        profiles={
            "minimax": {
                "ANTHROPIC_MODEL": "M2.7",
                "ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1",
                "ANTHROPIC_API_KEY": "test-key",
            }
        }
    )
    effective_model, profile_name, extras = _resolve_model_as_profile("minimax", cfg)
    assert effective_model == "M2.7"
    assert profile_name == "minimax"
    assert extras is not None
    assert "ANTHROPIC_MODEL" not in extras
    assert extras["ANTHROPIC_BASE_URL"] == "https://api.minimax.chat/v1"
    assert extras["ANTHROPIC_API_KEY"] == "test-key"


def test_profile_with_only_anthropic_model_returns_empty_extras():
    from autoskillit.server._guards import _resolve_model_as_profile

    cfg = _make_config(profiles={"simple": {"ANTHROPIC_MODEL": "some-model"}})
    result = _resolve_model_as_profile("simple", cfg)
    assert result == ("some-model", "simple", {})


def test_anthropic_as_model_value_no_profile():
    from autoskillit.server._guards import _resolve_model_as_profile

    cfg = _make_config(profiles={"minimax": {"ANTHROPIC_MODEL": "M2.7"}})
    result = _resolve_model_as_profile("anthropic", cfg)
    assert result == ("anthropic", "", None)
