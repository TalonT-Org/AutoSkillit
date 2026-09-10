"""Tests for process lifecycle callback timing in run_managed_async."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import anyio
import psutil
import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


class TestOnPidResolvedTiming:
    async def test_on_pid_resolved_fires_during_execution(self, tmp_path: Path) -> None:
        """Callback fires while the subprocess is still alive (spawn-time, not post-completion)."""
        from autoskillit.execution.process import run_managed_async

        alive_at_callback: list[bool] = []

        def callback(pid: int, ticks: int) -> None:
            alive_at_callback.append(psutil.pid_exists(pid))

        await run_managed_async(
            [sys.executable, "-c", "import time; time.sleep(0.3)"],
            cwd=tmp_path,
            timeout=5.0,
            on_pid_resolved=callback,
        )

        assert len(alive_at_callback) == 1, "Callback should fire exactly once"
        assert alive_at_callback[0] is True, "Process must be alive when callback fires"

    async def test_on_pid_resolved_provides_starttime_ticks(self, tmp_path: Path) -> None:
        """On Linux, starttime_ticks is > 0; non-Linux 0 is accepted."""
        from autoskillit.execution.process import run_managed_async

        received_ticks: list[int] = []

        def callback(pid: int, ticks: int) -> None:
            received_ticks.append(ticks)

        await run_managed_async(
            [sys.executable, "-c", "import time; time.sleep(0.3)"],
            cwd=tmp_path,
            timeout=5.0,
            on_pid_resolved=callback,
        )

        assert len(received_ticks) == 1
        if sys.platform == "linux":
            assert received_ticks[0] > 0, "starttime_ticks must be > 0 on Linux"
        else:
            assert received_ticks[0] >= 0, "Non-Linux: 0 is accepted"

    async def test_on_pid_resolved_not_called_on_zero_pid(self, tmp_path: Path) -> None:
        """Guard: on_pid_resolved is NOT invoked when _observed_pid is 0."""
        from autoskillit.execution.process import run_managed_async

        called_with: list[tuple[int, int]] = []

        mock_proc = MagicMock()
        mock_proc.pid = 0
        mock_owner = MagicMock()
        mock_owner.pid = 0
        mock_owner.process = mock_proc
        mock_owner.pgid = 0

        with patch(
            "autoskillit.execution.process.spawn_owned_process",
            return_value=mock_owner,
        ):
            try:
                await run_managed_async(
                    [sys.executable, "-c", "pass"],
                    cwd=tmp_path,
                    timeout=1.0,
                    on_pid_resolved=lambda pid, ticks: called_with.append((pid, ticks)),
                )
            except Exception:
                pass  # Mock artifacts expected — only the callback check matters

        assert called_with == [], "on_pid_resolved must NOT be called when pid == 0"


class TestProcessLifecycleCallbacks:
    async def test_spawn_precedes_pid_resolution_and_reap_follows_completion(
        self, tmp_path: Path
    ) -> None:
        """The owned group is reported before PID resolution and after proven cleanup."""
        from autoskillit.execution.process import run_managed_async

        events: list[tuple[str, int, int]] = []

        await run_managed_async(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path,
            timeout=5.0,
            on_process_spawned=lambda pid, pgid: events.append(("spawned", pid, pgid)),
            on_pid_resolved=lambda pid, ticks: events.append(("resolved", pid, ticks)),
            on_process_reaped=lambda pid, pgid: events.append(("reaped", pid, pgid)),
        )

        assert [kind for kind, _, _ in events] == ["spawned", "resolved", "reaped"]
        assert events[0][1:] == events[2][1:]
        assert events[0][1] == events[0][2]

    async def test_spawn_callback_failure_settles_and_notifies_reap_once(
        self, tmp_path: Path
    ) -> None:
        """A spawn callback failure cannot report success and still settles the child."""
        from autoskillit.execution.process import run_managed_async

        reaped: list[tuple[int, int]] = []

        def fail_spawn(_pid: int, _pgid: int) -> None:
            raise RuntimeError("spawn callback failed")

        with pytest.raises(RuntimeError, match="spawn callback failed"):
            await run_managed_async(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                cwd=tmp_path,
                timeout=30.0,
                on_process_spawned=fail_spawn,
                on_process_reaped=lambda pid, pgid: reaped.append((pid, pgid)),
            )

        assert len(reaped) == 1
        assert reaped[0][0] == reaped[0][1]

    async def test_reap_callback_failure_is_not_retried(self, tmp_path: Path) -> None:
        """A failed reap notification fails the run without duplicate notification."""
        from autoskillit.execution.process import run_managed_async

        attempts: list[tuple[int, int]] = []

        def fail_reap(pid: int, pgid: int) -> None:
            attempts.append((pid, pgid))
            raise RuntimeError("reap callback failed")

        with pytest.raises(RuntimeError, match="reap callback failed"):
            await run_managed_async(
                [sys.executable, "-c", "pass"],
                cwd=tmp_path,
                timeout=5.0,
                on_process_reaped=fail_reap,
            )

        assert len(attempts) == 1

    async def test_reap_callback_follows_forced_timeout(self, tmp_path: Path) -> None:
        """Forced termination reports reaping only after the managed group settles."""
        from autoskillit.execution.process import run_managed_async

        reaped: list[tuple[int, int]] = []
        await run_managed_async(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=tmp_path,
            timeout=0.01,
            on_process_reaped=lambda pid, pgid: reaped.append((pid, pgid)),
        )

        assert len(reaped) == 1
        assert reaped[0][0] == reaped[0][1]

    async def test_reap_callback_follows_cancellation(self, tmp_path: Path) -> None:
        """Cancellation shields owned-process settlement before notifying reaping."""
        from autoskillit.execution.process import run_managed_async

        spawned = anyio.Event()
        reaped: list[tuple[int, int]] = []

        async def run_until_cancelled() -> None:
            await run_managed_async(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                cwd=tmp_path,
                timeout=30.0,
                on_process_spawned=lambda _pid, _pgid: spawned.set(),
                on_process_reaped=lambda pid, pgid: reaped.append((pid, pgid)),
            )

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(run_until_cancelled)
            await spawned.wait()
            task_group.cancel_scope.cancel()

        assert len(reaped) == 1
        assert reaped[0][0] == reaped[0][1]

    async def test_incomplete_normal_cleanup_omits_reap_callback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A settled process with incomplete evidence cannot be reported as reaped."""
        from autoskillit.execution import process as process_module

        original_execute = process_module.execute_termination_action

        async def incomplete_execute(*args, **kwargs):
            kill_reason, returncode, cleanup = await original_execute(*args, **kwargs)
            return (
                kill_reason,
                returncode,
                dataclasses.replace(cleanup, observation_complete=False),
            )

        monkeypatch.setattr(process_module, "execute_termination_action", incomplete_execute)
        reaped: list[tuple[int, int]] = []

        result = await process_module.run_managed_async(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path,
            timeout=5.0,
            on_process_reaped=lambda pid, pgid: reaped.append((pid, pgid)),
        )

        assert result.cleanup_evidence is not None
        assert result.cleanup_evidence.complete is False
        assert reaped == []

    async def test_incomplete_exception_cleanup_omits_reap_callback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Callback errors cannot turn incomplete exception cleanup into a reap report."""
        from autoskillit.execution.process import run_managed_async
        from autoskillit.execution.process._process_kill import OwnedProcessGroup

        original_settle = OwnedProcessGroup.settle_preserving

        def incomplete_settle(self, cause, **kwargs):
            cleanup = original_settle(self, cause, **kwargs)
            return dataclasses.replace(cleanup, observation_complete=False)

        monkeypatch.setattr(OwnedProcessGroup, "settle_preserving", incomplete_settle)
        reaped: list[tuple[int, int]] = []

        def fail_pid(_pid: int, _ticks: int) -> None:
            raise RuntimeError("PID callback failed")

        with pytest.raises(RuntimeError, match="PID callback failed"):
            await run_managed_async(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                cwd=tmp_path,
                timeout=30.0,
                on_pid_resolved=fail_pid,
                on_process_reaped=lambda pid, pgid: reaped.append((pid, pgid)),
            )

        assert reaped == []

    async def test_failed_owned_spawn_invokes_no_lifecycle_callbacks(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """No lifecycle callback runs when no owned process group was created."""
        from autoskillit.execution import process as process_module

        def fail_spawn(*_args, **_kwargs):
            raise OSError("spawn failed")

        monkeypatch.setattr(process_module, "spawn_owned_process", fail_spawn)
        callbacks: list[tuple[int, int]] = []

        with pytest.raises(OSError, match="spawn failed"):
            await process_module.run_managed_async(
                [sys.executable, "-c", "pass"],
                cwd=tmp_path,
                timeout=5.0,
                on_process_spawned=lambda pid, pgid: callbacks.append((pid, pgid)),
                on_process_reaped=lambda pid, pgid: callbacks.append((pid, pgid)),
            )

        assert callbacks == []
