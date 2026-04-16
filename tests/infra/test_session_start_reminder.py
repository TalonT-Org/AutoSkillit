"""Tests for the SessionStart hook — session_start_hook.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "src/autoskillit/hooks/session_start_hook.py"


def _run(stdin_data: str, env: dict | None = None) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout


def _write_marker(
    marker_dir: Path, session_id: str, recipe_name: object, *, fresh: bool = True
) -> None:
    opened_at = datetime.now(UTC) if fresh else datetime.now(UTC) - timedelta(hours=25)
    (marker_dir / f"{session_id}.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "opened_at": opened_at.isoformat(),
                "recipe_name": recipe_name,
                "marker_version": 1,
            }
        )
    )


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


def test_resumed_session_includes_recipe_name_from_fresh_marker(tmp_path: Path) -> None:
    """Fresh marker with recipe_name → name appears in additionalContext."""
    marker_dir = tmp_path / "state" / "kitchen_state"
    marker_dir.mkdir(parents=True)
    _write_marker(marker_dir, "sess-1", "my-recipe", fresh=True)
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('{"type":"say","text":"hello"}\n')
    payload = json.dumps({"session_id": "sess-1", "transcript_path": str(transcript)})
    env = {**os.environ, "AUTOSKILLIT_STATE_DIR": str(tmp_path / "state")}
    rc, out = _run(payload, env=env)
    assert rc == 0
    data = json.loads(out.strip())
    assert "my-recipe" in data["additionalContext"]


def test_resumed_session_no_recipe_name_when_marker_has_none(tmp_path: Path) -> None:
    """Fresh marker with recipe_name=None → generic reminder, no recipe name."""
    marker_dir = tmp_path / "state" / "kitchen_state"
    marker_dir.mkdir(parents=True)
    _write_marker(marker_dir, "sess-2", None, fresh=True)
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('{"type":"say","text":"hello"}\n')
    payload = json.dumps({"session_id": "sess-2", "transcript_path": str(transcript)})
    env = {**os.environ, "AUTOSKILLIT_STATE_DIR": str(tmp_path / "state")}
    rc, out = _run(payload, env=env)
    assert rc == 0
    data = json.loads(out.strip())
    assert "recipe" not in data["additionalContext"]


def test_resumed_session_no_recipe_name_when_no_markers_exist(tmp_path: Path) -> None:
    """No marker files → additionalContext present but no recipe name."""
    (tmp_path / "state" / "kitchen_state").mkdir(parents=True)
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('{"type":"say","text":"hello"}\n')
    payload = json.dumps({"session_id": "sess-3", "transcript_path": str(transcript)})
    env = {**os.environ, "AUTOSKILLIT_STATE_DIR": str(tmp_path / "state")}
    rc, out = _run(payload, env=env)
    assert rc == 0
    data = json.loads(out.strip())
    assert "additionalContext" in data
    assert "recipe" not in data["additionalContext"]


def test_resumed_session_picks_most_recent_fresh_marker(tmp_path: Path) -> None:
    """Two fresh markers → most recent recipe_name wins."""
    marker_dir = tmp_path / "state" / "kitchen_state"
    marker_dir.mkdir(parents=True)
    # Write older marker first
    older_at = datetime.now(UTC) - timedelta(seconds=10)
    (marker_dir / "old-sess.json").write_text(
        json.dumps(
            {
                "session_id": "old-sess",
                "opened_at": older_at.isoformat(),
                "recipe_name": "old-recipe",
                "marker_version": 1,
            }
        )
    )
    # Write newer marker
    newer_at = datetime.now(UTC)
    (marker_dir / "new-sess.json").write_text(
        json.dumps(
            {
                "session_id": "new-sess",
                "opened_at": newer_at.isoformat(),
                "recipe_name": "new-recipe",
                "marker_version": 1,
            }
        )
    )
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('{"type":"say","text":"hello"}\n')
    payload = json.dumps({"session_id": "new-sess", "transcript_path": str(transcript)})
    env = {**os.environ, "AUTOSKILLIT_STATE_DIR": str(tmp_path / "state")}
    rc, out = _run(payload, env=env)
    assert rc == 0
    data = json.loads(out.strip())
    assert "new-recipe" in data["additionalContext"]
    assert "old-recipe" not in data["additionalContext"]


def test_resumed_session_ignores_stale_marker_recipe_name(tmp_path: Path) -> None:
    """Stale marker recipe_name must not appear in additionalContext."""
    marker_dir = tmp_path / "state" / "kitchen_state"
    marker_dir.mkdir(parents=True)
    _write_marker(marker_dir, "stale-sess", "stale-recipe", fresh=False)
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('{"type":"say","text":"hello"}\n')
    payload = json.dumps({"session_id": "stale-sess", "transcript_path": str(transcript)})
    env = {**os.environ, "AUTOSKILLIT_STATE_DIR": str(tmp_path / "state")}
    rc, out = _run(payload, env=env)
    assert rc == 0
    data = json.loads(out.strip())
    assert "stale-recipe" not in data["additionalContext"]


def test_fresh_session_not_affected_by_markers(tmp_path: Path) -> None:
    """Fresh session (empty transcript) stays silent even with a marker present."""
    marker_dir = tmp_path / "state" / "kitchen_state"
    marker_dir.mkdir(parents=True)
    _write_marker(marker_dir, "sess-6", "some-recipe", fresh=True)
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("")
    payload = json.dumps({"session_id": "sess-6", "transcript_path": str(transcript)})
    env = {**os.environ, "AUTOSKILLIT_STATE_DIR": str(tmp_path / "state")}
    rc, out = _run(payload, env=env)
    assert rc == 0
    assert "additionalContext" not in out


def test_resumed_session_marker_dir_missing_no_crash(tmp_path: Path) -> None:
    """Missing AUTOSKILLIT_STATE_DIR path → exit 0, generic reminder, no crash."""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('{"type":"say","text":"hello"}\n')
    payload = json.dumps({"session_id": "sess-7", "transcript_path": str(transcript)})
    env = {**os.environ, "AUTOSKILLIT_STATE_DIR": str(tmp_path / "nonexistent")}
    rc, out = _run(payload, env=env)
    assert rc == 0
    data = json.loads(out.strip())
    assert "additionalContext" in data
    assert "recipe" not in data["additionalContext"]
