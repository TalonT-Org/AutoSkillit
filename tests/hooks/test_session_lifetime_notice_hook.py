"""Behavioral tests for the interactive session lifetime notice hook."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._hook_protocol_oracle import (
    STATUS_COMPLETED,
    assert_both_protocols_context,
    claude_verdict,
    run_hook,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

NOTICE_ENV = "AUTOSKILLIT_SESSION_LIFETIME_NOTICE"


def _run(
    tmp_path: Path,
    event: dict,
    *,
    env: dict[str, str] | None = None,
    headless: bool = False,
):
    run_env = {"AUTOSKILLIT_LOG_DIR": str(tmp_path / "logs"), **(env or {})}
    if headless:
        run_env["AUTOSKILLIT_HEADLESS"] = "1"
    unset = []
    if not headless:
        unset.append("AUTOSKILLIT_HEADLESS")
    if NOTICE_ENV not in run_env:
        unset.append(NOTICE_ENV)
    return run_hook(
        "session_lifetime_notice_hook.py",
        event,
        env=run_env,
        unset=unset,
        cwd=tmp_path,
    )


def test_headless_session_leaves_interactive_notice_untouched(tmp_path: Path) -> None:
    notice_path = tmp_path / "notice.json"
    notice_path.write_text(
        json.dumps({"message": "This cook session is nearing its soft lifetime."}),
        encoding="utf-8",
    )

    emission = _run(
        tmp_path,
        {"hook_event_name": "PostToolUse", "tool_name": "Bash"},
        env={NOTICE_ENV: str(notice_path)},
        headless=True,
    )

    assert emission.exit_code == 0
    assert emission.stdout == ""
    assert notice_path.exists()


def test_post_tool_use_delivers_and_consumes_notice_once(tmp_path: Path) -> None:
    notice_path = tmp_path / "notice.json"
    notice_path.write_text(
        json.dumps(
            {
                "level": "warning",
                "message": "This cook session is nearing its soft lifetime.",
                "deadline_epoch": 1_800_000_000,
            }
        ),
        encoding="utf-8",
    )
    event = {"hook_event_name": "PostToolUse", "tool_name": "Bash"}
    env = {NOTICE_ENV: str(notice_path)}

    emission = _run(tmp_path, event, env=env, headless=False)

    assert emission.exit_code == 0
    result = json.loads(emission.stdout)
    assert "systemMessage" in result
    assert "hookSpecificOutput" in result
    assert "additionalContext" in result["hookSpecificOutput"]
    assert_both_protocols_context(
        event["hook_event_name"],
        exit_code=emission.exit_code,
        stdout=emission.stdout,
        stderr=emission.stderr,
    )
    assert not notice_path.exists()

    second = _run(tmp_path, event, env=env)

    assert second.exit_code == 0
    assert second.stdout == ""


def test_stop_delivers_only_system_message(tmp_path: Path) -> None:
    notice_path = tmp_path / "notice.json"
    notice_path.write_text(
        json.dumps(
            {
                "level": "warning",
                "message": "This cook session is nearing its soft lifetime.",
                "deadline_epoch": 1_800_000_000,
            }
        ),
        encoding="utf-8",
    )

    event = {"hook_event_name": "Stop"}
    emission = _run(
        tmp_path,
        event,
        env={NOTICE_ENV: str(notice_path)},
    )

    assert emission.exit_code == 0
    result = json.loads(emission.stdout)
    assert "systemMessage" in result
    assert "hookSpecificOutput" not in result
    verdict = claude_verdict(
        event["hook_event_name"],
        exit_code=emission.exit_code,
        stdout=emission.stdout,
        stderr=emission.stderr,
    )
    assert verdict.status == STATUS_COMPLETED
    assert verdict.system_message


@pytest.mark.parametrize("case", ["unset", "missing", "malformed"])
def test_hook_is_silent_without_a_valid_notice(tmp_path: Path, case: str) -> None:
    env: dict[str, str] = {}
    notice_path = tmp_path / "notice.json"
    if case == "missing":
        env[NOTICE_ENV] = str(notice_path)
    elif case == "malformed":
        notice_path.write_text("{not-json", encoding="utf-8")
        env[NOTICE_ENV] = str(notice_path)

    emission = _run(
        tmp_path,
        {"hook_event_name": "PostToolUse", "tool_name": "Bash"},
        env=env,
    )

    assert emission.exit_code == 0
    assert emission.stdout == ""
