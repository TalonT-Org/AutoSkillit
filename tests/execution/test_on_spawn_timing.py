"""Tests for on_pid_resolved callback timing in run_managed_async (Group J)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import psutil
import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


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

        with patch(
            "autoskillit.execution.process.anyio.open_process",
            new_callable=AsyncMock,
            return_value=mock_proc,
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
