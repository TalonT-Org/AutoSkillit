"""Test that serve() uses event-loop-routed signal handling. Regression guard for issue #745.

The original serve() installed a raw signal.signal(SIGTERM) handler that raised
KeyboardInterrupt, which escaped anyio's C-level event-loop runner before
finally: blocks could fire. The new implementation delegates to anyio.run()
with a _serve_with_signal_guard closure that uses anyio.open_signal_receiver.

Structural enforcement (no raw signal.signal SIGTERM) is covered by the AST guard
in tests/server/test_no_raw_signal_handler.py. This test covers unit behavior:
serve() must call anyio.run() and pass it an async coroutine function.
"""

from __future__ import annotations

import ast
import inspect
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def test_serve_uses_anyio_run_not_mcp_run(monkeypatch, tmp_path):
    """serve() routes through anyio.run(), not mcp.run() directly."""
    anyio_calls: list = []

    monkeypatch.chdir(tmp_path)

    mock_cfg = MagicMock()
    mock_cfg.logging.level = "INFO"
    mock_cfg.logging.json_output = None
    mock_cfg.safety.protected_branches = []

    monkeypatch.setattr("autoskillit.config.load_config", lambda _: mock_cfg)
    monkeypatch.setattr("autoskillit.core.configure_logging", lambda **kw: None)
    monkeypatch.setattr("autoskillit.server.make_context", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("autoskillit.server._initialize", lambda ctx: None)

    def capture_anyio_run(coro_fn, *args, **kwargs):
        anyio_calls.append(coro_fn)
        # Do not actually run it — just record the call

    monkeypatch.setattr("anyio.run", capture_anyio_run)

    from autoskillit.cli.app import serve

    serve()

    assert anyio_calls, "serve() did not call anyio.run()"
    guard_fn = anyio_calls[0]
    # _serve_with_signal_guard is a local async closure — must be a coroutine function
    assert inspect.iscoroutinefunction(guard_fn), (
        f"Expected serve() to pass an async coroutine function to anyio.run(), got {guard_fn!r}"
    )


def test_sighup_in_serve_guard_signal_list() -> None:
    """_serve_guard.py must pass signal.SIGHUP to open_signal_receiver (AST guard).

    Sending a real SIGHUP in tests is unsafe — before SIGHUP is registered,
    the default disposition terminates the process. AST analysis is safe for
    both the pre- and post-implementation codebase.
    """
    from autoskillit.core.paths import pkg_root

    src_path = pkg_root() / "cli" / "_serve_guard.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    sighup_found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "open_signal_receiver"):
            continue
        for arg in node.args:
            if (
                isinstance(arg, ast.Attribute)
                and arg.attr == "SIGHUP"
                and isinstance(arg.value, ast.Name)
                and arg.value.id == "signal"
            ):
                sighup_found = True
                break
        if sighup_found:
            break

    assert sighup_found, (
        "signal.SIGHUP not found in anyio.open_signal_receiver() call in _serve_guard.py. "
        "Terminal disconnects (SIGHUP) must trigger graceful shutdown."
    )
