"""CLI checks for report-index inspection and refresh."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import ArtifactLease
from autoskillit.execution import REPORT_INDEX_SCHEMA_VERSION
from tests.cli._sessions_helpers import _configure_log_root, _seed_session

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def test_sessions_index_reports_without_creating_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autoskillit.cli.ops._sessions import sessions_index

    log_root = tmp_path / "logs"
    _seed_session(log_root)
    _configure_log_root(monkeypatch, log_root)

    sessions_index()

    out = capsys.readouterr().out
    index_dir = log_root / "report-index"
    assert out == (
        f"report index v{REPORT_INDEX_SCHEMA_VERSION} at {index_dir}: "
        "sessions=0 requests=0 tools=0 subagents=0\n"
    )
    assert not (index_dir / "rows.jsonl").exists()


def test_sessions_index_updates_and_rebuilds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autoskillit.cli.ops._sessions import sessions_index

    log_root = tmp_path / "logs"
    _seed_session(log_root)
    _configure_log_root(monkeypatch, log_root)

    sessions_index(update=True)
    index_dir = log_root / "report-index"
    assert capsys.readouterr().out == (
        "report index: walked 2 items, wrote 1 rows\n"
        f"report index v{REPORT_INDEX_SCHEMA_VERSION} at {index_dir}: "
        "sessions=1 requests=0 tools=0 subagents=0\n"
    )

    sessions_index(rebuild=True)
    assert capsys.readouterr().out == (
        "report index: walked 2 items, wrote 1 rows\n"
        f"report index v{REPORT_INDEX_SCHEMA_VERSION} at {index_dir}: "
        "sessions=1 requests=0 tools=0 subagents=0\n"
    )


def test_sessions_index_reports_lease_contention_without_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autoskillit.cli.ops._sessions import sessions_index

    log_root = tmp_path / "logs"
    _seed_session(log_root)
    _configure_log_root(monkeypatch, log_root)
    index_dir = log_root / "report-index"
    index_dir.mkdir()

    with ArtifactLease.acquire_exclusive(index_dir / "index.lock", timeout=0.0):
        with pytest.raises(SystemExit) as raised:
            sessions_index(update=True)

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.err.strip() == (
        f"report index: another operation holds a required index or source lease: "
        f"{index_dir / 'index.lock'}"
    )
    assert captured.out == ""
