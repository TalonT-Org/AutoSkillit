"""Integration test: serve() must call anyio.run() within the startup timing budget.

REQ-STARTUP-001: The pre-transport startup work (serve() entry to anyio.run() call)
must complete within 2 seconds, leaving 3s of margin before Claude Code's ~5s
connection timeout.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import structlog.testing

from autoskillit.server import _state


def test_serve_calls_mcp_run_within_budget(monkeypatch, tmp_path):
    """serve() pre-anyio.run() synchronous work must complete within 2 seconds."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    # Ensure _state._ctx is restored after the test (xdist safety)
    original_ctx = _state._ctx
    monkeypatch.setattr(_state, "_ctx", None)

    run_called_at: list[float] = []

    def timed_anyio_run(*_a, **_kw):
        run_called_at.append(time.monotonic())
        # Do not actually start the event loop — just record the call site

    with (
        patch("anyio.run", side_effect=timed_anyio_run),
        structlog.testing.capture_logs(),
    ):
        import autoskillit.cli as cli_mod

        start = time.monotonic()
        cli_mod.serve()

    # Restore global state
    monkeypatch.setattr(_state, "_ctx", original_ctx)

    assert run_called_at, "anyio.run() was never called"
    elapsed = run_called_at[0] - start
    assert elapsed < 2.0, (
        f"serve() took {elapsed:.2f}s before anyio.run() — exceeds 2s budget. "
        f"Claude Code timeout is ~5s; budget is 2s to leave margin."
    )
