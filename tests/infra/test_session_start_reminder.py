"""Tests for the SessionStart hook — session_start_hook.py."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "src/autoskillit/hooks/session_start_hook.py"


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


def test_session_start_sweeps_stale_markers(tmp_path: Path) -> None:
    import json
    from datetime import datetime

    marker_dir = tmp_path / "kitchen_state"
    marker_dir.mkdir(parents=True)
    stale = marker_dir / "old-session.json"
    stale.write_text(
        json.dumps(
            {
                "session_id": "old-session",
                "opened_at": "2020-01-01T00:00:00+00:00",
                "recipe_name": None,
                "marker_version": 1,
            }
        )
    )
    fresh = marker_dir / "new-session.json"
    fresh.write_text(
        json.dumps(
            {
                "session_id": "new-session",
                "opened_at": datetime.now(UTC).isoformat(),
                "recipe_name": None,
                "marker_version": 1,
            }
        )
    )
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("")
    payload = json.dumps(
        {
            "session_id": "new-session",
            "transcript_path": str(transcript),
            "autoskillit_state_dir": str(tmp_path),
        }
    )
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "AUTOSKILLIT_STATE_DIR": str(tmp_path)},
    )
    assert result.returncode == 0
    assert not stale.exists(), "Stale marker should have been swept"
    assert fresh.exists(), "Fresh marker must survive"
