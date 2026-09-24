"""Behavioral tests for the interactive session lifetime notice hook."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

SCRIPT = (
    Path(__file__).resolve().parents[2] / "src/autoskillit/hooks/session_lifetime_notice_hook.py"
)
NOTICE_ENV = "AUTOSKILLIT_SESSION_LIFETIME_NOTICE"


def _run(
    event: object, *, env: dict[str, str] | None = None, headless: bool = False
) -> tuple[int, str]:
    run_env = production_interpreter_env()
    run_env.pop("AUTOSKILLIT_HEADLESS", None)
    run_env.pop(NOTICE_ENV, None)
    run_env.update(env or {})
    if headless:
        run_env["AUTOSKILLIT_HEADLESS"] = "1"
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=run_env,
        check=False,
    )
    return result.returncode, result.stdout


def test_headless_session_leaves_interactive_notice_untouched(tmp_path: Path) -> None:
    notice_path = tmp_path / "notice.json"
    notice_path.write_text(
        json.dumps({"message": "This cook session is nearing its soft lifetime."}),
        encoding="utf-8",
    )

    code, output = _run(
        {"hook_event_name": "PostToolUse", "tool_name": "Bash"},
        env={NOTICE_ENV: str(notice_path)},
        headless=True,
    )

    assert code == 0
    assert output == ""
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

    code, output = _run(event, env=env, headless=False)

    assert code == 0
    result = json.loads(output)
    assert "systemMessage" in result
    assert "hookSpecificOutput" in result
    assert "additionalContext" in result["hookSpecificOutput"]
    assert not notice_path.exists()

    second_code, second_output = _run(event, env=env)

    assert second_code == 0
    assert second_output == ""


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

    code, output = _run(
        {"hook_event_name": "Stop"},
        env={NOTICE_ENV: str(notice_path)},
    )

    assert code == 0
    result = json.loads(output)
    assert "systemMessage" in result
    assert "hookSpecificOutput" not in result


@pytest.mark.parametrize("case", ["unset", "missing", "malformed"])
def test_hook_is_silent_without_a_valid_notice(tmp_path: Path, case: str) -> None:
    env: dict[str, str] = {}
    notice_path = tmp_path / "notice.json"
    if case == "missing":
        env[NOTICE_ENV] = str(notice_path)
    elif case == "malformed":
        notice_path.write_text("{not-json", encoding="utf-8")
        env[NOTICE_ENV] = str(notice_path)

    code, output = _run({"hook_event_name": "PostToolUse", "tool_name": "Bash"}, env=env)

    assert code == 0
    assert output == ""
