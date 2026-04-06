"""Tests for the quota_check PreToolUse hook.

The hook reads a local quota cache file and denies run_skill when utilization
exceeds the threshold. It fails open when the cache is missing/stale/corrupt.
"""

import io
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch


def _write_cache(cache_path: Path, utilization: float, resets_at: str | None = None) -> None:
    """Write a fresh quota cache file."""
    payload = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "five_hour": {
            "utilization": utilization,
            "resets_at": resets_at,
        },
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload))


def _run_hook(
    event: dict | None = None,
    raw_stdin: str | None = None,
    cache_path: Path | None = None,
) -> tuple[str, int]:
    """Run quota_check.main() with synthetic stdin and optional cache file.

    Returns (stdout, exit_code). stdout empty = approve, JSON string = deny.
    Uses cache_path_override parameter for DI — no module-level patching needed.
    """
    from autoskillit.hooks.quota_check import main

    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(event or {})

    buf = io.StringIO()
    exit_code = 0
    with patch("sys.stdin", io.StringIO(stdin_text)):
        with redirect_stdout(buf):
            try:
                main(cache_path_override=str(cache_path) if cache_path is not None else None)
            except SystemExit as e:
                exit_code = e.code if e.code is not None else 0
    return buf.getvalue(), exit_code


def test_deny_when_utilization_above_threshold(tmp_path):
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=95.0)
    out, _ = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_deny_message_contains_sleep_seconds(tmp_path):
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=95.0)
    out, _ = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    data = json.loads(out)
    reason = data["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Sleep" in reason
    assert "seconds" in reason


def test_approve_when_utilization_below_threshold(tmp_path):
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=50.0)
    out, _ = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    assert out.strip() == ""


def test_approve_when_cache_missing(tmp_path):
    cache = tmp_path / "nonexistent" / "quota_cache.json"
    out, _ = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    assert out.strip() == ""


def test_approve_when_cache_corrupt(tmp_path):
    cache = tmp_path / "quota_cache.json"
    cache.write_text("not-json-{{{")
    out, _ = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    assert out.strip() == ""


def test_approve_on_malformed_stdin(tmp_path):
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=95.0)
    out, _ = _run_hook(raw_stdin="not-json", cache_path=cache)
    assert out.strip() == ""


def test_deny_output_is_valid_json(tmp_path):
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=95.0)
    out, _ = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    parsed = json.loads(out)
    assert "hookSpecificOutput" in parsed


def test_approve_when_stale_cache(tmp_path):
    """Cache older than max age → fail open."""
    cache = tmp_path / "quota_cache.json"
    payload = {
        "fetched_at": "2020-01-01T00:00:00+00:00",
        "five_hour": {"utilization": 99.0, "resets_at": None},
    }
    cache.write_text(json.dumps(payload))
    out, _ = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    assert out.strip() == ""


def _write_hook_config(
    hook_cfg_path: Path, threshold: float, cache_max_age: int, cache_path: str
) -> None:
    """Write a hook config file to the given path."""
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(
        json.dumps(
            {
                "quota_guard": {
                    "threshold": threshold,
                    "cache_max_age": cache_max_age,
                    "cache_path": cache_path,
                }
            }
        )
    )


# T-CFG-1
def test_quota_check_reads_threshold_from_hook_config(tmp_path, monkeypatch):
    """Hook must deny when utilization exceeds user-configured threshold from hook config."""
    monkeypatch.chdir(tmp_path)
    cache = tmp_path / "custom_cache.json"
    _write_cache(cache, utilization=60.0)
    _write_hook_config(
        tmp_path / ".autoskillit" / "temp" / ".autoskillit_hook_config.json",
        threshold=50.0,
        cache_max_age=300,
        cache_path=str(cache),
    )
    out, _ = _run_hook(event={"tool_name": "run_skill"})
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


# T-CFG-2
def test_quota_check_reads_cache_path_from_hook_config(tmp_path, monkeypatch):
    """Hook must read cache from hook config cache_path when AUTOSKILLIT_QUOTA_CACHE is unset."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AUTOSKILLIT_QUOTA_CACHE", raising=False)
    custom_cache = tmp_path / "my_custom_cache.json"
    _write_cache(custom_cache, utilization=95.0)
    _write_hook_config(
        tmp_path / ".autoskillit" / "temp" / ".autoskillit_hook_config.json",
        threshold=85.0,
        cache_max_age=300,
        cache_path=str(custom_cache),
    )
    out, _ = _run_hook(event={"tool_name": "run_skill"})
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


# T-CFG-3
def test_quota_check_env_var_overrides_hook_config_cache_path(tmp_path, monkeypatch):
    """AUTOSKILLIT_QUOTA_CACHE env var must take precedence over hook config cache_path.

    Backward-compat regression test: env var path must still win even when hook config is present.
    """
    monkeypatch.chdir(tmp_path)
    correct_cache = tmp_path / "correct_cache.json"
    _write_cache(correct_cache, utilization=95.0)
    wrong_cache = tmp_path / "wrong_cache.json"  # not written — should not be read
    _write_hook_config(
        tmp_path / "temp" / ".autoskillit_hook_config.json",
        threshold=85.0,
        cache_max_age=300,
        cache_path=str(wrong_cache),
    )
    monkeypatch.setenv("AUTOSKILLIT_QUOTA_CACHE", str(correct_cache))
    out, _ = _run_hook(event={"tool_name": "run_skill"})
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


# T-CFG-4
def test_quota_check_falls_back_to_defaults_without_hook_config(tmp_path, monkeypatch):
    """Without hook config, hook falls back to hard-coded defaults (threshold=85.0).

    Regression test: no regression when kitchen was never opened or hook config was removed.
    """
    monkeypatch.chdir(tmp_path)
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=95.0)
    out, _ = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


# T1
def test_quota_event_approved_written_to_log(tmp_path, monkeypatch):
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=50.0)
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(log_dir))
    _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    events = [
        json.loads(line) for line in (log_dir / "quota_events.jsonl").read_text().splitlines()
    ]
    assert len(events) == 1
    assert events[0]["event"] == "approved"
    assert events[0]["utilization"] == 50.0
    assert events[0]["threshold"] == 85.0
    assert "ts" in events[0]


# T2
def test_quota_event_blocked_written_to_log(tmp_path, monkeypatch):
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=95.0)
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(log_dir))
    _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    events = [
        json.loads(line) for line in (log_dir / "quota_events.jsonl").read_text().splitlines()
    ]
    assert len(events) == 1
    ev = events[0]
    assert ev["event"] == "blocked"
    assert ev["utilization"] == 95.0
    assert ev["threshold"] == 85.0
    assert isinstance(ev["sleep_seconds"], int)
    assert ev["sleep_seconds"] > 0
    assert "ts" in ev


# T3
def test_quota_event_cache_miss_written_to_log(tmp_path, monkeypatch):
    missing_cache = tmp_path / "nonexistent" / "cache.json"
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(log_dir))
    _run_hook(event={"tool_name": "run_skill"}, cache_path=missing_cache)
    events = [
        json.loads(line) for line in (log_dir / "quota_events.jsonl").read_text().splitlines()
    ]
    assert len(events) == 1
    assert events[0]["event"] == "cache_miss"
    assert "ts" in events[0]


# T4
def test_quota_event_stale_cache_writes_cache_miss(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    payload = {
        "fetched_at": "2020-01-01T00:00:00+00:00",
        "five_hour": {"utilization": 99.0, "resets_at": None},
    }
    cache.write_text(json.dumps(payload))
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(log_dir))
    _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    events = [
        json.loads(line) for line in (log_dir / "quota_events.jsonl").read_text().splitlines()
    ]
    assert events[0]["event"] == "cache_miss"


# T5
def test_quota_event_parse_error_on_malformed_utilization(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    # Valid JSON but utilization is not a float
    cache.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(UTC).isoformat(),
                "five_hour": {"utilization": "not-a-number", "resets_at": None},
            }
        )
    )
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(log_dir))
    _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    events = [
        json.loads(line) for line in (log_dir / "quota_events.jsonl").read_text().splitlines()
    ]
    assert events[0]["event"] == "parse_error"


# T6
def test_quota_event_no_crash_when_log_dir_unresolvable(tmp_path, monkeypatch):
    """Hook must still complete normally when log write fails (fail-open)."""
    cache = tmp_path / "cache.json"
    _write_cache(cache, utilization=50.0)
    # Point log dir to a file path (not a directory) — mkdir will fail
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "not_a_dir.txt"))
    (tmp_path / "not_a_dir.txt").write_text("blocker")
    out, exit_code = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    # Hook still approves — no crash, no output
    assert out.strip() == ""
    assert exit_code == 0


# T-CFG-5
def test_defaults_yaml_cache_max_age_is_300():
    """defaults.yaml must have quota_guard.cache_max_age: 300 (was 60).

    A 60s TTL is shorter than typical pipeline steps (5-20 min), causing stale-cache
    fail-open between steps. 300s matches realistic inter-step gaps.
    """
    from autoskillit.core import load_yaml, pkg_root

    defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
    assert isinstance(defaults, dict)
    assert defaults["quota_guard"]["cache_max_age"] == 300


def test_resolve_quota_log_dir_and_resolve_log_dir_in_sync(monkeypatch):
    """_resolve_quota_log_dir() must produce the same path as resolve_log_dir('') for
    identical env inputs. Guards against independent evolution of the two implementations.

    Constraint: these functions MUST NOT be merged — quota_check.py is stdlib-only
    and self-contained. This test is the canonical drift guard.
    """
    from autoskillit.execution.session_log import resolve_log_dir
    from autoskillit.hooks.quota_check import _resolve_quota_log_dir

    # Case 1: platform default (no env overrides)
    monkeypatch.delenv("AUTOSKILLIT_LOG_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    quota_path = _resolve_quota_log_dir()
    session_path = resolve_log_dir("")

    assert quota_path is not None, "_resolve_quota_log_dir() must not return None"
    assert quota_path == session_path, (
        f"Log dir mismatch (no env overrides):\n"
        f"  quota_check._resolve_quota_log_dir(): {quota_path}\n"
        f"  session_log.resolve_log_dir(''): {session_path}"
    )

    # Case 2: XDG_DATA_HOME override
    monkeypatch.setenv("XDG_DATA_HOME", "/tmp/xdg-test")
    quota_path_xdg = _resolve_quota_log_dir()
    session_path_xdg = resolve_log_dir("")

    assert quota_path_xdg is not None
    assert quota_path_xdg == session_path_xdg, (
        f"Log dir mismatch (XDG_DATA_HOME set):\n"
        f"  quota_check: {quota_path_xdg}\n"
        f"  session_log: {session_path_xdg}"
    )
