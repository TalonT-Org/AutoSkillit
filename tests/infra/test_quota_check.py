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
