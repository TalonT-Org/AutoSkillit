"""Tests for autoskillit.cli.app.main() entry point behaviour."""

from __future__ import annotations

import importlib
import sys

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def test_main_does_not_call_app_after_update_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() must not call app() when run_update_checks triggers a process exit."""

    app_module = importlib.import_module("autoskillit.cli.app")

    app_called: list[bool] = []
    monkeypatch.setattr(app_module, "app", lambda: app_called.append(True))

    monkeypatch.setattr(
        "autoskillit.cli._init_helpers.evict_direct_mcp_entry", lambda *a, **kw: None
    )

    def fake_run_update_checks(**kwargs: object) -> None:
        raise SystemExit(0)

    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.run_update_checks", fake_run_update_checks
    )
    monkeypatch.setattr(sys, "argv", ["autoskillit", "order"])

    try:
        app_module.main()
    except SystemExit:
        pass

    assert not app_called, "app() must not be called when update path exits the process"
