"""Architectural invariant tests for RETIRED_PROFILE_KEYS (issue #4685).

``RETIRED_PROFILE_KEYS`` is the frozen profile-field retirement registry
that lets a pre-existing ``~/.autoskillit/config.yaml`` carrying a retired
profile key (``providers.profiles.<name>.context_window``) silently survive
the field's removal from ``ProviderProfileDef`` — the parsing layer pops
retired keys before constructing the dataclass, so a user's stale config
loads cleanly and the field is gone from ``raw_env``. These invariants keep
the registry internally consistent and pin the silent-drop behavior:

  * lowercase str entries (T1: mirrored from RETIRED_CONFIG_KEYS)
  * retired key MUST NOT be a live ProviderProfileDef field (T2)
  * retired key is silently dropped from raw_env (T3)
  * non-retired unrecognized keys still flow to raw_env (T4)
  * retirement applies across every profile, not just the first (T5)
"""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.config import RETIRED_PROFILE_KEYS, ProviderProfileDef, ProvidersConfig

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


@pytest.mark.parametrize("retired_key", sorted(RETIRED_PROFILE_KEYS))
def test_retired_profile_key_invariants(retired_key: str) -> None:
    # T1: Retired entries must be lowercase strings — fail-fast at module
    # load already enforces this; this test pins the test-side mirror.
    assert isinstance(retired_key, str)
    assert retired_key == retired_key.lower()

    # T2: A retired profile key must not also be a live dataclass field.
    # Use the class because ProviderProfileDef.name is required.
    live_fields = {f.name for f in dataclasses.fields(ProviderProfileDef)}
    assert retired_key not in live_fields, (
        f"RETIRED_PROFILE_KEYS[{retired_key!r}]: retired key is also a live "
        f"ProviderProfileDef field — a retired name must never be reused."
    )


@pytest.mark.parametrize(
    "retired_key, raw_value",
    [(k, v) for k in sorted(RETIRED_PROFILE_KEYS) for v in ["200000", "0", "-1", "abc", ""]],
)
def test_resolved_profiles_silently_drops_retired_keys(retired_key: str, raw_value: str) -> None:
    """T3/T4: drop retired string values while preserving unknown keys.

    Previously, ``context_window: 0`` raised ``ValueError`` from
    ``__post_init__``. After retirement, configured string values (positive,
    zero, negative, non-numeric, and empty) are silently dropped without
    raising. The key must NOT appear in ``raw_env`` — the retirement loop runs before
    ``raw_env=copy`` so it cannot leak into the catch-all sink.
    """
    cfg = ProvidersConfig(
        profiles={
            "anthropic": {
                "base_url": "https://example.invalid",
                "model": "gpt-4",  # non-retired unrecognized key — must pass through
                retired_key: raw_value,
            },
        },
    )
    profile = cfg.resolved_profiles["anthropic"]
    assert profile.base_url == "https://example.invalid"
    assert "model" in profile.raw_env, (
        f"Non-retired unrecognized key 'model' must be preserved in raw_env; "
        f"got raw_env={profile.raw_env!r}. The retirement loop is over-broad."
    )
    assert retired_key not in profile.raw_env, (
        f"Retired key {retired_key!r}={raw_value!r} leaked into raw_env; "
        f"the retirement loop ran AFTER raw_env=copy assignment. raw_env={profile.raw_env!r}"
    )
    assert profile.raw_env == {"model": "gpt-4"}


def test_resolved_profiles_silently_drops_retired_keys_across_multiple_profiles() -> None:
    """T5: retirement applies to every profile, not just the first."""
    cfg = ProvidersConfig(
        profiles={
            "anthropic": {"base_url": "https://a.invalid", "context_window": "200000"},
            "my_provider": {"base_url": "https://b.invalid", "context_window": "100000"},
        },
    )
    for name in ("anthropic", "my_provider"):
        profile = cfg.resolved_profiles[name]
        assert profile.base_url is not None
        assert profile.base_url.startswith("https://")
        assert "context_window" not in profile.raw_env, (
            f"profile {name!r}: context_window leaked into raw_env={profile.raw_env!r}"
        )
