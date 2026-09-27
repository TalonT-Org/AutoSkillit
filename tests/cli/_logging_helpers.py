"""Logging isolation for CLI tests that exercise launch behavior."""

import pytest


def stub_configure_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep launch commands from reconfiguring the test process's logging."""
    monkeypatch.setattr("autoskillit.core.configure_logging", lambda **_kwargs: None)
