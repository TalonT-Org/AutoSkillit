"""Session-scope and side-effect tests for session_start_hook.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._hook_protocol_oracle import run_hook

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

SCRIPT = Path(__file__).resolve().parents[2] / "src/autoskillit/hooks/session_start_hook.py"


def test_session_start_hook_is_silent_for_headless_sessions(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"type":"say","text":"hello"}\n')
    payload = {
        "hook_event_name": "SessionStart",
        "source": "startup",
        "session_id": "abc",
        "transcript_path": str(transcript),
    }

    emission = run_hook(
        SCRIPT,
        payload,
        env={
            "AUTOSKILLIT_HEADLESS": "1",
            "AUTOSKILLIT_STATE_DIR": str(tmp_path / "state"),
        },
    )

    assert emission.exit_code == 0
    assert emission.stdout == ""


def test_session_start_hook_never_mutates_pipeline_trackers(tmp_path: Path) -> None:
    """Malformed and aged tracker authority survives SessionStart unchanged."""
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"type":"say","text":"hello"}\n')
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    tracker_dir.mkdir(parents=True)
    malformed = tracker_dir / "malformed.json"
    malformed.write_bytes(b"{not-json")
    aged = tracker_dir / "aged.json"
    aged.write_text(
        json.dumps(
            {
                "initialized_at": "2020-01-01T00:00:00+00:00",
                "steps": {},
                "dependencies": {},
            }
        )
    )
    before = {path.name: path.read_bytes() for path in tracker_dir.iterdir()}
    payload = {
        "hook_event_name": "SessionStart",
        "source": "startup",
        "session_id": "abc",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
    }

    emission = run_hook(
        SCRIPT,
        payload,
        env={"AUTOSKILLIT_STATE_DIR": str(tmp_path / "state")},
        unset=("AUTOSKILLIT_HEADLESS",),
    )

    assert emission.exit_code == 0
    assert {path.name: path.read_bytes() for path in tracker_dir.iterdir()} == before


def test_session_start_hook_registry_scope() -> None:
    """session_start_hook must be registered with session_scope=interactive_only."""
    from autoskillit.hook_registry import HOOK_REGISTRY

    hook_def = next(
        h
        for h in HOOK_REGISTRY
        if h.event_type == "SessionStart" and "session_start_hook.py" in h.scripts
    )
    assert hook_def.session_scope == "interactive_only"
