"""Tests for the quota_post_check PostToolUse hook.

The hook fires after run_skill completes and checks whether post-execution
quota utilization exceeds the threshold. When over threshold, it replaces the
tool output with a quota warning + sleep instruction via updatedMCPToolOutput.
"""

import io
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

_LONG_PATTERNS = ("weekly", "sonnet", "opus")


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


def _build_event(
    tool_name: str = "mcp__plugin_autoskillit_autoskillit__run_skill",
    success: bool = True,
    result_text: str = "plan written",
) -> dict:
    """Build a synthetic PostToolUse event for run_skill."""
    inner = json.dumps({"success": success, "result": result_text})
    outer = json.dumps({"result": inner})
    return {
        "tool_name": tool_name,
        "tool_response": outer,
    }


def _run_hook(
    event: dict | None = None,
    raw_stdin: str | None = None,
    cache_path: Path | None = None,
) -> tuple[str, int]:
    """Run quota_post_check.main() with synthetic stdin and optional cache file.

    Returns (stdout, exit_code). stdout empty = no warning, JSON string = warning.
    """
    from autoskillit.hooks.quota_post_hook import main

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


# T1: PostToolUse quota warning emitted when over threshold
def test_qpc1_emits_warning_when_over_threshold(tmp_path):
    """PostToolUse hook emits updatedMCPToolOutput with quota warning."""
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=90.0)
    event = _build_event()
    out, _ = _run_hook(event=event, cache_path=cache)
    data = json.loads(out)
    assert "hookSpecificOutput" in data
    assert "updatedMCPToolOutput" in data["hookSpecificOutput"]
    assert "QUOTA WARNING" in data["hookSpecificOutput"]["updatedMCPToolOutput"]


# T2: Silent exit when under threshold
def test_qpc2_silent_when_under_threshold(tmp_path):
    """PostToolUse hook exits silently (no stdout) when utilization < threshold."""
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=50.0)
    event = _build_event()
    out, _ = _run_hook(event=event, cache_path=cache)
    assert out.strip() == ""


# T3: Fail-open on missing cache
def test_qpc3_silent_on_missing_cache(tmp_path):
    """PostToolUse hook exits silently when cache file does not exist."""
    event = _build_event()
    out, _ = _run_hook(event=event, cache_path=tmp_path / "nonexistent.json")
    assert out.strip() == ""


# T4: Fail-open on corrupt cache
def test_qpc4_silent_on_corrupt_cache(tmp_path):
    """PostToolUse hook exits silently when cache file contains invalid JSON."""
    cache = tmp_path / "quota_cache.json"
    cache.write_text("not-json-{{{")
    event = _build_event()
    out, _ = _run_hook(event=event, cache_path=cache)
    assert out.strip() == ""


# T5: Fail-open on stale cache
def test_qpc5_silent_on_stale_cache(tmp_path):
    """PostToolUse hook exits silently when cache is older than max_age."""
    cache = tmp_path / "quota_cache.json"
    payload = {
        "fetched_at": "2020-01-01T00:00:00+00:00",
        "five_hour": {"utilization": 99.0, "resets_at": None},
    }
    cache.write_text(json.dumps(payload))
    event = _build_event()
    out, _ = _run_hook(event=event, cache_path=cache)
    assert out.strip() == ""


# T6: Warning output includes sleep seconds and run_cmd instruction
def test_qpc6_warning_contains_sleep_instruction(tmp_path):
    """Warning output includes explicit run_cmd sleep command with correct seconds."""
    cache = tmp_path / "quota_cache.json"
    resets_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    _write_cache(cache, utilization=90.0, resets_at=resets_at)
    event = _build_event()
    out, _ = _run_hook(event=event, cache_path=cache)
    data = json.loads(out)
    output = data["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "run_cmd" in output
    assert "time.sleep" in output


# T7: Warning preserves run_skill result summary
def test_qpc7_warning_preserves_result_summary(tmp_path):
    """updatedMCPToolOutput includes the run_skill result (success/fail, key fields)."""
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=90.0)
    event = _build_event(success=True, result_text="plan written")
    out, _ = _run_hook(event=event, cache_path=cache)
    data = json.loads(out)
    output = data["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "success: True" in output
    assert "plan written" in output


# T8: JSONL event logging for post-check warning
def test_qpc8_warning_event_written_to_log(tmp_path, monkeypatch):
    """Post-check logs a 'post_check_warning' event to quota_events.jsonl."""
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=90.0)
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(log_dir))
    event = _build_event()
    _run_hook(event=event, cache_path=cache)
    events = [
        json.loads(line) for line in (log_dir / "quota_events.jsonl").read_text().splitlines()
    ]
    assert len(events) == 1
    assert events[0]["event"] == "post_check_warning"


# T9: JSONL event logging for post-check pass
def test_qpc9_pass_event_written_to_log(tmp_path, monkeypatch):
    """Post-check logs a 'post_check_pass' event when under threshold."""
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=50.0)
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(log_dir))
    event = _build_event()
    _run_hook(event=event, cache_path=cache)
    events = [
        json.loads(line) for line in (log_dir / "quota_events.jsonl").read_text().splitlines()
    ]
    assert len(events) == 1
    assert events[0]["event"] == "post_check_pass"


# T10: Hook fires on failed run_skill results too
def test_qpc10_fires_on_failed_run_skill(tmp_path):
    """PostToolUse hook checks quota even when run_skill returned success=False."""
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=90.0)
    event = _build_event(success=False)
    out, _ = _run_hook(event=event, cache_path=cache)
    data = json.loads(out)
    assert "QUOTA WARNING" in data["hookSpecificOutput"]["updatedMCPToolOutput"]


# T11: Hook reads cache_path from hook config
def test_qpc11_reads_cache_path_from_hook_config(tmp_path, monkeypatch):
    """PostToolUse hook reads cache_path from .autoskillit/.hook_config.json.

    The hook trusts the cache binding's ``should_block`` flag — it never re-derives
    a verdict from a hook config threshold. This test only verifies that the hook
    locates the cache file via the hook config cache_path setting.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AUTOSKILLIT_QUOTA_CACHE", raising=False)
    cache = tmp_path / "custom_cache.json"
    _write_cache(cache, utilization=95.0)
    hook_cfg_path = tmp_path / ".autoskillit" / "temp" / ".hook_config.json"
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(
        json.dumps(
            {
                "quota_guard": {
                    "cache_max_age": 300,
                    "cache_path": str(cache),
                }
            }
        )
    )
    event = _build_event()
    out, _ = _run_hook(event=event)
    data = json.loads(out)
    assert "QUOTA WARNING" in data["hookSpecificOutput"]["updatedMCPToolOutput"]


# T12: Hook registered in HOOK_REGISTRY
def test_qpc12_registered_in_hook_registry():
    """quota_post_hook.py is registered as PostToolUse in HOOK_REGISTRY."""
    from autoskillit.hook_registry import HOOK_REGISTRY

    post_tool_scripts = [
        s for h in HOOK_REGISTRY if h.event_type == "PostToolUse" for s in h.scripts
    ]
    assert "quota_post_hook.py" in post_tool_scripts


# T13: Fail-open on malformed stdin
def test_qpc13_failopen_on_malformed_stdin(tmp_path):
    """PostToolUse hook exits silently when stdin is not valid JSON."""
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=90.0)
    out, exit_code = _run_hook(raw_stdin="not-json-garbage", cache_path=cache)
    assert out.strip() == ""
    assert exit_code == 0


# T14: Non-run_skill tools do not trigger post-check
def test_qpc14_only_run_skill_matcher():
    """The hook_registry PostToolUse entry for quota_post_check matches only run_skill."""
    import re

    from autoskillit.hook_registry import HOOK_REGISTRY

    entry = next(
        h
        for h in HOOK_REGISTRY
        if h.event_type == "PostToolUse" and "quota_post_hook.py" in h.scripts
    )
    assert re.match(entry.matcher, "mcp__plugin_autoskillit_autoskillit__run_skill")
    assert not re.match(entry.matcher, "mcp__plugin_autoskillit_autoskillit__run_cmd")
    assert not re.match(entry.matcher, "mcp__plugin_autoskillit_autoskillit__kitchen_status")


# T-PCHK-PWT-1: regression test for #721 — post_check silent for weekly at 86%.
def test_post_hook_silent_when_weekly_below_long_threshold(tmp_path):
    """Weekly window at 86% must NOT emit a warning. Regression test for #721."""
    cache = tmp_path / "quota_cache.json"
    _write_cache(
        cache,
        utilization=86.0,
        window_name="weekly",
        should_block=False,
        effective_threshold=98.0,
    )
    event = _build_event()
    out, exit_code = _run_hook(event=event, cache_path=cache)
    assert out.strip() == ""
    assert exit_code == 0


# T-PCHK-PWT-2: weekly above 98% must still warn.
def test_post_hook_warns_when_weekly_above_long_threshold(tmp_path):
    cache = tmp_path / "quota_cache.json"
    _write_cache(
        cache,
        utilization=99.0,
        window_name="weekly",
        should_block=True,
        effective_threshold=98.0,
    )
    event = _build_event()
    out, _ = _run_hook(event=event, cache_path=cache)
    data = json.loads(out)
    output = data["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "QUOTA WARNING" in output
    assert "weekly" in output
    assert "98" in output


# T-PCHK-MW-1: post_check warns when binding window is exhausted (not five_hour)
def test_warns_when_binding_window_exhausted(tmp_path):
    """PostToolUse hook emits warning when binding is one_hour (not five_hour)."""
    cache = tmp_path / "quota_cache.json"
    _write_cache(
        cache,
        utilization=91.0,
        window_name="one_hour",
        extra_windows={"five_hour": {"utilization": 35.0, "resets_at": None}},
    )
    event = _build_event()
    out, _ = _run_hook(event=event, cache_path=cache)
    data = json.loads(out)
    assert "QUOTA WARNING" in data["hookSpecificOutput"]["updatedMCPToolOutput"]
