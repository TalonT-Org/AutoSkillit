"""Tests for the autoskillit process-orphans CLI command.

``run_process_orphans`` resolves ``find_orphaned_tethers``/``default_tether_dir``/
``sweep_orphaned_tethers`` via a local ``from autoskillit.execution import ...``
inside the function body — so these tests patch the attributes on
``autoskillit.execution`` (the resolution site at call time), not on the
caller module, which never binds those names at module scope.
"""

from __future__ import annotations

import json

import pytest

from autoskillit.execution import OrphanedTetherRecord, TetherRecord, TetherSweepOutcome
from autoskillit.execution import TetherSweepReport as _TetherSweepReportCls

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _make_record(pid: int) -> TetherRecord:
    return TetherRecord(
        child_pid=pid,
        child_pgid=pid,
        child_starttime_ticks=12345,
        boot_id="test-boot-id",
        spawner_pid=99999,
        spawner_starttime_ticks=54321,
        spawned_at_ns=1_000_000_000,
        not_after=1000000.0,
        origin="test",
    )


def _make_orphan(pid: int, reason: str = "spawner_dead") -> OrphanedTetherRecord:
    return OrphanedTetherRecord(f"/tmp/tether-{pid}.json", _make_record(pid), reason)


def test_process_orphans_cmd_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    import autoskillit.cli.ops._process_orphans as process_orphans_mod
    from autoskillit import cli

    called_with: dict[str, object] = {}

    def mock_run_process_orphans(*, reap: bool = False, output_json: bool = False) -> None:
        called_with["reap"] = reap
        called_with["output_json"] = output_json

    monkeypatch.setattr(process_orphans_mod, "run_process_orphans", mock_run_process_orphans)

    cli.process_orphans(reap=True, output_json=True)

    assert called_with == {"reap": True, "output_json": True}


def test_run_reports_orphans_plain(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import autoskillit.execution as execution_mod
    from autoskillit.cli.ops import run_process_orphans

    orphans = [_make_orphan(1001), _make_orphan(1002, reason="ceiling_expired")]
    monkeypatch.setattr(execution_mod, "find_orphaned_tethers", lambda _dir: orphans)

    run_process_orphans()

    out = capsys.readouterr().out
    assert "pid=1001" in out
    assert "reason=spawner_dead" in out
    assert "pid=1002" in out
    assert "reason=ceiling_expired" in out
    assert "run again with --reap" in out


def test_run_reports_no_orphans(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import autoskillit.execution as execution_mod
    from autoskillit.cli.ops import run_process_orphans

    monkeypatch.setattr(execution_mod, "find_orphaned_tethers", lambda _dir: [])

    run_process_orphans()

    assert capsys.readouterr().out == "no orphaned process tethers\n"


def test_run_output_json_shape(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import autoskillit.execution as execution_mod
    from autoskillit.cli.ops import run_process_orphans

    orphans = [_make_orphan(1001), _make_orphan(1002)]
    monkeypatch.setattr(execution_mod, "find_orphaned_tethers", lambda _dir: orphans)

    run_process_orphans(output_json=True)

    doc = json.loads(capsys.readouterr().out)
    assert len(doc["orphans"]) == 2
    for entry in doc["orphans"]:
        assert set(entry) >= {"tether_path", "child_pid", "origin", "reason", "not_after"}
    assert doc["swept"] == []


def test_run_reap_invokes_sweep_and_reports(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import autoskillit.execution as execution_mod
    from autoskillit.cli.ops import run_process_orphans

    orphans = [_make_orphan(2001), _make_orphan(2002)]
    monkeypatch.setattr(execution_mod, "find_orphaned_tethers", lambda _dir: orphans)

    report = _TetherSweepReportCls(
        outcomes=(
            TetherSweepOutcome("/tmp/tether-2001.json", 2001, "reaped_orphan"),
            TetherSweepOutcome("/tmp/tether-2002.json", 2002, "kill_failed"),
        )
    )
    received: list[object] = []

    def fake_sweep(tether_dir: object) -> _TetherSweepReportCls:
        received.append(tether_dir)
        return report

    monkeypatch.setattr(execution_mod, "sweep_orphaned_tethers", fake_sweep)

    run_process_orphans(reap=True)

    assert len(received) == 1

    out = capsys.readouterr().out
    assert "terminated pid 2001 (reaped_orphan)" in out
    assert "incomplete pid 2002 (kill did not confirm death)" in out
    assert out.index("orphan: pid=2001") < out.index("terminated pid 2001")


def test_run_reap_output_json_shape(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import autoskillit.execution as execution_mod
    from autoskillit.cli.ops import run_process_orphans

    orphans = [_make_orphan(3001)]
    monkeypatch.setattr(execution_mod, "find_orphaned_tethers", lambda _dir: orphans)
    monkeypatch.setattr(
        execution_mod,
        "sweep_orphaned_tethers",
        lambda _dir: _TetherSweepReportCls(
            outcomes=(TetherSweepOutcome("/tmp/tether-3001.json", 3001, "reaped_orphan"),)
        ),
    )

    run_process_orphans(reap=True, output_json=True)

    doc = json.loads(capsys.readouterr().out)
    assert doc["swept"] == [
        {"tether_path": "/tmp/tether-3001.json", "child_pid": 3001, "outcome": "reaped_orphan"}
    ]
