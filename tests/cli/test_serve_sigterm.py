"""Test that serve() installs a SIGTERM handler. Regression guard for issue #745."""

import signal as signal_mod
from unittest.mock import MagicMock

import pytest


def test_serve_installs_sigterm_handler(monkeypatch, tmp_path):
    """serve() installs signal.SIGTERM → sys.exit(0) before mcp.run()."""
    installed_handlers = {}

    original_signal = signal_mod.signal

    def capture(sig, handler):
        installed_handlers[sig] = handler
        return original_signal(sig, handler)

    monkeypatch.chdir(tmp_path)

    mock_cfg = MagicMock()
    mock_cfg.logging.level = "INFO"
    mock_cfg.logging.json_output = None
    mock_cfg.safety.protected_branches = []

    # Patch the source modules that serve() imports from at call time.
    monkeypatch.setattr("autoskillit.config.load_config", lambda _: mock_cfg)
    monkeypatch.setattr("autoskillit.core.configure_logging", lambda **kw: None)
    monkeypatch.setattr("autoskillit.server.make_context", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("autoskillit.server._initialize", lambda ctx: None)
    monkeypatch.setattr("autoskillit.server.mcp.run", lambda: None)
    monkeypatch.setattr(signal_mod, "signal", capture)

    from autoskillit.cli.app import serve

    serve()

    assert signal_mod.SIGTERM in installed_handlers, "SIGTERM handler not installed by serve()"

    handler = installed_handlers[signal_mod.SIGTERM]
    with pytest.raises(SystemExit) as exc_info:
        handler(signal_mod.SIGTERM, None)
    assert exc_info.value.code == 0
