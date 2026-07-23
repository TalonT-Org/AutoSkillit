"""Session-scope enforcement tests for session_start_hook.py.

These tests satisfy the structural contract in test_session_scope_enforcement.py
which requires test_<script_stem>.py to exist and exercise both headless and
non-headless code paths for any hook with session_scope != 'any'.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

SCRIPT = Path(__file__).resolve().parents[2] / "src/autoskillit/hooks/session_start_hook.py"


def _run(
    stdin_data: str,
    env: dict | None = None,
    *,
    cwd: Path | None = None,
) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )
    return result.returncode, result.stdout


def test_session_start_hook_silent_for_headless_sessions(tmp_path: Path) -> None:
    """Headless sessions must not receive the open-kitchen reminder."""
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"type":"say","text":"hello"}\n')
    payload = json.dumps({"session_id": "abc", "transcript_path": str(transcript)})
    env = {
        **os.environ,
        "AUTOSKILLIT_HEADLESS": "1",
        "AUTOSKILLIT_STATE_DIR": str(tmp_path / "state"),
    }
    rc, out = _run(payload, env=env)
    assert rc == 0
    assert "additionalContext" not in out


def test_session_start_hook_fires_for_interactive_sessions(tmp_path: Path) -> None:
    """Non-headless resumed sessions must still receive the open-kitchen reminder."""
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"type":"say","text":"hello"}\n')
    payload = json.dumps({"session_id": "abc", "transcript_path": str(transcript)})
    env = {k: v for k, v in os.environ.items() if k != "AUTOSKILLIT_HEADLESS"}
    env["AUTOSKILLIT_STATE_DIR"] = str(tmp_path / "empty_state")
    rc, out = _run(payload, env=env)
    assert rc == 0
    assert out.strip(), "hook produced no output for interactive session"
    data = json.loads(out.strip())
    assert "additionalContext" in data
    assert "open-kitchen" in data["additionalContext"]


def test_session_start_hook_registry_scope() -> None:
    """session_start_hook must be registered with session_scope=interactive_only."""
    from autoskillit.hook_registry import HOOK_REGISTRY

    hook_def = next(
        h
        for h in HOOK_REGISTRY
        if h.event_type == "SessionStart" and "session_start_hook.py" in h.scripts
    )
    assert hook_def.session_scope == "interactive_only"


def test_session_start_cleanup_does_not_follow_symlinked_capture_root(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    temp_dir = project / ".autoskillit" / "temp"
    temp_dir.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    stale = external / "shell_0123456789abcdef.log"
    stale.write_bytes(b"must-survive")
    os.utime(stale, (0, 0))
    (temp_dir / "shell_capture").symlink_to(external, target_is_directory=True)
    transcript = tmp_path / "empty-transcript.jsonl"
    transcript.write_text("")
    payload = json.dumps({"session_id": "abc", "transcript_path": str(transcript)})
    env = {k: v for k, v in os.environ.items() if k != "AUTOSKILLIT_HEADLESS"}
    env["AUTOSKILLIT_STATE_DIR"] = str(tmp_path / "empty-state")

    rc, _ = _run(payload, env=env, cwd=project)

    assert rc == 0
    assert stale.read_bytes() == b"must-survive"


def test_session_start_cleanup_failure_keeps_resume_reminder(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    temp_dir = project / ".autoskillit" / "temp"
    temp_dir.mkdir(parents=True)
    (temp_dir / "shell_capture").write_text("blocking regular file")
    transcript = tmp_path / "resumed.jsonl"
    transcript.write_text('{"type":"say","text":"hello"}\n')
    payload = json.dumps({"session_id": "abc", "transcript_path": str(transcript)})
    env = {k: v for k, v in os.environ.items() if k != "AUTOSKILLIT_HEADLESS"}
    env["AUTOSKILLIT_STATE_DIR"] = str(tmp_path / "empty-state")

    rc, out = _run(payload, env=env, cwd=project)

    assert rc == 0
    assert "additionalContext" in json.loads(out)
