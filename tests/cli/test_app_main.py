"""Tests for autoskillit.cli.app.main() entry point behaviour."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


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


def test_serve_activity_check_uses_backend_derived_marker_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """serve() must derive _marker_dir via backend.session_locator().project_log_dir()."""
    import asyncio
    import importlib
    from unittest.mock import MagicMock

    app_module = importlib.import_module("autoskillit.cli.app")

    monkeypatch.chdir(tmp_path)

    expected_dir = tmp_path / "backend-marker"

    mock_backend = MagicMock()
    mock_backend.session_locator.return_value.project_log_dir.return_value = expected_dir

    mock_ctx = MagicMock()
    mock_ctx.project_dir = tmp_path
    mock_ctx.backend = mock_backend
    mock_ctx.fleet_lock = None

    mock_cfg = MagicMock()
    mock_cfg.logging.level = "INFO"
    mock_cfg.logging.json_output = None
    mock_cfg.safety.protected_branches = []

    monkeypatch.setattr("autoskillit.config.load_config", lambda _: mock_cfg)
    monkeypatch.setattr("autoskillit.core.configure_logging", lambda **kw: None)
    monkeypatch.setattr("autoskillit.server.make_context", lambda *a, **kw: mock_ctx)
    monkeypatch.setattr("autoskillit.server._initialize", lambda ctx: None)

    captured_activity_check: list = []

    async def fake_serve_guard(mcp_instance, *, activity_check=None, **kw):
        captured_activity_check.append(activity_check)

    monkeypatch.setattr(app_module, "serve_with_signal_guard", fake_serve_guard)
    monkeypatch.setattr("anyio.run", lambda fn, **kw: asyncio.run(fn()))

    from autoskillit.cli.app import serve

    serve()

    mock_backend.session_locator.return_value.project_log_dir.assert_called_once_with(
        str(tmp_path)
    )

    assert captured_activity_check, "serve_with_signal_guard was not called"

    seen_marker_dirs: list = []
    monkeypatch.setattr(
        app_module,
        "is_server_active",
        lambda md, fl: (seen_marker_dirs.append(md), False)[-1],
    )

    captured_activity_check[0]()
    assert seen_marker_dirs == [expected_dir], (
        f"activity_check should pass backend-derived marker_dir ({expected_dir}), "
        f"got {seen_marker_dirs}"
    )
