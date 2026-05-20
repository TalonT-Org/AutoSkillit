"""Tests for _profile_to_env — ProviderProfileDef to env dict conversion."""

from __future__ import annotations

import pytest
import structlog.testing

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_profile(**kwargs):
    from autoskillit.config._config_dataclasses import ProviderProfileDef

    return ProviderProfileDef(**kwargs)


def test_all_fields_populated(monkeypatch):
    from autoskillit.server._guards import _profile_to_env

    monkeypatch.setenv("MY_KEY", "secret")
    profile = _make_profile(
        name="x", base_url="https://a.com", timeout_seconds=30, api_key_env="MY_KEY"
    )
    result = _profile_to_env(profile)
    assert result == {
        "ANTHROPIC_BASE_URL": "https://a.com",
        "API_TIMEOUT_MS": "30000",
        "ANTHROPIC_API_KEY": "secret",
    }


def test_empty_fields_returns_empty_dict():
    from autoskillit.server._guards import _profile_to_env

    profile = _make_profile(name="x")
    result = _profile_to_env(profile)
    assert result == {}


def test_zero_timeout_returns_empty_dict():
    from autoskillit.server._guards import _profile_to_env

    profile = _make_profile(name="x", timeout_seconds=0)
    result = _profile_to_env(profile)
    assert result == {}


def test_api_key_env_absent_warns(monkeypatch):
    from autoskillit.server._guards import _profile_to_env

    monkeypatch.delenv("MISSING_KEY", raising=False)
    profile = _make_profile(name="x", api_key_env="MISSING_KEY")
    with structlog.testing.capture_logs() as logs:
        result = _profile_to_env(profile)
    assert "ANTHROPIC_API_KEY" not in result
    assert any(
        log["event"] == "provider_api_key_env_missing" and log["env_var_name"] == "MISSING_KEY"
        for log in logs
    )


def test_base_url_only():
    from autoskillit.server._guards import _profile_to_env

    profile = _make_profile(name="x", base_url="https://b.com")
    result = _profile_to_env(profile)
    assert result == {"ANTHROPIC_BASE_URL": "https://b.com"}


def test_timeout_only():
    from autoskillit.server._guards import _profile_to_env

    profile = _make_profile(name="x", timeout_seconds=60)
    result = _profile_to_env(profile)
    assert result == {"API_TIMEOUT_MS": "60000"}


def test_api_key_env_only(monkeypatch):
    from autoskillit.server._guards import _profile_to_env

    monkeypatch.setenv("K", "v")
    profile = _make_profile(name="x", api_key_env="K")
    result = _profile_to_env(profile)
    assert result == {"ANTHROPIC_API_KEY": "v"}


def test_raw_env_passthrough():
    from autoskillit.server._guards import _profile_to_env

    profile = _make_profile(name="x", raw_env={"AWS_REGION": "us-east-1", "CUSTOM": "val"})
    result = _profile_to_env(profile)
    assert result == {"AWS_REGION": "us-east-1", "CUSTOM": "val"}


def test_raw_env_merged_with_mapped_fields():
    from autoskillit.server._guards import _profile_to_env

    profile = _make_profile(name="x", base_url="https://a.com", raw_env={"EXTRA": "e"})
    result = _profile_to_env(profile)
    assert result == {"ANTHROPIC_BASE_URL": "https://a.com", "EXTRA": "e"}
