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


def _claude_record(
    text: str | None,
    *,
    request_id: str | None = None,
    message_id: str | None = None,
    content: object | None = None,
    **extra: object,
) -> dict[str, object]:
    message: dict[str, object] = {
        "role": "assistant",
        "content": text if content is None else content,
    }
    if message_id is not None:
        message["id"] = message_id
    record: dict[str, object] = {
        "type": "assistant",
        "sessionId": _SESSION,
        "message": message,
        **extra,
    }
    if request_id is not None:
        record["requestId"] = request_id
    return record


def _codex_record(
    text: str, *, agent_id: str | None = None, session_id: str | None = None
) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }
    if agent_id is not None:
        payload["agent_id"] = agent_id
    record: dict[str, object] = {
        "type": "response_item",
        "payload": payload,
    }
    if session_id is not None:
        record["session_id"] = session_id
    return record


def _claude_tool_record(
    *, request_id: str | None = None, message_id: str | None = None
) -> dict[str, object]:
    return _claude_record(
        None,
        request_id=request_id,
        message_id=message_id,
        content=[
            {
                "type": "tool_use",
                "name": "mcp__autoskillit__complete_run_skill_result",
                "input": {"receipt_id": "fabricated"},
            }
        ],
    )


def _codex_tool_result(payload_type: str) -> dict[str, object]:
    return {
        "type": "response_item",
        "payload": {"type": payload_type, "output": "done"},
    }


def _write_marker(
    directory: Path,
    *,
    session_id: str = _SESSION,
    filename_session: str = _SESSION,
    age: float = 0,
) -> None:
    marker = directory / f"run-skill-in-progress-{filename_session}-token.marker"
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
    headless: bool | None = True,
    hook_session: str = _SESSION,
) -> dict[str, object] | None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("".join(json.dumps(record) + "\n" for record in records))
    if marker_session is not None:
        _write_marker(tmp_path, session_id=marker_session, age=marker_age)
    hook_payload: dict[str, object] = {
        "session_id": hook_session,
        "transcript_path": str(transcript),
        "tool_name": "mcp__autoskillit__run_python",
        "tool_input": {},
    }
    if payload:
        hook_payload.update(payload)
    env = {
        **os.environ,
        "AUTOSKILLIT_SESSION_TYPE": "orchestrator",
        **(env_overrides or {}),
    }
    if headless is None:
        env.pop("AUTOSKILLIT_HEADLESS", None)
    else:
        env["AUTOSKILLIT_HEADLESS"] = "1" if headless else "0"
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


def test_empty_unicode_prefixed_notification_fails_open(tmp_path: Path) -> None:
    result = _run_guard(tmp_path, [_claude_record("İ<bg_result></bg_result>")])

    assert result is None


@pytest.mark.parametrize(
    "text",
    [
        f"Incident wrapper <function_results>{_BG_RESULT}</function_results> after prose.",
        f"Task update: {_TASK_NOTIFICATION} Continue waiting.",
        f"```xml\n{_BG_RESULT}\n```",
    ],
    ids=["function-results-wrapper", "task-update-prose", "xml-code-fence"],
)
def test_denies_complete_embedded_notification(tmp_path: Path, text: str) -> None:
    result = _run_guard(tmp_path, [_claude_record(text)])

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


def test_skips_message_less_system_metadata(tmp_path: Path) -> None:
    records = [
        _claude_record(_BG_RESULT),
        {"type": "system", "subtype": "stop_hook_summary", "summary": "hook stopped"},
    ]

    result = _run_guard(tmp_path, records)

    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "records",
    [
        [_claude_record(_BG_RESULT), _claude_record("child", isSidechain=True)],
        [_claude_record(_BG_RESULT), _claude_record("meta", isMeta=True)],
        [_claude_record(_BG_RESULT), _claude_record("child", agent_id="child")],
        [
            _claude_record(_BG_RESULT),
            _claude_record("foreign", sessionId="different-session"),
        ],
        [_codex_record(_BG_RESULT), _codex_record("child", agent_id="child")],
        [
            _codex_record(_BG_RESULT),
            _codex_record("foreign", session_id="different-session"),
        ],
    ],
    ids=[
        "claude-sidechain",
        "claude-meta",
        "claude-agent",
        "claude-foreign-session",
        "codex-agent",
        "codex-foreign-session",
    ],
)
def test_newer_excluded_assistant_does_not_hide_parent_completion(
    tmp_path: Path, records: list[dict[str, object]]
) -> None:
    result = _run_guard(tmp_path, records)

    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "records",
    [
        [
            _claude_record(_BG_RESULT, request_id="req-1", message_id="msg-1"),
            _claude_tool_record(request_id="req-1", message_id="msg-1"),
        ],
        [
            _claude_record(_BG_RESULT, request_id="req-1"),
            _claude_tool_record(request_id="req-1"),
        ],
        [
            _claude_record(_BG_RESULT, message_id="msg-1"),
            _claude_tool_record(message_id="msg-1"),
        ],
    ],
    ids=("full-pair", "request-only", "message-only"),
)
def test_denies_split_claude_logical_turn(
    tmp_path: Path, records: list[dict[str, object]]
) -> None:
    result = _run_guard(tmp_path, records)

    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "records",
    [
        [
            _claude_record(_BG_RESULT, request_id="same", message_id="msg"),
            _claude_tool_record(request_id="same"),
        ],
        [
            _claude_record(_BG_RESULT, request_id="same"),
            _claude_tool_record(message_id="same"),
        ],
        [
            _claude_record(_BG_RESULT, message_id="same"),
            _claude_tool_record(request_id="same", message_id="msg"),
        ],
    ],
    ids=("full-then-request", "request-then-message", "message-then-full"),
)
def test_mixed_claude_identity_shapes_do_not_coalesce(
    tmp_path: Path, records: list[dict[str, object]]
) -> None:
    assert _run_guard(tmp_path, records) is None


@pytest.mark.parametrize("payload_type", ["function_call_output", "custom_tool_call_output"])
def test_codex_tool_result_is_a_turn_boundary(tmp_path: Path, payload_type: str) -> None:
    records = [_codex_record(_BG_RESULT), _codex_tool_result(payload_type)]

    assert _run_guard(tmp_path, records) is None


def test_codex_in_progress_function_call_preserves_current_turn(tmp_path: Path) -> None:
    records = [
        _codex_record(_BG_RESULT),
        {"type": "response_item", "payload": {"type": "function_call", "name": "run_python"}},
    ]

    result = _run_guard(tmp_path, records)

    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("role", ["user", "system", "tool"])
def test_later_role_message_is_a_turn_boundary(tmp_path: Path, role: str) -> None:
    records = [
        _claude_record(_BG_RESULT),
        {"type": role, "message": {"role": role, "content": "later boundary"}},
    ]

    assert _run_guard(tmp_path, records) is None


def test_simultaneous_markers_require_exact_filename_payload_binding(tmp_path: Path) -> None:
    _write_marker(tmp_path, session_id="session-b", filename_session="session-a")
    _write_marker(tmp_path, session_id="session-b", filename_session="session-b")

    result_a = _run_guard(
        tmp_path,
        [_claude_record(_BG_RESULT, sessionId="session-a")],
        marker_session=None,
        hook_session="session-a",
    )
    result_b = _run_guard(
        tmp_path,
        [_claude_record(_BG_RESULT, sessionId="session-b")],
        marker_session=None,
        hook_session="session-b",
    )

    assert result_a is None
    assert result_b is not None


@pytest.mark.parametrize(
    ("records", "kwargs"),
    [
        ([_claude_record("<bg_result>incomplete")], {}),
        ([_claude_record("<bg_result></bg_result>")], {}),
        ([_claude_record("<bg_result><bg_result>x</bg_result></bg_result>")], {}),
        (
            [_claude_record("<task-notification><status>completed</status></task_notification>")],
            {},
        ),
        (
            [
                _claude_record(
                    "<task-notification><status>running</status></task-notification>"
                    "<status>completed</status>"
                )
            ],
            {},
        ),
        ([_claude_record(_BG_RESULT, isSidechain=True)], {}),
        ([_claude_record(_BG_RESULT, isMeta=True)], {}),
        ([_claude_record(_BG_RESULT, agent_id="child")], {}),
        ([_claude_record(_BG_RESULT, sessionId="different-session")], {}),
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
    assert entry.session_scope == "any"
    assert "fabricated_completion_guard.py" in NEW_SUBDIR_BASENAMES


def test_denies_interactive_orchestrator_without_headless_env(tmp_path: Path) -> None:
    result = _run_guard(tmp_path, [_claude_record(_BG_RESULT)], headless=None)

    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_fails_open_without_reading_non_regular_transcript(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.jsonl"
    os.mkfifo(transcript)
    _write_marker(tmp_path)
    payload = {
        "session_id": _SESSION,
        "transcript_path": str(transcript),
        "tool_name": "mcp__autoskillit__run_python",
        "tool_input": {},
    }
    env = {
        **os.environ,
        "AUTOSKILLIT_HEADLESS": "1",
        "AUTOSKILLIT_SESSION_TYPE": "orchestrator",
    }

    completed = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        env=env,
        timeout=1,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""
