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
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

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


# ---------------------------------------------------------------------------
# Session-scoped quota-disable marker helpers
# ---------------------------------------------------------------------------


def _write_quota_disable_marker(monkeypatch, *, state_dir: Path, session_id: str) -> None:
    """Helper: write a fresh quota-disable marker via the public helper."""
    from autoskillit.hooks._hook_settings import write_quota_disable_marker

    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(state_dir))
    write_quota_disable_marker(session_id)


def test_quota_disable_marker_path_uses_kitchen_state(tmp_path, monkeypatch):
    """Marker path must be <state_dir>/kitchen_state/<session_id>_quota_guard_disabled.json."""
    from autoskillit.hooks._hook_settings import quota_disable_marker_path

    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    path = quota_disable_marker_path("session-aaa")
    assert path == tmp_path / "kitchen_state" / "session-aaa_quota_guard_disabled.json"


def test_quota_disable_marker_path_rejects_empty_session_id(tmp_path, monkeypatch):
    """quota_disable_marker_path("") raises — empty session IDs are not allowed."""
    from autoskillit.hooks._hook_settings import quota_disable_marker_path

    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        quota_disable_marker_path("")


def test_quota_disable_marker_path_rejects_traversal_shapes(tmp_path, monkeypatch):
    """Path-separator and parent-relative session IDs are rejected."""
    from autoskillit.hooks._hook_settings import quota_disable_marker_path

    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    for bad in ("../escape", "sub/dir", "..", "/abs", "with space", "."):
        with pytest.raises(ValueError):
            quota_disable_marker_path(bad)


def test_write_quota_disable_marker_round_trips_for_exact_session(tmp_path, monkeypatch):
    """Fresh marker is read back only for its exact session ID."""
    from autoskillit.hooks._hook_settings import (
        clear_quota_disable_marker,
        quota_disable_marker_path,
        read_quota_disable_marker,
        write_quota_disable_marker,
    )

    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    write_quota_disable_marker("session-aaa")
    marker = quota_disable_marker_path("session-aaa")
    assert marker.exists()

    payload = read_quota_disable_marker("session-aaa")
    assert payload is not None
    assert payload["session_id"] == "session-aaa"
    assert "disabled_at" in payload
    assert payload.get("marker_version") == 1

    # Other session ID sees no marker
    assert read_quota_disable_marker("session-bbb") is None

    # Cleanup
    clear_quota_disable_marker("session-aaa")
    assert not marker.exists()


def test_read_quota_disable_marker_returns_none_for_other_session(tmp_path, monkeypatch):
    """A marker for session A is invisible to session B."""
    from autoskillit.hooks._hook_settings import (
        read_quota_disable_marker,
    )

    _write_quota_disable_marker(monkeypatch, state_dir=tmp_path, session_id="session-aaa")
    assert read_quota_disable_marker("session-bbb") is None


def test_read_quota_disable_marker_returns_none_for_malformed_payload(tmp_path, monkeypatch):
    """A malformed marker must NOT grant a quota bypass."""
    from autoskillit.hooks._hook_settings import (
        quota_disable_marker_path,
        read_quota_disable_marker,
    )

    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    state_dir = tmp_path / "kitchen_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    marker = quota_disable_marker_path("session-aaa")
    marker.write_text("not-json-{{{")
    assert read_quota_disable_marker("session-aaa") is None


def test_read_quota_disable_marker_returns_none_for_session_id_mismatch(tmp_path, monkeypatch):
    """Marker whose payload session_id disagrees with the query session must NOT grant bypass."""
    from autoskillit.hooks._hook_settings import (
        quota_disable_marker_path,
        read_quota_disable_marker,
    )

    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    state_dir = tmp_path / "kitchen_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    marker = quota_disable_marker_path("session-aaa")
    marker.write_text(
        json.dumps(
            {
                "session_id": "session-bbb",
                "disabled_at": "2026-01-01T00:00:00+00:00",
                "marker_version": 1,
            }
        )
    )
    assert read_quota_disable_marker("session-aaa") is None


def test_read_quota_disable_marker_returns_none_for_traversal_session_id(tmp_path, monkeypatch):
    """A session ID containing path separators is rejected outright."""
    from autoskillit.hooks._hook_settings import read_quota_disable_marker

    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    assert read_quota_disable_marker("../escape") is None


def test_read_quota_disable_marker_returns_none_for_expired_marker(tmp_path, monkeypatch):
    """A 25-hour-old marker must NOT grant a quota bypass (24h TTL)."""
    from autoskillit.hooks._hook_settings import (
        quota_disable_marker_path,
        read_quota_disable_marker,
    )

    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    state_dir = tmp_path / "kitchen_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    marker = quota_disable_marker_path("session-aaa")
    old = datetime.fromtimestamp(datetime.now(UTC).timestamp() - 25 * 3600, tz=UTC).isoformat()
    marker.write_text(
        json.dumps({"session_id": "session-aaa", "disabled_at": old, "marker_version": 1})
    )
    assert read_quota_disable_marker("session-aaa") is None


def test_clear_quota_disable_marker_leaves_other_session_intact(tmp_path, monkeypatch):
    """Clearing session A's marker leaves session B's marker intact."""
    from autoskillit.hooks._hook_settings import (
        clear_quota_disable_marker,
        quota_disable_marker_path,
        read_quota_disable_marker,
        write_quota_disable_marker,
    )

    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    write_quota_disable_marker("session-aaa")
    write_quota_disable_marker("session-bbb")

    clear_quota_disable_marker("session-aaa")
    assert not quota_disable_marker_path("session-aaa").exists()
    assert read_quota_disable_marker("session-bbb") is not None


def test_clear_quota_disable_marker_missing_file_is_noop(tmp_path, monkeypatch):
    """clear_quota_disable_marker on a non-existent marker is silently tolerated."""
    from autoskillit.hooks._hook_settings import clear_quota_disable_marker

    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    clear_quota_disable_marker("session-aaa")  # must not raise


def test_write_quota_disable_marker_respects_state_dir_override(tmp_path, monkeypatch):
    """AUTOSKILLIT_STATE_DIR controls the marker directory (used in tests + campaigns)."""
    from autoskillit.hooks._hook_settings import (
        quota_disable_marker_path,
        read_quota_disable_marker,
        write_quota_disable_marker,
    )

    state_dir = tmp_path / "campaign_state"
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(state_dir))
    write_quota_disable_marker("session-aaa")
    expected = state_dir / "kitchen_state" / "session-aaa_quota_guard_disabled.json"
    assert expected.exists()
    assert quota_disable_marker_path("session-aaa") == expected
    assert read_quota_disable_marker("session-aaa") is not None


def test_write_quota_disable_marker_respects_campaign_id(tmp_path, monkeypatch):
    """AUTOSKILLIT_CAMPAIGN_ID nests the marker under a campaign subdirectory."""
    from autoskillit.hooks._hook_settings import (
        quota_disable_marker_path,
        write_quota_disable_marker,
    )

    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "campaign-42")
    write_quota_disable_marker("session-aaa")
    expected = tmp_path / "kitchen_state" / "campaign-42" / "session-aaa_quota_guard_disabled.json"
    assert expected.exists()
    assert quota_disable_marker_path("session-aaa") == expected
