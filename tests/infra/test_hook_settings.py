"""Tests for the shared stdlib-only quota hook settings resolver.

``autoskillit.hooks._hook_settings.resolve_quota_settings()`` resolves cache path,
cache max age, and buffer seconds from a layered hierarchy:

    1. ``cache_path_override`` parameter (tests / DI)
    2. ``AUTOSKILLIT_QUOTA_GUARD__<KEY>`` env var
    3. ``.autoskillit/.hook_config.json`` snapshot
    4. Module defaults (matching ``config/defaults.yaml``)

These tests use ``tmp_path`` and ``monkeypatch`` for isolation — no global state.
"""

from __future__ import annotations

import json

_ENV_VARS = (
    "AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH",
    "AUTOSKILLIT_QUOTA_GUARD__CACHE_MAX_AGE",
    "AUTOSKILLIT_QUOTA_GUARD__BUFFER_SECONDS",
)


def _clear_env(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _write_hook_config(tmp_path, quota_guard: dict) -> None:
    hook_cfg = tmp_path / ".autoskillit" / ".hook_config.json"
    hook_cfg.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg.write_text(json.dumps({"quota_guard": quota_guard}))


# T-HS-1
def test_resolve_defaults_without_env_or_hook_config(tmp_path, monkeypatch):
    """With no env var and no hook config, resolver returns module defaults."""
    from autoskillit.hooks._hook_settings import (
        DEFAULT_BUFFER_SECONDS,
        DEFAULT_CACHE_MAX_AGE,
        DEFAULT_CACHE_PATH,
        resolve_quota_settings,
    )

    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    settings = resolve_quota_settings()

    assert settings.cache_path == DEFAULT_CACHE_PATH
    assert settings.cache_max_age == DEFAULT_CACHE_MAX_AGE
    assert settings.buffer_seconds == DEFAULT_BUFFER_SECONDS


# T-HS-2
def test_env_var_overrides_cache_max_age(tmp_path, monkeypatch):
    """AUTOSKILLIT_QUOTA_GUARD__CACHE_MAX_AGE env var sets cache_max_age."""
    from autoskillit.hooks._hook_settings import resolve_quota_settings

    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)
    monkeypatch.setenv("AUTOSKILLIT_QUOTA_GUARD__CACHE_MAX_AGE", "600")

    settings = resolve_quota_settings()

    assert settings.cache_max_age == 600


# T-HS-3
def test_env_var_overrides_cache_path(tmp_path, monkeypatch):
    """AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH env var sets cache_path."""
    from autoskillit.hooks._hook_settings import resolve_quota_settings

    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)
    monkeypatch.setenv("AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH", "/custom/path.json")

    settings = resolve_quota_settings()

    assert settings.cache_path == "/custom/path.json"


# T-HS-4
def test_env_var_overrides_buffer_seconds(tmp_path, monkeypatch):
    """AUTOSKILLIT_QUOTA_GUARD__BUFFER_SECONDS env var sets buffer_seconds."""
    from autoskillit.hooks._hook_settings import resolve_quota_settings

    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)
    monkeypatch.setenv("AUTOSKILLIT_QUOTA_GUARD__BUFFER_SECONDS", "120")

    settings = resolve_quota_settings()

    assert settings.buffer_seconds == 120


# T-HS-5
def test_hook_config_overrides_defaults(tmp_path, monkeypatch):
    """Hook config snapshot overrides module defaults when env vars are unset."""
    from autoskillit.hooks._hook_settings import resolve_quota_settings

    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)
    _write_hook_config(
        tmp_path,
        {
            "cache_max_age": 600,
            "cache_path": "/bridge/cache.json",
            "buffer_seconds": 90,
        },
    )

    settings = resolve_quota_settings()

    assert settings.cache_max_age == 600
    assert settings.cache_path == "/bridge/cache.json"
    assert settings.buffer_seconds == 90


# T-HS-6
def test_env_var_beats_hook_config(tmp_path, monkeypatch):
    """Env var takes precedence over hook config snapshot."""
    from autoskillit.hooks._hook_settings import resolve_quota_settings

    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)
    _write_hook_config(
        tmp_path,
        {
            "cache_max_age": 600,
            "cache_path": "/bridge/cache.json",
            "buffer_seconds": 90,
        },
    )
    monkeypatch.setenv("AUTOSKILLIT_QUOTA_GUARD__CACHE_MAX_AGE", "900")
    monkeypatch.setenv("AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH", "/env/cache.json")
    monkeypatch.setenv("AUTOSKILLIT_QUOTA_GUARD__BUFFER_SECONDS", "300")

    settings = resolve_quota_settings()

    assert settings.cache_max_age == 900
    assert settings.cache_path == "/env/cache.json"
    assert settings.buffer_seconds == 300


# T-HS-7
def test_cache_path_override_parameter_beats_all(tmp_path, monkeypatch):
    """``cache_path_override`` parameter wins over env var and hook config."""
    from autoskillit.hooks._hook_settings import resolve_quota_settings

    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)
    _write_hook_config(tmp_path, {"cache_path": "/bridge/cache.json"})
    monkeypatch.setenv("AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH", "/env/cache.json")

    settings = resolve_quota_settings(cache_path_override="/test.json")

    assert settings.cache_path == "/test.json"


# T-HS-8
def test_invalid_env_var_falls_through(tmp_path, monkeypatch):
    """Non-numeric env var values fall through to hook config / default."""
    from autoskillit.hooks._hook_settings import (
        DEFAULT_BUFFER_SECONDS,
        resolve_quota_settings,
    )

    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)
    monkeypatch.setenv("AUTOSKILLIT_QUOTA_GUARD__CACHE_MAX_AGE", "not-a-number")
    _write_hook_config(tmp_path, {"cache_max_age": 450})

    settings = resolve_quota_settings()

    # Invalid env var → falls through to hook config (450)
    assert settings.cache_max_age == 450
    # buffer_seconds has neither env var nor hook config → default
    assert settings.buffer_seconds == DEFAULT_BUFFER_SECONDS


# T-HS-9
def test_defaults_match_defaults_yaml():
    """Module default constants must match ``config/defaults.yaml`` exactly.

    Structural guard against drift between the stdlib-only hook module and the
    canonical dynaconf-loaded settings layer.
    """
    from autoskillit.core import load_yaml, pkg_root
    from autoskillit.hooks._hook_settings import (
        DEFAULT_BUFFER_SECONDS,
        DEFAULT_CACHE_MAX_AGE,
        DEFAULT_CACHE_PATH,
    )

    defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
    assert isinstance(defaults, dict)
    quota_guard = defaults["quota_guard"]
    assert DEFAULT_CACHE_PATH == quota_guard["cache_path"]
    assert DEFAULT_CACHE_MAX_AGE == quota_guard["cache_max_age"]
    assert DEFAULT_BUFFER_SECONDS == quota_guard["buffer_seconds"]
