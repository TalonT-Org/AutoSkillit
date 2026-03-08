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
) -> str:
    """Run quota_check.main() with synthetic stdin and optional cache file.

    Returns captured stdout (empty string = approve, JSON string = deny).
    """
    from autoskillit.hooks.quota_check import main

    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(event or {})

    patches = [
        patch("sys.stdin", io.StringIO(stdin_text)),
    ]
    if cache_path is not None:
        patches.append(
            patch(
                "autoskillit.hooks.quota_check._DEFAULT_CACHE_PATH",
                str(cache_path),
            )
        )

    buf = io.StringIO()
    ctx_stack = patches[0]
    for p in patches[1:]:
        ctx_stack = _nested(ctx_stack, p)

    with _apply_patches(patches):
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
    return buf.getvalue()


class _apply_patches:
    """Context manager to apply multiple patches."""

    def __init__(self, patches):
        self._patches = patches
        self._contexts = []

    def __enter__(self):
        for p in self._patches:
            self._contexts.append(p.__enter__())
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.__exit__(*exc)


def _nested(cm1, cm2):
    """Not used — replaced by _apply_patches."""


def test_deny_when_utilization_above_threshold(tmp_path):
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=95.0)
    out = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_deny_message_contains_sleep_seconds(tmp_path):
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=95.0)
    out = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    data = json.loads(out)
    reason = data["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Sleep" in reason
    assert "seconds" in reason


def test_approve_when_utilization_below_threshold(tmp_path):
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=50.0)
    out = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    assert out.strip() == ""


def test_approve_when_cache_missing(tmp_path):
    cache = tmp_path / "nonexistent" / "quota_cache.json"
    out = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    assert out.strip() == ""


def test_approve_when_cache_corrupt(tmp_path):
    cache = tmp_path / "quota_cache.json"
    cache.write_text("not-json-{{{")
    out = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    assert out.strip() == ""


def test_approve_on_malformed_stdin(tmp_path):
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=95.0)
    out = _run_hook(raw_stdin="not-json", cache_path=cache)
    assert out.strip() == ""


def test_deny_output_is_valid_json(tmp_path):
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=95.0)
    out = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
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
    out = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
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
    """Hook must deny when utilization exceeds user-configured threshold from hook config.

    Today: hook ignores hook config file → uses default threshold (90.0) → approves at 60.0.
    After fix: reads threshold=50.0 from hook config → denies at 60.0 utilization.
    """
    monkeypatch.chdir(tmp_path)
    cache = tmp_path / "custom_cache.json"
    _write_cache(cache, utilization=60.0)
    _write_hook_config(
        tmp_path / "temp" / ".autoskillit_hook_config.json",
        threshold=50.0,
        cache_max_age=300,
        cache_path=str(cache),
    )
    out = _run_hook(event={"tool_name": "run_skill"})
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


# T-CFG-2
def test_quota_check_reads_cache_path_from_hook_config(tmp_path, monkeypatch):
    """Hook must read cache from hook config cache_path when AUTOSKILLIT_QUOTA_CACHE is unset.

    Today: hook uses default path (~/.claude/autoskillit_quota_cache.json) → missing → fail open.
    After fix: reads cache_path from hook config → finds cache at custom path → denies.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AUTOSKILLIT_QUOTA_CACHE", raising=False)
    custom_cache = tmp_path / "my_custom_cache.json"
    _write_cache(custom_cache, utilization=95.0)
    _write_hook_config(
        tmp_path / "temp" / ".autoskillit_hook_config.json",
        threshold=90.0,
        cache_max_age=300,
        cache_path=str(custom_cache),
    )
    out = _run_hook(event={"tool_name": "run_skill"})
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
        threshold=90.0,
        cache_max_age=300,
        cache_path=str(wrong_cache),
    )
    monkeypatch.setenv("AUTOSKILLIT_QUOTA_CACHE", str(correct_cache))
    out = _run_hook(event={"tool_name": "run_skill"})
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


# T-CFG-4
def test_quota_check_falls_back_to_defaults_without_hook_config(tmp_path, monkeypatch):
    """Without hook config, hook falls back to hard-coded defaults (threshold=90.0).

    Regression test: no regression when kitchen was never opened or hook config was removed.
    """
    monkeypatch.chdir(tmp_path)
    cache = tmp_path / "quota_cache.json"
    _write_cache(cache, utilization=95.0)
    out = _run_hook(event={"tool_name": "run_skill"}, cache_path=cache)
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


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
