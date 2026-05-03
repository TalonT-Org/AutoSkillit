"""Tests for the quota_check PreToolUse hook.

The hook reads a local quota cache file and denies run_skill when the cached
binding marks ``should_block=True``. It fails open when the cache is
missing/stale/corrupt.
"""

import io
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from autoskillit.hooks.formatters._fmt_primitives import _HOOK_CONFIG_PATH_COMPONENTS

_LONG_PATTERNS = ("seven_day", "sonnet", "opus")


def _classify_threshold(window_name: str) -> float:
    lowered = window_name.lower()
    return 98.0 if any(p in lowered for p in _LONG_PATTERNS) else 85.0


def _write_cache(
    cache_path: Path,
    utilization: float,
    resets_at: str | None = None,
    window_name: str = "five_hour",
    extra_windows: dict | None = None,
    should_block: bool | None = None,
    effective_threshold: float | None = None,
) -> None:
    """Write a fresh quota cache file in the full-snapshot format.

    When ``should_block`` is None, classifies the window via the default
    long-window patterns and computes ``should_block`` from utilization.
    """
    if effective_threshold is None:
        effective_threshold = _classify_threshold(window_name)
    if should_block is None:
        should_block = utilization >= effective_threshold
    windows = {window_name: {"utilization": utilization, "resets_at": resets_at}}
    if extra_windows:
        windows.update(extra_windows)
    payload = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "windows": windows,
        "binding": {
            "window_name": window_name,
            "utilization": utilization,
            "resets_at": resets_at,
            "should_block": should_block,
            "effective_threshold": effective_threshold,
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
    from autoskillit.hooks.guards.quota_guard import main

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
    assert "Sleeping" in reason
    assert "time.sleep" in reason


def test_deny_message_contains_echo_repeat(tmp_path):
    """Updated PreToolUse deny message includes echo/repeat instruction."""
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=90.0)
    out, _ = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    data = json.loads(out)
    reason = data["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Before executing, state aloud:" in reason
    assert "QUOTA WAIT REQUIRED" in reason


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
    hook_cfg_path: Path,
    cache_max_age: int,
    cache_path: str,
    extra: dict | None = None,
) -> None:
    """Write a hook config file to the given path."""
    quota_guard: dict = {
        "cache_max_age": cache_max_age,
        "cache_path": cache_path,
    }
    if extra:
        quota_guard.update(extra)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(json.dumps({"quota_guard": quota_guard}))


# T-CFG-1: hook trusts cache binding, never re-derives a verdict from hook config.
def test_hook_does_not_consult_threshold_field_in_hook_config(tmp_path, monkeypatch):
    """Hook trusts ``binding.should_block`` and ignores any threshold in hook config.

    Single-source-of-truth assertion: even if a stale ``short_window_threshold``
    in hook config would imply a deny, the hook must approve when the cache
    binding says ``should_block=False``.
    """
    monkeypatch.chdir(tmp_path)
    cache = tmp_path / "custom_cache.json"
    _write_cache(
        cache,
        utilization=70.0,
        window_name="five_hour",
        should_block=False,
        effective_threshold=85.0,
    )
    _write_hook_config(
        tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS),
        cache_max_age=300,
        cache_path=str(cache),
        extra={"short_window_threshold": 50.0},
    )
    out, _ = _run_hook(event={"tool_name": "run_skill"})
    assert out.strip() == ""


# T-CFG-2
def test_quota_check_reads_cache_path_from_hook_config(tmp_path, monkeypatch):
    """Hook must read cache from hook config cache_path when the env var is unset."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH", raising=False)
    custom_cache = tmp_path / "my_custom_cache.json"
    _write_cache(custom_cache, utilization=95.0)
    _write_hook_config(
        tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS),
        cache_max_age=300,
        cache_path=str(custom_cache),
    )
    out, _ = _run_hook(event={"tool_name": "run_skill"})
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


# T-CFG-3
def test_quota_check_env_var_overrides_hook_config_cache_path(tmp_path, monkeypatch):
    """``AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH`` must beat hook config ``cache_path``.

    Regression test: env var path must win even when a hook config is present at the
    canonical path (.autoskillit/.hook_config.json). Writing different
    utilization values to each cache confirms which source the hook actually reads.
    """
    monkeypatch.chdir(tmp_path)
    # env-var cache: high utilization → should deny if env var wins
    correct_cache = tmp_path / "correct_cache.json"
    _write_cache(correct_cache, utilization=95.0)
    # hook-config cache: low utilization → would approve if hook config wins
    wrong_cache = tmp_path / "wrong_cache.json"
    _write_cache(wrong_cache, utilization=50.0)
    _write_hook_config(
        tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS),
        cache_max_age=300,
        cache_path=str(wrong_cache),
    )
    monkeypatch.setenv("AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH", str(correct_cache))
    out, _ = _run_hook(event={"tool_name": "run_skill"})
    data = json.loads(out)
    # env var must win: deny because correct_cache has utilization=95.0
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


# T-CFG-4
def test_quota_check_falls_back_to_defaults_without_hook_config(tmp_path, monkeypatch):
    """Without hook config, hook still resolves cache via override / env var."""
    monkeypatch.chdir(tmp_path)
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=95.0)
    out, _ = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


# T-CFG-6
def test_quota_check_buffer_seconds_from_hook_config(tmp_path, monkeypatch):
    """Hook uses buffer_seconds from hook config when env var is unset.

    Writes a cache with ``resets_at=None`` so the hook takes the plain-buffer branch
    (``n = settings.buffer_seconds``), making the assertion deterministic.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AUTOSKILLIT_QUOTA_GUARD__BUFFER_SECONDS", raising=False)
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=95.0, resets_at=None)
    _write_hook_config(
        tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS),
        cache_max_age=300,
        cache_path=str(cache),
        extra={"buffer_seconds": 120},
    )
    out, _ = _run_hook(event={"tool_name": "run_skill"})
    data = json.loads(out)
    reason = data["hookSpecificOutput"]["permissionDecisionReason"]
    assert "time.sleep(120)" in reason
    assert "Sleeping 120s" in reason


# T-CFG-7
def test_quota_check_buffer_seconds_env_var_override(tmp_path, monkeypatch):
    """``AUTOSKILLIT_QUOTA_GUARD__BUFFER_SECONDS`` overrides the deny-message sleep duration."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOSKILLIT_QUOTA_GUARD__BUFFER_SECONDS", "180")
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=95.0, resets_at=None)
    out, _ = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    data = json.loads(out)
    reason = data["hookSpecificOutput"]["permissionDecisionReason"]
    assert "time.sleep(180)" in reason
    assert "Sleeping 180s" in reason


# T-CFG-8
def test_quota_check_cache_max_age_env_var_override(tmp_path, monkeypatch):
    """AUTOSKILLIT_QUOTA_GUARD__CACHE_MAX_AGE env var overrides the cache freshness window.

    With ``cache_max_age=60`` and a 61-second-old cache, the hook must treat the
    cache as stale and emit a cache_miss event (fail-open approve).
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOSKILLIT_QUOTA_GUARD__CACHE_MAX_AGE", "60")
    cache = tmp_path / "quota_cache.json"
    stale_fetched_at = (datetime.now(UTC) - timedelta(seconds=61)).isoformat()
    payload = {
        "fetched_at": stale_fetched_at,
        "windows": {"five_hour": {"utilization": 95.0, "resets_at": None}},
        "binding": {
            "window_name": "five_hour",
            "utilization": 95.0,
            "resets_at": None,
            "should_block": True,
            "effective_threshold": 85.0,
        },
    }
    cache.write_text(json.dumps(payload))
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(log_dir))
    out, exit_code = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    assert out.strip() == ""
    assert exit_code == 0
    events = [
        json.loads(line) for line in (log_dir / "quota_events.jsonl").read_text().splitlines()
    ]
    assert events[0]["event"] == "cache_miss"


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
    assert events[0]["effective_threshold"] == 85.0
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
    assert ev["effective_threshold"] == 85.0
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


# T-HOOK-PWT-1: regression test for #721 — seven_day at 86% must approve.
def test_hook_approves_seven_day_at_86_percent_should_block_false(tmp_path):
    """seven_day window at 86% utilisation must NOT be blocked.

    Regression test for issue #721: long-window quotas (seven_day/sonnet/opus)
    use a higher threshold (98%), so 86% is comfortable headroom — not exhaustion.
    """
    cache = tmp_path / "quota_cache.json"
    _write_cache(
        cache,
        utilization=86.0,
        window_name="seven_day",
        should_block=False,
        effective_threshold=98.0,
    )
    out, _ = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    assert out.strip() == ""


# T-HOOK-PWT-2: seven_day above 98% must still deny.
def test_hook_blocks_seven_day_above_long_threshold(tmp_path):
    cache = tmp_path / "quota_cache.json"
    _write_cache(
        cache,
        utilization=99.0,
        window_name="seven_day",
        should_block=True,
        effective_threshold=98.0,
    )
    out, _ = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = data["hookSpecificOutput"]["permissionDecisionReason"]
    assert "seven_day" in reason
    assert "98" in reason


# T-HOOK-PWT-3: short window above 85% must deny with short threshold in message.
def test_hook_blocks_short_window_above_threshold_message(tmp_path):
    cache = tmp_path / "quota_cache.json"
    _write_cache(
        cache,
        utilization=90.0,
        window_name="five_hour",
        should_block=True,
        effective_threshold=85.0,
    )
    out, _ = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    data = json.loads(out)
    reason = data["hookSpecificOutput"]["permissionDecisionReason"]
    assert "five_hour" in reason
    assert "85" in reason


# T-HOOK-PWT-4: missing should_block field → fail open (approve).
def test_hook_falls_back_when_should_block_field_missing(tmp_path):
    """Old-format cache without should_block defaults to approve (fail-open)."""
    cache = tmp_path / "quota_cache.json"
    payload = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "windows": {"five_hour": {"utilization": 95.0, "resets_at": None}},
        "binding": {
            "window_name": "five_hour",
            "utilization": 95.0,
            "resets_at": None,
        },
    }
    cache.write_text(json.dumps(payload))
    out, _ = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    assert out.strip() == ""


# T-HOOK-MW-1: cache with one_hour binding above threshold → deny
def test_deny_when_binding_window_is_one_hour_exhausted(tmp_path):
    """Hook denies when binding window (one_hour) is above threshold."""
    cache = tmp_path / "quota_cache.json"
    _write_cache(
        cache,
        utilization=91.0,
        window_name="one_hour",
        extra_windows={"five_hour": {"utilization": 35.0, "resets_at": None}},
    )
    out, _ = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


# T-HOOK-MW-2: cache with binding below threshold → approve
def test_approve_when_binding_below_threshold(tmp_path):
    """Hook approves when binding utilization is below threshold (even with high other window)."""
    cache = tmp_path / "quota_cache.json"
    _write_cache(
        cache,
        utilization=35.0,
        window_name="five_hour",
        extra_windows={"one_hour": {"utilization": 20.0, "resets_at": None}},
    )
    out, exit_code = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    assert exit_code == 0
    assert out.strip() == ""


# Test 18: hook reads should_block=False from a cache produced with long_enabled=False
def test_hook_respects_should_block_false_when_class_disabled(tmp_path):
    """Hook approves when cache binding has should_block=False, even if utilization is high.

    Simulates the outcome of _compute_binding called with long_enabled=False:
    the binding is five_hour at 30%, should_block=False, effective_threshold=85.0.
    The hook must exit 0 with no permissionDecision deny line — zero hook code changes required.
    """
    cache = tmp_path / "quota_cache.json"
    _write_cache(
        cache,
        utilization=30.0,
        window_name="five_hour",
        should_block=False,
        effective_threshold=85.0,
    )
    out, exit_code = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    assert exit_code == 0
    assert out.strip() == ""


def test_resolve_quota_log_dir_and_resolve_log_dir_in_sync(monkeypatch):
    """_resolve_quota_log_dir() must produce the same path as resolve_log_dir('') for
    identical env inputs. Guards against independent evolution of the two implementations.

    Constraint: these functions MUST NOT be merged — quota_guard.py is stdlib-only
    and self-contained. This test is the canonical drift guard.
    """
    from autoskillit.execution.session_log import resolve_log_dir
    from autoskillit.hooks.guards.quota_guard import _resolve_quota_log_dir

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


def test_hook_approves_when_disabled_flag_set_in_hook_config(tmp_path, monkeypatch):
    """Hook bypasses all checks and approves when disabled=True in hook config.

    Even with a high-utilization blocking cache, the hook must exit 0 with
    no output when the quota_guard.disabled flag is set.
    """
    monkeypatch.chdir(tmp_path)
    cache = tmp_path / "blocking_cache.json"
    _write_cache(cache, utilization=99.0, should_block=True)
    _write_hook_config(
        tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS),
        cache_max_age=300,
        cache_path=str(cache),
        extra={"disabled": True},
    )
    out, _ = _run_hook(event={"tool_name": "run_skill"})
    assert out.strip() == ""


def test_hook_still_blocks_without_disabled_flag(tmp_path, monkeypatch):
    """Regression guard: hook still blocks when disabled key is absent from hook config."""
    monkeypatch.chdir(tmp_path)
    cache = tmp_path / "blocking_cache.json"
    _write_cache(cache, utilization=99.0, should_block=True)
    _write_hook_config(
        tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS),
        cache_max_age=300,
        cache_path=str(cache),
    )
    out, _ = _run_hook(event={"tool_name": "run_skill"})
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
