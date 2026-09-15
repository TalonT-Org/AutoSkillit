"""Tests for the diagnostic-only quota PostToolUse hook."""

import io
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]


def _write_cache(
    cache_path: Path,
    *,
    utilization: float,
    resets_at: datetime | None = None,
    should_block: bool | None = None,
) -> None:
    if should_block is None:
        should_block = utilization >= 85.0
    cache_path.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(UTC).isoformat(),
                "credential_scope": "",
                "windows": {
                    "five_hour": {
                        "utilization": utilization,
                        "resets_at": resets_at.isoformat() if resets_at else None,
                    }
                },
                "binding": {
                    "window_name": "five_hour",
                    "utilization": utilization,
                    "resets_at": resets_at.isoformat() if resets_at else None,
                    "should_block": should_block,
                    "effective_threshold": 85.0,
                },
            }
        )
    )


def _run_hook(cache_path: Path, event: dict | None = None) -> str:
    from autoskillit.hooks.quota_post_hook import main

    stdout = io.StringIO()
    with patch("sys.stdin", io.StringIO(json.dumps(event or {"tool_name": "run_skill"}))):
        with redirect_stdout(stdout), pytest.raises(SystemExit) as exit_info:
            main(cache_path_override=str(cache_path))
    assert exit_info.value.code == 0
    return stdout.getvalue()


def test_over_threshold_quota_preserves_the_completed_tool_output(tmp_path) -> None:
    cache = tmp_path / "quota-cache.json"
    _write_cache(
        cache,
        utilization=95.0,
        resets_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    with patch("autoskillit.hooks.quota_post_hook.write_quota_log_event") as write_event:
        output = _run_hook(cache, {"tool_name": "run_skill", "tool_response": {"success": True}})

    assert output == ""
    event = write_event.call_args.args[0]
    assert event["event"] == "post_quota_observation"
    assert event["constraint_observed"] is True
    assert event["resets_at"] is not None


def test_under_threshold_quota_is_recorded_after_run_skill(tmp_path) -> None:
    cache = tmp_path / "quota-cache.json"
    _write_cache(cache, utilization=50.0)

    with patch("autoskillit.hooks.quota_post_hook.write_quota_log_event") as write_event:
        output = _run_hook(cache)

    assert output == ""
    event = write_event.call_args.args[0]
    assert event["event"] == "post_quota_observation"
    assert event["constraint_observed"] is False


def test_post_observation_does_not_depend_on_parent_backend(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "quota-cache.json"
    _write_cache(cache, utilization=95.0, resets_at=datetime.now(UTC) + timedelta(minutes=5))
    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "codex")
    monkeypatch.setenv("AUTOSKILLIT_PROVIDER_PROFILE", "openai")

    with patch("autoskillit.hooks.quota_post_hook.write_quota_log_event") as write_event:
        output = _run_hook(cache)

    assert output == ""
    assert write_event.call_args.args[0]["constraint_observed"] is True


def test_malformed_event_is_silent(tmp_path) -> None:
    from autoskillit.hooks.quota_post_hook import main

    with patch("sys.stdin", io.StringIO("not json")), pytest.raises(SystemExit) as exit_info:
        main(cache_path_override=str(tmp_path / "quota-cache.json"))

    assert exit_info.value.code == 0
