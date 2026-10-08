"""CLI checks for rendering the report index as a self-contained deck."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autoskillit.core import ArtifactLease
from tests.cli._sessions_helpers import _configure_log_root, _seed_session, _seed_turn_ledger

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def test_sessions_deck_passes_all_fact_collections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.execution as execution
    import autoskillit.report as report
    from autoskillit.cli.ops import _sessions

    log_root = tmp_path / "logs"
    _configure_log_root(monkeypatch, log_root)
    facts = SimpleNamespace(
        sessions={"parent": {"key": "parent"}},
        requests={"request": {"key": "request"}},
        tools={"tool": {"key": "tool"}},
        subagents={"child": {"key": "child", "actor_level": "L0"}},
        turns={"turn": {"key": "turn", "session_key": "parent"}},
    )
    received: dict[str, Any] = {}

    def capture_deck(session_rows: Any, **kwargs: Any) -> str:
        received["session_rows"] = list(session_rows)
        received.update(kwargs)
        return "<!doctype html>"

    monkeypatch.setattr(_sessions, "_refresh_report_index", lambda *a, **kw: None)
    monkeypatch.setattr(execution, "read_report_index", lambda *a: facts)
    monkeypatch.setattr(report, "render_deck", capture_deck)
    out = tmp_path / "deck.html"

    _sessions.sessions_deck(str(out))

    assert received["session_rows"] == list(facts.sessions.values())
    for name in ("request", "tool", "subagent", "turn"):
        assert list(received[f"{name}_rows"]) == list(getattr(facts, f"{name}s").values())
    assert out.read_text(encoding="utf-8") == "<!doctype html>"


def test_sessions_deck_refreshes_index_and_writes_session_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autoskillit.cli.ops._sessions import sessions_deck

    log_root = tmp_path / "logs"
    _seed_session(
        log_root,
        success=False,
        skill_command="/autoskillit:implement",
        session_type="skill",
        recipe_name="implementation",
        step_name="build",
    )
    _seed_turn_ledger(log_root)
    _configure_log_root(monkeypatch, log_root)
    out = tmp_path / "deck.html"

    sessions_deck(str(out))

    assert capsys.readouterr().out == (
        f"report index: walked 2 items, wrote 2 rows\ndeck: wrote {out} (1 session rows)\n"
    )
    html = out.read_text(encoding="utf-8")
    match = re.search(r'<script[^>]*id="deck-data"[^>]*>(.*?)</script>', html, re.DOTALL)
    assert match is not None
    data = json.loads(match.group(1))
    sessions = data["tables"]["sessions"]
    assert len(sessions["rows"]) == 1
    assert sessions["rows"][0][sessions["columns"].index("harness")] == "claude-code"
    turns = data["tables"]["turns"]
    assert len(turns["rows"]) == 1
    turn = dict(zip(turns["columns"], turns["rows"][0], strict=True))
    assert turn["session_key"] == "session-1"
    assert turn["model"] == "claude-opus-4-1-20250805"
    assert turn["output_tokens"] == {"state": "measured_zero", "value": 0}
    assert turn["cache_write_tokens"] == {"state": "unavailable", "value": None}
    assert all(
        view["status"] == "built"
        for view in data["views"]
        if view["id"] in {"context", "errors", "trend", "gaps", "parity"}
    )


def test_sessions_deck_reports_lease_contention_without_writing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autoskillit.cli.ops._sessions import sessions_deck

    log_root = tmp_path / "logs"
    _seed_session(log_root)
    _configure_log_root(monkeypatch, log_root)
    index_dir = log_root / "report-index"
    index_dir.mkdir()
    out = tmp_path / "deck.html"

    with ArtifactLease.acquire_exclusive(index_dir / "index.lock", timeout=0.0):
        with pytest.raises(SystemExit) as raised:
            sessions_deck(str(out))

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.err.strip() == (
        f"report index: another operation holds a required index or source lease: "
        f"{index_dir / 'index.lock'}"
    )
    assert not out.exists()
