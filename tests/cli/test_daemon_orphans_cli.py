"""CLI behavior for report-default AutoSkillit daemon orphan handling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.cli._daemon_orphans import run_daemon_orphans
from autoskillit.core import ProcessCleanupResult
from autoskillit.execution import DaemonOrphanReapResult, OrphanedAutoSkillitDaemon
from autoskillit.execution.process import _daemon_orphans as daemon_orphans

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _candidate(state_root: Path) -> OrphanedAutoSkillitDaemon:
    return OrphanedAutoSkillitDaemon(
        pid=44,
        launch_id="0123456789abcdef",
        state_root=str(state_root),
        boot_id="boot",
        starttime_ticks=9,
        owner_pid=33,
        owner_boot_id="boot",
        owner_starttime_ticks=8,
    )


def test_default_reports_without_reaping(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    candidate = _candidate(tmp_path)
    monkeypatch.setattr(
        "autoskillit.execution.find_orphaned_autoskillit_daemons", lambda: [candidate]
    )
    monkeypatch.setattr(
        "autoskillit.execution.reap_orphaned_autoskillit_daemons",
        lambda _items: pytest.fail("report-only command reaped"),
    )
    run_daemon_orphans()
    output = capsys.readouterr().out
    assert "orphan: pid=44" in output
    assert "--reap" in output


def test_default_reports_when_no_candidates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("autoskillit.execution.find_orphaned_autoskillit_daemons", lambda: [])
    monkeypatch.setattr(
        "autoskillit.execution.reap_orphaned_autoskillit_daemons",
        lambda _items: pytest.fail("empty report-only command reaped"),
    )

    run_daemon_orphans()

    assert capsys.readouterr().out == "no orphaned AutoSkillit daemons\n"


def test_reap_prints_target_before_signaling_and_preserves_registry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    registry = tmp_path / ".autoskillit" / "temp" / "session_registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text('{"0123456789abcdef":{"session_type":"cook"}}')
    candidate = _candidate(tmp_path)
    events: list[str] = []
    monkeypatch.setattr(
        "autoskillit.execution.find_orphaned_autoskillit_daemons", lambda: [candidate]
    )

    def fake_kill(_pid: int, **_kwargs: object) -> ProcessCleanupResult:
        events.append(capsys.readouterr().out)
        return ProcessCleanupResult(root_pid=44, observation_complete=True)

    monkeypatch.setattr(daemon_orphans, "_candidate_for_pid", lambda _pid: candidate)
    monkeypatch.setattr(daemon_orphans, "kill_process_tree", fake_kill)
    run_daemon_orphans(reap=True)
    assert "orphan: pid=44" in events[0]
    assert json.loads(registry.read_text()) == {"0123456789abcdef": {"session_type": "cook"}}


@pytest.mark.parametrize("action", ["terminated", "skipped", "incomplete"])
def test_json_contains_candidates_and_explicit_results(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    action: str,
) -> None:
    candidate = _candidate(tmp_path)
    result = DaemonOrphanReapResult(
        44,
        action,  # type: ignore[arg-type]
        survivor_pids=(45,) if action == "incomplete" else (),
    )
    monkeypatch.setattr(
        "autoskillit.execution.find_orphaned_autoskillit_daemons", lambda: [candidate]
    )
    monkeypatch.setattr(
        "autoskillit.execution.reap_orphaned_autoskillit_daemons", lambda _items: [result]
    )
    run_daemon_orphans(reap=True, output_json=True)
    document = json.loads(capsys.readouterr().out)
    assert document["candidates"][0]["launch_id"] == "0123456789abcdef"
    assert document["results"] == [
        {
            "pid": 44,
            "action": action,
            "survivor_pids": [45] if action == "incomplete" else [],
            "access_denied_pids": [],
        }
    ]
