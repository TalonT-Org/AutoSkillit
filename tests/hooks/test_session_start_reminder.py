"""Source-driven resume reminders and unconditional marker cleanup."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests._hook_protocol_oracle import (
    STATUS_COMPLETED,
    claude_verdict,
    codex_verdict,
    run_hook,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

SCRIPT = Path(__file__).resolve().parents[2] / "src/autoskillit/hooks/session_start_hook.py"
_NO_SOURCE = object()
_SESSION_START_CASES = [
    (source, marker_kind, False)
    for source in ("startup", "clear", "compact", _NO_SOURCE)
    for marker_kind in ("fresh_recipe", "none")
]
_SESSION_START_CASES.extend(
    (source, marker_kind, marker_kind == "fresh_recipe")
    for source in ("resume", "fork")
    for marker_kind in ("fresh_recipe", "none", "no_recipe")
)
_SESSION_START_SOURCES = ["startup", "clear", "compact", "resume", "fork", _NO_SOURCE]


def _payload(source: object, transcript_path: Path, *, cwd: Path | None = None) -> dict:
    payload = {
        "hook_event_name": "SessionStart",
        "session_id": "session-1",
        "transcript_path": str(transcript_path),
    }
    if source is not _NO_SOURCE:
        payload["source"] = source
    if cwd is not None:
        payload["cwd"] = str(cwd)
    return payload


def _write_marker(state_dir: Path, *, recipe_name: str | None, opened_at: datetime) -> Path:
    marker_dir = state_dir / "kitchen_state"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / "session-1.json"
    marker.write_text(
        json.dumps(
            {
                "opened_at": opened_at.isoformat(),
                "recipe_name": recipe_name,
                "session_id": "session-1",
                "marker_version": 1,
            }
        ),
        encoding="utf-8",
    )
    return marker


@pytest.mark.parametrize(
    ("source", "marker_kind", "should_remind"),
    _SESSION_START_CASES,
    ids=[
        f"{source if source is not _NO_SOURCE else 'missing-source'}-{marker}"
        for source, marker, _ in _SESSION_START_CASES
    ],
)
def test_session_start_source_and_marker_matrix(
    tmp_path: Path, source: object, marker_kind: str, should_remind: bool
) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('{"type":"say","text":"hello"}\n', encoding="utf-8")
    state_dir = tmp_path / "state"
    if marker_kind != "none":
        recipe_name = "recipe-x" if marker_kind == "fresh_recipe" else None
        _write_marker(state_dir, recipe_name=recipe_name, opened_at=datetime.now(UTC))
    payload = _payload(source, transcript)

    emission = run_hook(
        SCRIPT,
        payload,
        env={"AUTOSKILLIT_STATE_DIR": str(state_dir)},
        unset=("AUTOSKILLIT_HEADLESS",),
    )

    assert emission.exit_code == 0
    if not should_remind:
        assert emission.stdout == ""
        return

    output = json.loads(emission.stdout)
    assert set(output) == {"hookSpecificOutput"}
    specific = output["hookSpecificOutput"]
    assert set(specific) == {"hookEventName", "additionalContext"}
    assert specific["hookEventName"] == "SessionStart"
    context = specific["additionalContext"]
    assert "open_kitchen(name='recipe-x')" in context
    assert "/autoskillit:open-kitchen" not in context
    assert "not automatically restored" not in context
    assert "RESUME REMINDER" not in context

    for verdict_fn in (codex_verdict, claude_verdict):
        verdict = verdict_fn(
            payload,
            exit_code=emission.exit_code,
            stdout=emission.stdout,
            stderr=emission.stderr,
        )
        assert verdict.status == STATUS_COMPLETED
        assert len(verdict.contexts) == 1
        assert verdict.contexts[0] == context


@pytest.mark.parametrize(
    "source",
    _SESSION_START_SOURCES,
    ids=[
        str(source) if source is not _NO_SOURCE else "missing-source"
        for source in _SESSION_START_SOURCES
    ],
)
def test_session_start_sweeps_expired_markers_for_every_source(
    tmp_path: Path, source: object
) -> None:
    state_dir = tmp_path / "state"
    marker_dir = state_dir / "kitchen_state"
    marker_dir.mkdir(parents=True)
    stale = marker_dir / "expired.json"
    stale.write_text(
        json.dumps(
            {
                "opened_at": (datetime.now(UTC) - timedelta(hours=25)).isoformat(),
                "recipe_name": "expired-recipe",
                "session_id": "expired",
                "marker_version": 1,
            }
        ),
        encoding="utf-8",
    )
    fresh = _write_marker(state_dir, recipe_name="current-recipe", opened_at=datetime.now(UTC))
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('{"type":"say","text":"hello"}\n', encoding="utf-8")
    payload = _payload(source, transcript)

    emission = run_hook(
        SCRIPT,
        payload,
        env={"AUTOSKILLIT_STATE_DIR": str(state_dir)},
        unset=("AUTOSKILLIT_HEADLESS",),
    )

    assert emission.exit_code == 0
    assert not stale.exists(), "expired marker should be removed on every SessionStart source"
    assert fresh.exists(), "fresh marker should survive the TTL sweep"


def test_session_start_resolves_campaign_marker_namespace(tmp_path: Path) -> None:
    state_dir = tmp_path / ".autoskillit" / "temp" / "kitchen_state" / "camp-88"
    state_dir.mkdir(parents=True)
    (state_dir / "session-1.json").write_text(
        json.dumps(
            {
                "opened_at": datetime.now(UTC).isoformat(),
                "recipe_name": "campaign-recipe",
                "session_id": "session-1",
                "marker_version": 1,
            }
        ),
        encoding="utf-8",
    )
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("existing transcript\n", encoding="utf-8")
    payload = _payload("resume", transcript, cwd=tmp_path)

    emission = run_hook(
        SCRIPT,
        payload,
        env={"AUTOSKILLIT_CAMPAIGN_ID": "camp-88"},
        unset=("AUTOSKILLIT_HEADLESS", "AUTOSKILLIT_STATE_DIR"),
    )

    assert emission.exit_code == 0
    specific = json.loads(emission.stdout)["hookSpecificOutput"]
    assert "open_kitchen(name='campaign-recipe')" in specific["additionalContext"]
