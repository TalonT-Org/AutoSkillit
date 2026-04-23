"""Tests for _franchise_signal_guard in cli/_franchise.py (Group J)."""

from __future__ import annotations

import json
import os
import signal as _signal
from pathlib import Path
from unittest.mock import MagicMock, patch

import anyio
import pytest

from autoskillit.franchise import (
    DispatchRecord,
    DispatchStatus,
    read_state,
    write_initial_state,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small, pytest.mark.feature("franchise")]

BOOT_ID = "boot-sg-001"
TICKS = 5000


def _make_running_state(
    tmp_path: Path,
    *,
    dispatch_name: str = "sg-dispatch",
    l2_pid: int = 0,
    l2_starttime_ticks: int = TICKS,
    l2_boot_id: str = BOOT_ID,
) -> tuple[Path, str]:
    sp = tmp_path / "state.json"
    campaign_id = "camp-sg-001"
    write_initial_state(
        sp,
        campaign_id,
        "signal-guard-campaign",
        "/m.yaml",
        [DispatchRecord(name=dispatch_name)],
    )
    raw = json.loads(sp.read_text())
    raw["dispatches"][0].update(
        {
            "status": "running",
            "dispatch_id": "did-sg",
            "l2_pid": l2_pid,
            "l2_starttime_ticks": l2_starttime_ticks,
            "l2_boot_id": l2_boot_id,
            "started_at": 1000.0,
        }
    )
    sp.write_text(json.dumps(raw))
    return sp, campaign_id


async def _run_signal_guard(
    state_path: Path,
    campaign_id: str,
    sig: int,
    *,
    cleanup_on_interrupt: bool = False,
) -> None:
    """Run the signal guard and fire a signal after it's armed."""
    from autoskillit.cli._franchise import _franchise_signal_guard

    async with anyio.create_task_group() as tg:

        async def _fire_signal() -> None:
            await anyio.sleep(0.05)
            os.kill(os.getpid(), sig)

        tg.start_soon(_fire_signal)

        async with _franchise_signal_guard(
            state_path,
            campaign_id,
            cleanup_on_interrupt=cleanup_on_interrupt,
        ):
            await anyio.sleep(10)  # cancelled by signal guard


class TestSignalGuard:
    async def test_signal_guard_kills_running_dispatch(self, tmp_path: Path) -> None:
        state_path, campaign_id = _make_running_state(
            tmp_path, l2_pid=12345, l2_starttime_ticks=TICKS
        )
        kill_calls: list[int] = []

        async def fake_kill(pid: int, timeout: float = 2.0) -> None:
            kill_calls.append(pid)

        with (
            patch(
                "autoskillit.execution.read_starttime_ticks",
                return_value=TICKS,
            ),
            patch(
                "autoskillit.execution.async_kill_process_tree",
                side_effect=fake_kill,
            ),
        ):
            await _run_signal_guard(state_path, campaign_id, _signal.SIGTERM)

        assert kill_calls == [12345]
        state = read_state(state_path)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.INTERRUPTED

    async def test_signal_guard_reason_contains_signal_name(self, tmp_path: Path) -> None:
        state_path, campaign_id = _make_running_state(
            tmp_path,
            l2_pid=0,  # zero pid → skips kill, goes straight to mark
        )

        await _run_signal_guard(state_path, campaign_id, _signal.SIGINT)

        state = read_state(state_path)
        assert state is not None
        assert state.dispatches[0].reason == "signal_SIGINT"

    async def test_signal_guard_skips_zero_pid(self, tmp_path: Path) -> None:
        """Dispatch with l2_pid=0 → state marked INTERRUPTED without kill attempt."""
        state_path, campaign_id = _make_running_state(tmp_path, l2_pid=0)
        kill_calls: list[int] = []

        async def fake_kill(pid: int, timeout: float = 2.0) -> None:
            kill_calls.append(pid)

        with patch(
            "autoskillit.execution.async_kill_process_tree",
            side_effect=fake_kill,
        ):
            await _run_signal_guard(state_path, campaign_id, _signal.SIGTERM)

        assert kill_calls == []
        state = read_state(state_path)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.INTERRUPTED

    async def test_signal_guard_verifies_pid_identity(self, tmp_path: Path) -> None:
        """Dispatch with pid alive but different starttime_ticks → kill NOT called."""
        state_path, campaign_id = _make_running_state(
            tmp_path, l2_pid=12345, l2_starttime_ticks=TICKS
        )
        kill_calls: list[int] = []

        async def fake_kill(pid: int, timeout: float = 2.0) -> None:
            kill_calls.append(pid)

        with (
            patch(
                "autoskillit.execution.read_starttime_ticks",
                return_value=9999,  # mismatch → recycled
            ),
            patch(
                "autoskillit.execution.async_kill_process_tree",
                side_effect=fake_kill,
            ),
        ):
            await _run_signal_guard(state_path, campaign_id, _signal.SIGTERM)

        assert kill_calls == []
        state = read_state(state_path)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.INTERRUPTED

    async def test_signal_guard_stderr_resume_hint(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        state_path, campaign_id = _make_running_state(tmp_path, l2_pid=0)

        await _run_signal_guard(state_path, campaign_id, _signal.SIGTERM)

        captured = capsys.readouterr()
        assert campaign_id in captured.err

    async def test_signal_guard_cleanup_on_interrupt_true(self, tmp_path: Path) -> None:
        """cleanup_on_interrupt=True → DefaultWorkspaceManager.delete_contents is invoked."""
        state_path, campaign_id = _make_running_state(tmp_path, l2_pid=0)
        cleanup_calls: list[Path] = []

        class FakeWorkspaceManager:
            def delete_contents(self, directory: Path, preserve: object = None) -> object:
                cleanup_calls.append(directory)
                return MagicMock()

        with (
            patch("autoskillit.workspace.DefaultWorkspaceManager", FakeWorkspaceManager),
            patch("autoskillit.core.ensure_project_temp", return_value=tmp_path / "ws"),
        ):
            await _run_signal_guard(
                state_path, campaign_id, _signal.SIGTERM, cleanup_on_interrupt=True
            )

        assert len(cleanup_calls) == 1

    async def test_signal_guard_cleanup_on_interrupt_default_false(self, tmp_path: Path) -> None:
        """cleanup_on_interrupt=False (default) → workspace cleanup NOT invoked."""
        state_path, campaign_id = _make_running_state(tmp_path, l2_pid=0)
        cleanup_calls: list[Path] = []

        class FakeWorkspaceManager:
            def delete_contents(self, directory: Path, preserve: object = None) -> object:
                cleanup_calls.append(directory)
                return MagicMock()

        with (
            patch("autoskillit.workspace.DefaultWorkspaceManager", FakeWorkspaceManager),
            patch("autoskillit.core.ensure_project_temp", return_value=tmp_path / "ws"),
        ):
            await _run_signal_guard(state_path, campaign_id, _signal.SIGTERM)

        assert cleanup_calls == []

    async def test_signal_guard_cancels_scope_before_state_write(self, tmp_path: Path) -> None:
        """Scope is cancelled (guard exits) AND state is written (in shielded section).

        Verifies that scope cancellation happens before the state write by checking
        that the guard context manager exited cleanly (scope was cancelled) AND the
        dispatch state was updated (shielded cleanup ran).
        """
        state_path, campaign_id = _make_running_state(tmp_path, l2_pid=0)

        events: list[str] = []

        from autoskillit.franchise import mark_dispatch_interrupted as _real_mark

        def tracking_mark(sp: Path, name: str, *, reason: str) -> None:
            events.append("state_written")
            _real_mark(sp, name, reason=reason)

        with patch("autoskillit.cli._franchise.mark_dispatch_interrupted", tracking_mark):
            await _run_signal_guard(state_path, campaign_id, _signal.SIGTERM)
            events.append("guard_exited")

        # Guard exits (scope was cancelled) and state was written in the shielded section
        assert "state_written" in events
        assert "guard_exited" in events
        # state_written happens inside the shielded section; guard_exited happens after
        assert events.index("state_written") < events.index("guard_exited")
