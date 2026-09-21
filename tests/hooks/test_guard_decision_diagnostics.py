"""Guard decision diagnostics remain bounded and do not persist payloads."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.hooks._runtime._guard_decision_diagnostics import _MAX_FILE_BYTES
from autoskillit.hooks.guards import write_guard

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def _run_write_guard(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    event: dict[str, object],
    root: Path,
) -> dict[str, object]:
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", str(root / "allowed"))
    with patch("sys.stdin.read", return_value=json.dumps(event)):
        with pytest.raises(SystemExit) as exit_code:
            write_guard.main()
    assert exit_code.value.code == 0
    rendered = capsys.readouterr().out
    return json.loads(rendered) if rendered else {}


def test_write_guard_records_allowed_and_denied_decisions_without_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sentinel_env = "environment-value-must-not-persist"
    sentinel_command = "command-payload-must-not-persist"
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("SENTINEL_ENV", sentinel_env)
    allowed = tmp_path / "allowed" / "ok.py"
    denied = tmp_path / "outside.py"

    _run_write_guard(
        monkeypatch,
        capsys,
        event={
            "session_id": "session-1",
            "tool_name": "Write",
            "tool_input": {"file_path": str(allowed)},
        },
        root=tmp_path,
    )
    denied_output = _run_write_guard(
        monkeypatch,
        capsys,
        event={
            "session_id": "session-1",
            "tool_name": "Bash",
            "tool_input": {"command": f"printf {sentinel_command} > {denied}"},
        },
        root=tmp_path,
    )

    assert denied_output["hookSpecificOutput"]["permissionDecision"] == "deny"
    diagnostic_path = tmp_path / ".autoskillit" / "temp" / "guard_decisions.jsonl"
    contents = diagnostic_path.read_text()
    records = [json.loads(line) for line in contents.splitlines()]
    assert [record["decision"] for record in records] == ["allow", "deny"]
    assert all(record["scope"] == "write_prefix" for record in records)
    assert sentinel_env not in contents
    assert sentinel_command not in contents


def test_guard_decision_retention_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.hooks._runtime import _guard_decision_diagnostics as diagnostics

    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(tmp_path))
    path = tmp_path / ".autoskillit" / "temp" / "guard_decisions.jsonl"
    data = {"cwd": str(tmp_path), "session_id": "session"}
    for _ in range(1005):
        diagnostics.record_guard_decision(
            data,
            guard="write_guard",
            activation_source="headless",
            scope="write_prefix",
            decision="allow",
            reason="in_scope",
        )

    assert path.stat().st_size <= _MAX_FILE_BYTES
    assert len(path.read_text().splitlines()) == 1000


def test_skill_load_post_hook_records_binding_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.hooks import skill_load_post_hook

    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        skill_load_post_hook,
        "_write_skill_binding",
        lambda *args, **kwargs: (
            skill_load_post_hook.unresolved_loaded_skill("x", "ts", "failure"),
            None,
            False,
        ),
    )
    event = {
        "session_id": "session-2",
        "hook_event_name": "PostToolUse",
        "tool_name": "Skill",
        "tool_input": {"skill": "autoskillit:test"},
        "cwd": str(tmp_path),
    }
    with patch("sys.stdin.read", return_value=json.dumps(event)):
        with pytest.raises(SystemExit):
            skill_load_post_hook.main()

    records = [
        json.loads(line)
        for line in (tmp_path / ".autoskillit" / "temp" / "guard_decisions.jsonl")
        .read_text()
        .splitlines()
    ]
    assert records[-1]["guard"] == "skill_load_post_hook"
    assert records[-1]["reason"] == "binding_write_failed"
