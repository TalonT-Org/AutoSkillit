"""Tests for starttime_ticks=0 identity degradation warning in run_managed_async."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog.testing

from autoskillit.execution.process import run_managed_async

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


class TestStarttimeTicksZeroWarning:
    """on_pid_resolved emits a warning when starttime_ticks cannot be resolved."""

    @pytest.mark.anyio
    async def test_on_pid_resolved_warns_when_ticks_zero(self, tmp_path: Path) -> None:
        """run_managed_async logs starttime_ticks_zero warning when ticks fall back to 0."""
        shim = tmp_path / "quick_exit.py"
        shim.write_text("import sys; sys.exit(0)")

        received: list[tuple[int, int]] = []

        def on_pid_resolved(pid: int, ticks: int) -> None:
            received.append((pid, ticks))

        with (
            structlog.testing.capture_logs() as logs,
            patch("autoskillit.execution.process.read_starttime_ticks", return_value=0),
        ):
            await run_managed_async(
                [sys.executable, str(shim)],
                cwd=tmp_path,
                timeout=10.0,
                on_pid_resolved=on_pid_resolved,
            )

        assert received, "on_pid_resolved callback was never called"
        assert all(ticks == 0 for _, ticks in received), (
            f"Expected ticks=0 for all callbacks, got: {received}"
        )
        warning_events = [
            log["event"] for log in logs if log.get("event") == "starttime_ticks_zero"
        ]
        assert warning_events, f"Expected 'starttime_ticks_zero' warning in logs, captured: {logs}"
