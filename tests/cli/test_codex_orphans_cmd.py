"""Tests for the autoskillit codex-orphans CLI command.

``run_codex_orphans`` and ``_check_orphaned_codex_processes`` both resolve
``find_orphaned_codex_processes``/``reap_orphaned_codex_processes`` via a
local ``from autoskillit.execution import ...`` inside the function body —
so these tests patch the attributes on ``autoskillit.execution`` (the
resolution site at call time), not on the caller modules, which never bind
those names at module scope.
"""

from __future__ import annotations

import json

import pytest

from autoskillit.execution import (
    CodexOrphanReapResult,
    OrphanedCodexProcess,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _make_orphan(pid: int) -> OrphanedCodexProcess:
    return OrphanedCodexProcess(
        pid=pid,
        fd0_target="/dev/pts/5 (deleted)",
        exe_target="/usr/bin/codex",
        starttime_ticks=12345,
        started_at=1000000.0,
    )


def test_codex_orphans_cmd_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    import autoskillit.cli.ops as ops_pkg
    from autoskillit import cli

    called_with: dict[str, object] = {}

    def mock_run_codex_orphans(*, reap: bool = False, output_json: bool = False) -> None:
        called_with["reap"] = reap
        called_with["output_json"] = output_json

    monkeypatch.setattr(ops_pkg, "run_codex_orphans", mock_run_codex_orphans)

    cli.codex_orphans(reap=True, output_json=True)

    assert called_with == {"reap": True, "output_json": True}


def test_run_reports_orphans_plain(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import autoskillit.execution as execution_mod
    from autoskillit.cli.ops import run_codex_orphans

    orphans = [_make_orphan(1001), _make_orphan(1002)]
    monkeypatch.setattr(execution_mod, "find_orphaned_codex_processes", lambda: orphans)

    run_codex_orphans()

    out = capsys.readouterr().out
    assert "1001" in out
    assert "1002" in out
    assert "/dev/pts/5 (deleted)" in out
    assert "run again with --reap" in out


def test_run_reports_no_orphans(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import autoskillit.execution as execution_mod
    from autoskillit.cli.ops import run_codex_orphans

    monkeypatch.setattr(execution_mod, "find_orphaned_codex_processes", lambda: [])

    run_codex_orphans()

    assert capsys.readouterr().out == "no orphaned codex processes\n"


def test_run_output_json_shape(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import autoskillit.execution as execution_mod
    from autoskillit.cli.ops import run_codex_orphans

    orphans = [_make_orphan(1001), _make_orphan(1002)]
    monkeypatch.setattr(execution_mod, "find_orphaned_codex_processes", lambda: orphans)

    run_codex_orphans(output_json=True)

    doc = json.loads(capsys.readouterr().out)
    assert len(doc["orphans"]) == 2
    for entry in doc["orphans"]:
        assert set(entry) >= {"pid", "fd0_target", "exe_target", "started_at"}
    assert doc["reaped"] == []


def test_run_reap_output_json_shape(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import autoskillit.execution as execution_mod
    from autoskillit.cli.ops import run_codex_orphans

    orphans = [_make_orphan(1001)]
    monkeypatch.setattr(execution_mod, "find_orphaned_codex_processes", lambda: orphans)
    monkeypatch.setattr(
        execution_mod,
        "reap_orphaned_codex_processes",
        lambda scanned: [
            CodexOrphanReapResult(
                scanned[0].pid,
                "terminated",
                observation_complete=True,
            )
        ],
    )

    run_codex_orphans(reap=True, output_json=True)

    doc = json.loads(capsys.readouterr().out)
    assert doc["reaped"] == [
        {
            "pid": 1001,
            "action": "terminated",
            "observation_complete": True,
            "survivor_pids": [],
            "access_denied_pids": [],
        }
    ]


def test_run_reap_invokes_reaper_and_reports(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import autoskillit.execution as execution_mod
    from autoskillit.cli.ops import run_codex_orphans

    orphans = [_make_orphan(2001), _make_orphan(2002), _make_orphan(2003)]
    monkeypatch.setattr(execution_mod, "find_orphaned_codex_processes", lambda: orphans)

    fake_results = [
        CodexOrphanReapResult(2001, "terminated", observation_complete=True),
        CodexOrphanReapResult(
            2002,
            "incomplete",
            observation_complete=True,
            survivor_pids=(3001,),
        ),
        CodexOrphanReapResult(2003, "skipped"),
    ]
    received: list[object] = []

    def fake_reap(scanned: object) -> list[CodexOrphanReapResult]:
        received.append(scanned)
        return fake_results

    monkeypatch.setattr(execution_mod, "reap_orphaned_codex_processes", fake_reap)

    run_codex_orphans(reap=True)

    assert received == [orphans]

    out = capsys.readouterr().out
    assert "terminated pid 2001" in out
    assert "incomplete pid 2002 (survivors: 3001)" in out
    assert "skipped pid 2003 (no longer matches the orphan signature)" in out
    assert out.index("orphan: pid=2001") < out.index("terminated pid 2001")
    assert out.index("orphan: pid=2002") < out.index("incomplete pid 2002")
    assert out.index("orphan: pid=2003") < out.index("skipped pid 2003")
    assert "exited" not in out
    assert "caused" not in out


def test_run_reap_reports_observation_incomplete_without_pid_lists(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import autoskillit.execution as execution_mod
    from autoskillit.cli.ops import run_codex_orphans

    orphan = _make_orphan(2004)
    monkeypatch.setattr(execution_mod, "find_orphaned_codex_processes", lambda: [orphan])
    monkeypatch.setattr(
        execution_mod,
        "reap_orphaned_codex_processes",
        lambda _scanned: [CodexOrphanReapResult(2004, "incomplete")],
    )

    run_codex_orphans(reap=True)

    assert "incomplete pid 2004 (observation incomplete)" in capsys.readouterr().out
