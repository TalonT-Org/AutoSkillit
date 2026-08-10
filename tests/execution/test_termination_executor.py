"""Integration tests for execute_termination_action (1b).

Uses real subprocesses to verify drain-window behavior. All tests here must
FAIL before Phase 2 implements execute_termination_action and the KillReason enum.
"""

from __future__ import annotations

import sys
import textwrap
import time
from unittest.mock import patch

import anyio
import pytest

from autoskillit.core.types import KillReason, TerminationAction
from autoskillit.execution.process import execute_termination_action

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

# ---------------------------------------------------------------------------
# Helper scripts
# ---------------------------------------------------------------------------

# Script that exits after sleeping N seconds (float arg)
_EXIT_AFTER_SLEEP = textwrap.dedent("""\
    import sys, time
    delay = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
    time.sleep(delay)
    sys.exit(0)
""")


async def _spawn_script(script_text: str, args: list[str], tmp_path) -> anyio.abc.Process:
    """Spawn a Python subprocess running script_text with args."""
    script = tmp_path / "helper.py"
    script.write_text(script_text)
    return await anyio.open_process(
        [sys.executable, str(script), *args],
        start_new_session=True,
    )


class TestDrainWindowPermitsNaturalExit:
    """DRAIN_THEN_KILL_IF_ALIVE: process exits inside window → natural_exit."""

    @pytest.mark.anyio
    async def test_drain_window_permits_natural_exit_when_process_exits_inside_window(
        self, tmp_path
    ) -> None:
        """Process exits at 0.5s; grace=3.0s → kill_reason=NATURAL_EXIT, no kill called."""
        proc = await _spawn_script(_EXIT_AFTER_SLEEP, ["0.5"], tmp_path)
        proc_exited_event = anyio.Event()

        # Start a task that waits for the process and sets the event
        async def _watch() -> None:
            await proc.wait()
            proc_exited_event.set()

        kill_calls: list[int] = []

        async def _mock_kill(
            pid: int,
            timeout: float = 2.0,
            process_group_id: int | None = None,
        ) -> None:
            kill_calls.append(pid)

        with patch(
            "autoskillit.execution.process.async_kill_process_tree",
            side_effect=_mock_kill,
        ):
            import structlog

            proc_log = structlog.get_logger().bind(pid=proc.pid)

            async with anyio.create_task_group() as tg:
                tg.start_soon(_watch)
                kill_reason = await execute_termination_action(
                    TerminationAction.DRAIN_THEN_KILL_IF_ALIVE,
                    proc=proc,
                    process_exited_event=proc_exited_event,
                    grace_seconds=3.0,
                    proc_log=proc_log,
                )
                tg.cancel_scope.cancel()

        assert kill_reason == KillReason.NATURAL_EXIT
        assert kill_calls == [], (
            f"async_kill_process_tree should not have been called, got calls to pids={kill_calls}"
        )


class TestDrainWindowEscalatesToKill:
    """DRAIN_THEN_KILL_IF_ALIVE: process survives grace window → KILL_AFTER_COMPLETION."""

    @pytest.mark.anyio
    async def test_drain_window_escalates_to_kill_when_process_survives(self, tmp_path) -> None:
        """Process sleeps 10s; grace=0.3s → kill_reason=KILL_AFTER_COMPLETION, kill called once."""
        proc = await _spawn_script(_EXIT_AFTER_SLEEP, ["10.0"], tmp_path)
        proc_exited_event = anyio.Event()

        kill_calls: list[int] = []

        async def _mock_kill(
            pid: int,
            timeout: float = 2.0,
            process_group_id: int | None = None,
        ) -> None:
            kill_calls.append(pid)
            proc.kill()
            await proc.wait()

        import structlog

        proc_log = structlog.get_logger().bind(pid=proc.pid)

        with patch(
            "autoskillit.execution.process.async_kill_process_tree",
            side_effect=_mock_kill,
        ):
            kill_reason = await execute_termination_action(
                TerminationAction.DRAIN_THEN_KILL_IF_ALIVE,
                proc=proc,
                process_exited_event=proc_exited_event,
                grace_seconds=0.3,
                proc_log=proc_log,
            )

        assert kill_reason == KillReason.KILL_AFTER_COMPLETION
        assert len(kill_calls) == 1, f"Expected exactly one kill call, got {kill_calls}"


class TestImmediateKillSkipsDrain:
    """IMMEDIATE_KILL: no drain delay, kill called within milliseconds."""

    @pytest.mark.anyio
    async def test_immediate_kill_skips_drain(self, tmp_path) -> None:
        """IMMEDIATE_KILL must call kill without waiting for drain window."""
        proc = await _spawn_script(_EXIT_AFTER_SLEEP, ["10.0"], tmp_path)
        proc_exited_event = anyio.Event()

        kill_calls: list[int] = []
        call_time: list[float] = []

        async def _mock_kill(
            pid: int,
            timeout: float = 2.0,
            process_group_id: int | None = None,
        ) -> None:
            call_time.append(time.monotonic())
            kill_calls.append(pid)
            proc.kill()
            await proc.wait()

        import structlog

        proc_log = structlog.get_logger().bind(pid=proc.pid)
        start = time.monotonic()

        with patch(
            "autoskillit.execution.process.async_kill_process_tree",
            side_effect=_mock_kill,
        ):
            kill_reason = await execute_termination_action(
                TerminationAction.IMMEDIATE_KILL,
                proc=proc,
                process_exited_event=proc_exited_event,
                grace_seconds=3.0,  # large grace, should be ignored
                proc_log=proc_log,
            )

        elapsed = (call_time[0] - start) if call_time else 999.0
        assert kill_reason == KillReason.INFRA_KILL
        assert len(kill_calls) == 1
        assert elapsed < 0.5, f"IMMEDIATE_KILL took {elapsed:.3f}s — should be near-instant"


class TestNoKillNeverTouchesKillHelper:
    """NO_KILL: async_kill_process_tree must never be called."""

    @pytest.mark.anyio
    async def test_no_kill_action_never_touches_kill_helper(self, tmp_path) -> None:
        """NO_KILL returns NATURAL_EXIT without calling async_kill_process_tree."""
        proc = await _spawn_script(_EXIT_AFTER_SLEEP, ["0.1"], tmp_path)
        await proc.wait()  # let it exit first
        proc_exited_event = anyio.Event()
        proc_exited_event.set()

        kill_calls: list[int] = []

        async def _mock_kill(
            pid: int,
            timeout: float = 2.0,
            process_group_id: int | None = None,
        ) -> None:
            kill_calls.append(pid)

        import structlog

        proc_log = structlog.get_logger().bind(pid=proc.pid)

        with patch(
            "autoskillit.execution.process.async_kill_process_tree",
            side_effect=_mock_kill,
        ):
            kill_reason = await execute_termination_action(
                TerminationAction.NO_KILL,
                proc=proc,
                process_exited_event=proc_exited_event,
                grace_seconds=3.0,
                proc_log=proc_log,
            )

        assert kill_reason == KillReason.NATURAL_EXIT
        assert kill_calls == [], f"NO_KILL must not call async_kill_process_tree, got {kill_calls}"


class TestChildLivenessDefersKill:
    """DRAIN_THEN_KILL_IF_ALIVE + active children: kill is deferred beyond grace_seconds.

    Child-process liveness is simulated via a mock on _has_active_child_processes
    (the same usage-site patching style already used for async_kill_process_tree
    in this file) rather than relying on real psutil CPU-percent sampling, which
    requires a priming call before it reports non-zero usage and would make the
    test's timing assertions racy under CI load.
    """

    @pytest.mark.anyio
    async def test_active_children_defer_kill_past_grace_seconds(self, tmp_path) -> None:
        """Active child processes defer the kill past grace_seconds, then kill fires."""
        proc = await _spawn_script(_EXIT_AFTER_SLEEP, ["10.0"], tmp_path)
        proc_exited_event = anyio.Event()

        kill_calls: list[int] = []

        async def _mock_kill(
            pid: int,
            timeout: float = 2.0,
            process_group_id: int | None = None,
        ) -> None:
            kill_calls.append(pid)
            proc.kill()
            await proc.wait()

        import structlog

        proc_log = structlog.get_logger().bind(pid=proc.pid)
        start = time.monotonic()

        with (
            patch(
                "autoskillit.execution.process.async_kill_process_tree",
                side_effect=_mock_kill,
            ),
            patch(
                "autoskillit.execution.process._has_active_child_processes",
                return_value=True,
            ),
            patch(
                "autoskillit.execution.process._has_active_api_connection",
                return_value=False,
            ),
        ):
            kill_reason = await execute_termination_action(
                TerminationAction.DRAIN_THEN_KILL_IF_ALIVE,
                proc=proc,
                process_exited_event=proc_exited_event,
                grace_seconds=0.5,
                proc_log=proc_log,
                pid=proc.pid,
                child_deferral_ceiling=1.5,
            )

        elapsed = time.monotonic() - start
        assert kill_reason == KillReason.KILL_AFTER_COMPLETION
        assert len(kill_calls) == 1
        assert elapsed > 0.5, f"Expected deferral past grace_seconds=0.5, took only {elapsed:.3f}s"


class TestChildLivenessFastKillUnchanged:
    """DRAIN_THEN_KILL_IF_ALIVE + child_deferral_ceiling=0: fast kill unchanged (today)."""

    @pytest.mark.anyio
    async def test_zero_ceiling_disables_deferral(self, tmp_path) -> None:
        """child_deferral_ceiling=0 must never consult child-liveness predicates."""
        proc = await _spawn_script(_EXIT_AFTER_SLEEP, ["10.0"], tmp_path)
        proc_exited_event = anyio.Event()

        kill_calls: list[int] = []

        async def _mock_kill(
            pid: int,
            timeout: float = 2.0,
            process_group_id: int | None = None,
        ) -> None:
            kill_calls.append(pid)
            proc.kill()
            await proc.wait()

        import structlog

        proc_log = structlog.get_logger().bind(pid=proc.pid)
        start = time.monotonic()

        with (
            patch(
                "autoskillit.execution.process.async_kill_process_tree",
                side_effect=_mock_kill,
            ),
            patch(
                "autoskillit.execution.process._has_active_child_processes",
            ) as mock_has_active,
        ):
            kill_reason = await execute_termination_action(
                TerminationAction.DRAIN_THEN_KILL_IF_ALIVE,
                proc=proc,
                process_exited_event=proc_exited_event,
                grace_seconds=0.3,
                proc_log=proc_log,
                pid=proc.pid,
                child_deferral_ceiling=0.0,
            )

        elapsed = time.monotonic() - start
        assert kill_reason == KillReason.KILL_AFTER_COMPLETION
        assert len(kill_calls) == 1, f"Expected exactly one kill call, got {kill_calls}"
        assert elapsed < 1.0, (
            f"ceiling=0 should behave like today's fast kill, took {elapsed:.3f}s"
        )
        mock_has_active.assert_not_called()


class TestChildLivenessCeilingExceeded:
    """DRAIN_THEN_KILL_IF_ALIVE + active children past ceiling: kill proceeds despite activity."""

    @pytest.mark.anyio
    async def test_kill_proceeds_once_ceiling_exceeded(self, tmp_path) -> None:
        """Kill fires shortly after child_deferral_ceiling expires even if child stays active."""
        proc = await _spawn_script(_EXIT_AFTER_SLEEP, ["10.0"], tmp_path)
        proc_exited_event = anyio.Event()

        kill_calls: list[int] = []

        async def _mock_kill(
            pid: int,
            timeout: float = 2.0,
            process_group_id: int | None = None,
        ) -> None:
            kill_calls.append(pid)
            proc.kill()
            await proc.wait()

        import structlog

        proc_log = structlog.get_logger().bind(pid=proc.pid)
        start = time.monotonic()

        with (
            patch(
                "autoskillit.execution.process.async_kill_process_tree",
                side_effect=_mock_kill,
            ),
            patch(
                "autoskillit.execution.process._has_active_child_processes",
                return_value=True,
            ),
            patch(
                "autoskillit.execution.process._has_active_api_connection",
                return_value=False,
            ),
        ):
            kill_reason = await execute_termination_action(
                TerminationAction.DRAIN_THEN_KILL_IF_ALIVE,
                proc=proc,
                process_exited_event=proc_exited_event,
                grace_seconds=0.2,
                proc_log=proc_log,
                pid=proc.pid,
                child_deferral_ceiling=1.0,
            )

        elapsed_after_grace = (time.monotonic() - start) - 0.2
        assert kill_reason == KillReason.KILL_AFTER_COMPLETION
        assert len(kill_calls) == 1
        assert 1.0 <= elapsed_after_grace <= 3.0, (
            f"Expected kill within ~1s of ceiling expiry (1.0s), "
            f"deferral loop ran for {elapsed_after_grace:.3f}s"
        )


class TestChildLivenessChildExitsDuringDeferral:
    """DRAIN_THEN_KILL_IF_ALIVE: child becomes inactive mid-deferral → kill proceeds."""

    @pytest.mark.anyio
    async def test_kill_proceeds_once_children_go_inactive(self, tmp_path) -> None:
        """Once the liveness predicate reports inactive, the next poll proceeds to kill."""
        proc = await _spawn_script(_EXIT_AFTER_SLEEP, ["10.0"], tmp_path)
        proc_exited_event = anyio.Event()

        kill_calls: list[int] = []

        async def _mock_kill(
            pid: int,
            timeout: float = 2.0,
            process_group_id: int | None = None,
        ) -> None:
            kill_calls.append(pid)
            proc.kill()
            await proc.wait()

        import structlog

        proc_log = structlog.get_logger().bind(pid=proc.pid)
        start = time.monotonic()

        with (
            patch(
                "autoskillit.execution.process.async_kill_process_tree",
                side_effect=_mock_kill,
            ),
            patch(
                "autoskillit.execution.process._has_active_child_processes",
                side_effect=[True, False],
            ),
            patch(
                "autoskillit.execution.process._has_active_api_connection",
                return_value=False,
            ),
        ):
            kill_reason = await execute_termination_action(
                TerminationAction.DRAIN_THEN_KILL_IF_ALIVE,
                proc=proc,
                process_exited_event=proc_exited_event,
                grace_seconds=0.2,
                proc_log=proc_log,
                pid=proc.pid,
                child_deferral_ceiling=30.0,
            )

        elapsed_after_grace = (time.monotonic() - start) - 0.2
        assert kill_reason == KillReason.KILL_AFTER_COMPLETION
        assert len(kill_calls) == 1
        assert elapsed_after_grace < 30.0, (
            "Kill should proceed as soon as the second poll reports inactivity, "
            "well before the 30s ceiling"
        )
