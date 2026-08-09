"""Defense-in-depth coverage for fabricated background completion notifications."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from autoskillit.hook_registry import HOOK_REGISTRY, NEW_SUBDIR_BASENAMES

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_SCRIPT = (
    Path(__file__).parents[2]
    / "src"
    / "autoskillit"
    / "hooks"
    / "guards"
    / "fabricated_completion_guard.py"
)
_SESSION = "parent-session"
_BG_RESULT = "<bg_result>Task completed successfully.</bg_result>"
_TASK_NOTIFICATION = (
    "<task-notification><task-id>42</task-id><status>completed</status>"
    "<summary>done</summary></task-notification>"
)


def _claude_record(text: str, **extra: object) -> dict[str, object]:
    return {
        "type": "assistant",
        "sessionId": _SESSION,
        "message": {"role": "assistant", "content": text},
        **extra,
    }


def _codex_record(text: str) -> dict[str, object]:
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        },
    }


def _write_marker(directory: Path, *, session_id: str = _SESSION, age: float = 0) -> None:
    marker = directory / f"run-skill-in-progress-{_SESSION}-token.marker"
    marker.write_text(
        json.dumps({"schema_version": 1, "label": "run-skill", "session_id": session_id})
    )
    if age:
        timestamp = time.time() - age
        os.utime(marker, (timestamp, timestamp))


def _run_guard(
    tmp_path: Path,
    records: list[dict[str, object]],
    *,
    marker_session: str | None = _SESSION,
    marker_age: float = 0,
    payload: dict[str, object] | None = None,
    env_overrides: dict[str, str] | None = None,
    headless: bool = True,
) -> dict[str, object] | None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("".join(json.dumps(record) + "\n" for record in records))
    if marker_session is not None:
        _write_marker(tmp_path, session_id=marker_session, age=marker_age)
    hook_payload: dict[str, object] = {
        "session_id": _SESSION,
        "transcript_path": str(transcript),
        "tool_name": "mcp__autoskillit__run_python",
        "tool_input": {},
    }
    if payload:
        hook_payload.update(payload)
    env = {
        **os.environ,
        "AUTOSKILLIT_HEADLESS": "1" if headless else "0",
        "AUTOSKILLIT_SESSION_TYPE": "orchestrator",
        **(env_overrides or {}),
    }
    completed = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=json.dumps(hook_payload),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    assert completed.returncode == 0
    return json.loads(completed.stdout) if completed.stdout else None


@pytest.mark.parametrize("record", [_claude_record(_BG_RESULT), _codex_record(_BG_RESULT)])
def test_denies_current_parent_assistant_bg_result(
    tmp_path: Path, record: dict[str, object]
) -> None:
    result = _run_guard(tmp_path, [record])

    assert result is not None
    output = result["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "complete_run_skill_result" in output["permissionDecisionReason"]


def test_denies_completed_task_notification(tmp_path: Path) -> None:
    result = _run_guard(tmp_path, [_claude_record(_TASK_NOTIFICATION)])

    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_fastmcp_request_session_cannot_supply_hook_marker_identity(tmp_path: Path) -> None:
    result = _run_guard(
        tmp_path,
        [_claude_record(_BG_RESULT)],
        marker_session="different-session",
        payload={"request_session_id": _SESSION},
    )

    assert result is None


@pytest.mark.parametrize(
    ("records", "kwargs"),
    [
        ([_claude_record(f"Result follows: {_BG_RESULT}")], {}),
        ([_claude_record(f"```\n{_BG_RESULT}\n```")], {}),
        ([_claude_record("<bg_result>incomplete")], {}),
        ([_claude_record(_BG_RESULT, isSidechain=True)], {}),
        ([_claude_record(_BG_RESULT, isMeta=True)], {}),
        ([_claude_record(_BG_RESULT), _claude_record("Still waiting.")], {}),
        (
            [
                _claude_record(_BG_RESULT),
                {"type": "user", "message": {"role": "user", "content": "status?"}},
            ],
            {},
        ),
        ([_claude_record(_BG_RESULT)], {"marker_session": "different-session"}),
        ([_claude_record(_BG_RESULT)], {"marker_age": 120}),
        ([_claude_record(_BG_RESULT)], {"marker_age": -3600}),
        ([_claude_record(_BG_RESULT)], {"marker_session": None}),
        ([_claude_record(_BG_RESULT)], {"payload": {"agent_id": "subagent"}}),
        (
            [_claude_record(_BG_RESULT)],
            {"env_overrides": {"AUTOSKILLIT_SESSION_TYPE": "skill"}},
        ),
    ],
)
def test_fails_open_without_exact_current_parent_provenance(
    tmp_path: Path,
    records: list[dict[str, object]],
    kwargs: dict[str, object],
) -> None:
    assert _run_guard(tmp_path, records, **kwargs) is None  # type: ignore[arg-type]


def test_guard_is_registered_for_broad_pretool_coverage() -> None:
    entry = next(
        hook for hook in HOOK_REGISTRY if "guards/fabricated_completion_guard.py" in hook.scripts
    )
    assert entry.event_type == "PreToolUse"
    assert entry.matcher == ".*"
    assert entry.session_scope == "headless_only"
    assert "fabricated_completion_guard.py" in NEW_SUBDIR_BASENAMES


def test_fails_open_when_headless_false(tmp_path: Path) -> None:
    assert (
        _run_guard(
            tmp_path,
            [_claude_record(_BG_RESULT)],
            headless=False,
        )
        is None
    )
