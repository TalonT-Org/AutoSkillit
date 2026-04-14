"""Integration test: serve() must call mcp.run_async() within the startup timing budget.

REQ-STARTUP-001: The pre-transport startup work (serve() entry to mcp.run_async() call)
must complete within 2 seconds, leaving 3s of margin before Claude Code's ~5s
connection timeout.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import structlog.testing

import autoskillit.server as server_mod
from autoskillit.server import _state


def test_serve_calls_mcp_run_within_budget(monkeypatch, tmp_path):
    """serve() pre-mcp.run_async() work must complete within 2 seconds."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    # Ensure _state._ctx is restored after the test (xdist safety)
    original_ctx = _state._ctx
    monkeypatch.setattr(_state, "_ctx", None)

    run_called_at: list[float] = []

    async def timed_run_async(*_a, **_kw):
        run_called_at.append(time.monotonic())

    with (
        patch.object(server_mod.mcp, "run_async", side_effect=timed_run_async),
        structlog.testing.capture_logs(),
    ):
        import autoskillit.cli as cli_mod

        start = time.monotonic()
        cli_mod.serve()

    # Restore global state
    monkeypatch.setattr(_state, "_ctx", original_ctx)

    assert run_called_at, "mcp.run_async() was never called"
    elapsed = run_called_at[0] - start
    assert elapsed < 2.0, (
        f"serve() took {elapsed:.2f}s before mcp.run_async() — exceeds 2s budget. "
        f"Claude Code timeout is ~5s; budget is 2s to leave margin."
    )
