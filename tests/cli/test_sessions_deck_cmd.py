"""CLI checks for rendering the report index as a self-contained deck."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from autoskillit.core import ArtifactLease
from tests.cli._sessions_helpers import _configure_log_root, _seed_session

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def test_sessions_deck_refreshes_index_and_writes_session_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autoskillit.cli.ops._sessions import sessions_deck

    log_root = tmp_path / "logs"
    _seed_session(log_root)
    _configure_log_root(monkeypatch, log_root)
    out = tmp_path / "deck.html"

    sessions_deck(str(out))

    assert capsys.readouterr().out == (
        f"report index: walked 2 items, wrote 1 rows\ndeck: wrote {out} (1 session rows)\n"
    )
    html = out.read_text(encoding="utf-8")
    match = re.search(r'<script[^>]*id="deck-data"[^>]*>(.*?)</script>', html, re.DOTALL)
    assert match is not None
    data = json.loads(match.group(1))
    sessions = data["tables"]["sessions"]
    assert len(sessions["rows"]) == 1
    assert sessions["rows"][0][sessions["columns"].index("harness")] == "claude-code"


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
