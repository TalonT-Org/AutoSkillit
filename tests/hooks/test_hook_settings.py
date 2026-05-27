"""Tests for the shared stdlib-only quota hook settings resolver.

``autoskillit.hooks._hook_settings.resolve_quota_settings()`` resolves cache path,
cache max age, and buffer seconds from a layered hierarchy:

    1. ``cache_path_override`` parameter (tests / DI)
    2. ``AUTOSKILLIT_QUOTA_GUARD__<KEY>`` env var
    3. ``.autoskillit/temp/.hook_config.json`` snapshot
    4. Module defaults (matching ``config/defaults.yaml``)

These tests use ``tmp_path`` and ``monkeypatch`` for isolation — no global state.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime
from unittest.mock import patch

_ENV_VARS = (
    "AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH",
    "AUTOSKILLIT_QUOTA_GUARD__CACHE_MAX_AGE",
    "AUTOSKILLIT_QUOTA_GUARD__BUFFER_SECONDS",
)


def _clear_env(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _write_hook_config(tmp_path, quota_guard: dict) -> None:
    hook_cfg = tmp_path / ".autoskillit" / "temp" / ".hook_config.json"
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


def test_merged_hook_config_overlay_wins():
    """merge_hook_configs gives overlay precedence over base."""
    from autoskillit.hooks._hook_settings import merge_hook_configs

    base = {"quota_guard": {"disabled": False, "cache_max_age": 60}, "kitchen_id": "k1"}
    overlay = {"quota_guard": {"disabled": True}}
    merged = merge_hook_configs(base, overlay)
    assert merged["quota_guard"]["disabled"] is True
    assert merged["quota_guard"]["cache_max_age"] == 60
    assert merged["kitchen_id"] == "k1"


# T-HS-10
def test_read_quota_cache_returns_data_for_fresh_cache(tmp_path):
    """Call read_quota_cache with a fresh cache file and assert it returns the parsed dict."""
    from autoskillit.hooks._hook_settings import read_quota_cache

    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps({"fetched_at": datetime.now(UTC).isoformat(), "binding": {}}))
    result = read_quota_cache(str(cache_file), 300)
    assert result is not None
    assert "binding" in result


# T-HS-11
def test_read_quota_cache_returns_none_for_missing_file():
    """Call read_quota_cache with a nonexistent path and assert it returns None."""
    from autoskillit.hooks._hook_settings import read_quota_cache

    result = read_quota_cache("/nonexistent/path.json", 300)
    assert result is None


# T-HS-12
def test_read_quota_cache_returns_none_for_stale_cache(tmp_path):
    """Cache 10 min old; read_quota_cache returns None for max_age=300."""
    from autoskillit.hooks._hook_settings import read_quota_cache

    cache_file = tmp_path / "cache.json"
    old_time = datetime.now(UTC).timestamp() - 600  # 10 minutes ago
    cache_file.write_text(
        json.dumps(
            {"fetched_at": datetime.fromtimestamp(old_time, tz=UTC).isoformat(), "binding": {}}
        )
    )
    result = read_quota_cache(str(cache_file), 300)
    assert result is None


# T-HS-13
def test_read_quota_cache_returns_none_for_corrupt_json(tmp_path):
    """Write "not json" to a cache file and assert read_quota_cache returns None."""
    from autoskillit.hooks._hook_settings import read_quota_cache

    cache_file = tmp_path / "cache.json"
    cache_file.write_text("not json")
    result = read_quota_cache(str(cache_file), 300)
    assert result is None


# T-HS-14
def test_read_quota_cache_returns_none_on_type_error(tmp_path):
    """fetched_at: null triggers TypeError; read_quota_cache returns None."""
    from autoskillit.hooks._hook_settings import read_quota_cache

    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps({"fetched_at": None, "binding": {}}))
    result = read_quota_cache(str(cache_file), 300)
    assert result is None


# T-HS-15
def test_resolve_quota_log_dir_returns_platform_default(monkeypatch):
    """Unset env vars; resolve_quota_log_dir returns a Path ending with autoskillit/logs."""
    from autoskillit.hooks._hook_settings import resolve_quota_log_dir

    monkeypatch.delenv("AUTOSKILLIT_LOG_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    result = resolve_quota_log_dir()
    assert result is not None
    assert str(result).endswith("autoskillit/logs")


# T-HS-16
def test_resolve_quota_log_dir_respects_env_override(monkeypatch):
    """AUTOSKILLIT_LOG_DIR=/tmp/custom; resolve_quota_log_dir returns Path("/tmp/custom")."""
    from autoskillit.hooks._hook_settings import resolve_quota_log_dir

    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", "/tmp/custom")
    result = resolve_quota_log_dir()
    assert result == pathlib.Path("/tmp/custom")


# T-HS-17
def test_resolve_quota_log_dir_silent_when_no_caller(monkeypatch, capsys):
    """Path.home raises; resolve_quota_log_dir() returns None, stderr empty (no caller)."""
    from autoskillit.hooks._hook_settings import resolve_quota_log_dir

    def raise_():
        raise OSError("boom")

    monkeypatch.setattr(pathlib.Path, "home", staticmethod(raise_))
    result = resolve_quota_log_dir()
    assert result is None
    captured = capsys.readouterr()
    assert captured.err == ""


# T-HS-18
def test_resolve_quota_log_dir_prints_stderr_with_caller(monkeypatch, capsys):
    """Path.home raises; resolve_quota_log_dir(caller="test_hook") prints to stderr."""
    from autoskillit.hooks._hook_settings import resolve_quota_log_dir

    def raise_():
        raise OSError("boom")

    monkeypatch.setattr(pathlib.Path, "home", staticmethod(raise_))
    result = resolve_quota_log_dir(caller="test_hook")
    assert result is None
    captured = capsys.readouterr()
    assert "test_hook" in captured.err


# T-HS-19
def test_write_quota_log_event_writes_jsonl(tmp_path):
    """write_quota_log_event writes JSON line to quota_events.jsonl."""
    from autoskillit.hooks._hook_settings import write_quota_log_event

    write_quota_log_event({"event": "test"}, tmp_path)
    log_file = tmp_path / "quota_events.jsonl"
    assert log_file.exists()
    lines = log_file.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "test"


# T-HS-20
def test_write_quota_log_event_noop_when_log_dir_none():
    """Call write_quota_log_event with log_dir=None; assert no error and no output."""
    from autoskillit.hooks._hook_settings import write_quota_log_event

    # Should not raise
    write_quota_log_event({"event": "test"}, None)


# T-HS-21
def test_write_quota_log_event_silent_when_no_caller(tmp_path, capsys):
    """open raises; write_quota_log_event({}, tmp_path) stderr empty (no caller)."""
    from autoskillit.hooks._hook_settings import write_quota_log_event

    with patch("builtins.open", side_effect=OSError("disk full")):
        write_quota_log_event({}, tmp_path)
    captured = capsys.readouterr()
    assert captured.err == ""


# T-HS-22
def test_write_quota_log_event_prints_stderr_with_caller(tmp_path, capsys):
    """open raises; write_quota_log_event(..., caller="test_hook") prints to stderr."""
    from autoskillit.hooks._hook_settings import write_quota_log_event

    with patch("builtins.open", side_effect=OSError("disk full")):
        write_quota_log_event({}, tmp_path, caller="test_hook")
    captured = capsys.readouterr()
    assert "test_hook" in captured.err
