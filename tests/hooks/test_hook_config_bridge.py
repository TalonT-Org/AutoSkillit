"""Regression tests for the quota_guard.py → .hook_config.json bridge.

Tests the end-to-end path from _quota_guard_hook_payload serialization through
.hook_config.json to resolve_quota_settings() consumption by quota_guard.py.
No pytestmark — hooks/ is out of scope for layer markers.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

_ENV_VARS = (
    "AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH",
    "AUTOSKILLIT_QUOTA_GUARD__CACHE_MAX_AGE",
    "AUTOSKILLIT_QUOTA_GUARD__BUFFER_SECONDS",
    "AUTOSKILLIT_QUOTA_GUARD__DISABLED",
)


def _clear_env(monkeypatch) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _write_hook_config(tmp_path: Path, quota_guard: dict) -> None:
    hook_cfg = tmp_path / ".autoskillit" / "temp" / ".hook_config.json"
    hook_cfg.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg.write_text(json.dumps({"quota_guard": quota_guard}))


def _write_blocking_cache(cache_path: Path, *, fetched_at: str | None = None) -> None:
    """Write a quota cache with should_block=True. No resets_at → sleep = buffer_seconds."""
    payload = {
        "fetched_at": fetched_at or datetime.now(UTC).isoformat(),
        "binding": {
            "utilization": 95.0,
            "should_block": True,
            "effective_threshold": 85.0,
            "window_name": "five_hour",
        },
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload))


def _run_hook(event: dict | None = None) -> tuple[str, int]:
    """Run quota_guard.main() without cache_path_override — exercises the bridge path."""
    from autoskillit.hooks.quota_guard import main

    buf = io.StringIO()
    exit_code = 0
    with patch("sys.stdin", io.StringIO(json.dumps(event or {}))):
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit as e:
                exit_code = e.code if e.code is not None else 0
    return buf.getvalue(), exit_code


# T-BRIDGE-1
def test_hook_reads_cache_path_from_hook_config_and_denies(tmp_path, monkeypatch):
    """Hook reads cache from payload-written path and denies when should_block=True."""
    cache = tmp_path / "quota_cache.json"
    _write_blocking_cache(cache)
    _write_hook_config(
        tmp_path,
        {
            "cache_path": str(cache),
            "cache_max_age": 300,
            "buffer_seconds": 60,
            "disabled": False,
        },
    )
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    out, _ = _run_hook(event={"tool_name": "run_skill"})

    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


# T-BRIDGE-2
def test_deny_message_contains_sleep_from_payload_buffer_seconds(tmp_path, monkeypatch):
    """Deny message contains time.sleep(77) and Sleeping 77s from hook config buffer_seconds=77."""
    cache = tmp_path / "quota_cache.json"
    _write_blocking_cache(cache)  # no resets_at → n = buffer_seconds exactly
    _write_hook_config(
        tmp_path,
        {
            "cache_path": str(cache),
            "cache_max_age": 300,
            "buffer_seconds": 77,
            "disabled": False,
        },
    )
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    out, _ = _run_hook(event={"tool_name": "run_skill"})

    data = json.loads(out)
    reason = data["hookSpecificOutput"]["permissionDecisionReason"]
    assert "time.sleep(77)" in reason
    assert "Sleeping 77s" in reason


# T-BRIDGE-3
def test_stale_cache_fails_open_with_hook_config_max_age(tmp_path, monkeypatch):
    """Cache older than max_age from hook config treated as stale → fail-open approve."""
    cache = tmp_path / "quota_cache.json"
    old_fetched_at = (datetime.now(UTC) - timedelta(seconds=31)).isoformat()
    _write_blocking_cache(cache, fetched_at=old_fetched_at)
    _write_hook_config(
        tmp_path,
        {
            "cache_path": str(cache),
            "cache_max_age": 30,
            "buffer_seconds": 60,
            "disabled": False,
        },
    )
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    out, _ = _run_hook(event={"tool_name": "run_skill"})

    assert out == ""  # no deny JSON → approve (fail-open on stale cache)


# T-BRIDGE-4
def test_disabled_true_unconditionally_approves(tmp_path, monkeypatch):
    """disabled=True in hook config → unconditional approve even with a blocking cache."""
    cache = tmp_path / "quota_cache.json"
    _write_blocking_cache(cache)
    _write_hook_config(
        tmp_path,
        {
            "cache_path": str(cache),
            "cache_max_age": 300,
            "buffer_seconds": 60,
            "disabled": True,
        },
    )
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    out, _ = _run_hook(event={"tool_name": "run_skill"})

    assert out == ""  # guard bypassed entirely — no cache logic runs


# T-BRIDGE-5
def test_disabled_false_blocks_normally(tmp_path, monkeypatch):
    """enabled=True → disabled=False in hook config → hook blocks on should_block=True."""
    cache = tmp_path / "quota_cache.json"
    _write_blocking_cache(cache)
    _write_hook_config(
        tmp_path,
        {
            "cache_path": str(cache),
            "cache_max_age": 300,
            "buffer_seconds": 60,
            "disabled": False,
        },
    )
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    out, _ = _run_hook(event={"tool_name": "run_skill"})

    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


# T-BRIDGE-6
def test_hook_config_quota_guard_keys_match_payload_keys(tmp_path):
    """set(data['quota_guard'].keys()) == QUOTA_GUARD_HOOK_PAYLOAD_KEYS."""
    from autoskillit.config.settings import QuotaGuardConfig
    from autoskillit.hooks._hook_settings import QUOTA_GUARD_HOOK_PAYLOAD_KEYS
    from autoskillit.server.tools_kitchen import _quota_guard_hook_payload

    cfg = QuotaGuardConfig()
    payload = {"quota_guard": _quota_guard_hook_payload(cfg)}
    hook_cfg = tmp_path / ".autoskillit" / "temp" / ".hook_config.json"
    hook_cfg.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg.write_text(json.dumps(payload))

    data = json.loads(hook_cfg.read_text())
    assert set(data["quota_guard"].keys()) == QUOTA_GUARD_HOOK_PAYLOAD_KEYS


# T-BRIDGE-7
def test_write_hook_config_round_trip_via_resolve_quota_settings(tmp_path, monkeypatch):
    """_write_hook_config round-trip: payload written by _quota_guard_hook_payload
    is correctly read back by resolve_quota_settings()."""
    from autoskillit.config.settings import QuotaGuardConfig
    from autoskillit.hooks._hook_settings import resolve_quota_settings
    from autoskillit.server.tools_kitchen import _quota_guard_hook_payload

    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    cfg = QuotaGuardConfig(
        cache_max_age=999,
        buffer_seconds=42,
        cache_path="/round/trip.json",
        enabled=True,
    )
    hook_cfg = tmp_path / ".autoskillit" / "temp" / ".hook_config.json"
    hook_cfg.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg.write_text(json.dumps({"quota_guard": _quota_guard_hook_payload(cfg)}))

    settings = resolve_quota_settings()

    assert settings.cache_max_age == 999
    assert settings.buffer_seconds == 42
    assert settings.cache_path == "/round/trip.json"
    assert settings.disabled is False  # enabled=True → disabled=False
