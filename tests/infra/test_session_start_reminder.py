"""Tests for the SessionStart hook — session_start_reminder.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "src/autoskillit/hooks/session_start_reminder.py"


def _run(stdin_data: str) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin_data,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout


# REQ-HOOK-002, REQ-HOOK-003
def test_fresh_session_no_output(tmp_path: Path) -> None:
    """Empty transcript_path → no additionalContext injected."""
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("")
    payload = json.dumps({"session_id": "abc", "transcript_path": str(transcript)})
    rc, out = _run(payload)
    assert rc == 0
    assert "additionalContext" not in out


def test_resumed_session_injects_context(tmp_path: Path) -> None:
    """Non-empty transcript → additionalContext with open-kitchen reminder."""
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"type":"say","text":"hello"}\n')
    payload = json.dumps({"session_id": "abc", "transcript_path": str(transcript)})
    rc, out = _run(payload)
    assert rc == 0
    data = json.loads(out.strip())
    assert "additionalContext" in data
    assert "open-kitchen" in data["additionalContext"]


def test_missing_transcript_path_no_crash(tmp_path: Path) -> None:
    """Missing transcript_path key → fail-open, no output, exit 0."""
    payload = json.dumps({"session_id": "abc"})
    rc, out = _run(payload)
    assert rc == 0


def test_nonexistent_transcript_no_crash(tmp_path: Path) -> None:
    """transcript_path pointing to non-existent file → fail-open, no output."""
    payload = json.dumps({"session_id": "abc", "transcript_path": "/nonexistent/path.jsonl"})
    rc, out = _run(payload)
    assert rc == 0
    assert "additionalContext" not in out
