"""Tests for autoskillit.cli.app.main() entry point behaviour."""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

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


def test_main_install_argv_skips_obligation_repair_to_avoid_reentrancy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """main() must not call attempt_obligation_repair() when invoked as
    `autoskillit install ...`.

    Regression guard: attempt_obligation_repair() itself spawns
    `autoskillit install --maintenance-update` as a subprocess. If main()
    called it unconditionally, that child's own main() would observe the
    SAME still-pending obligation (written by the parent transaction before
    launching this exact child) and recurse into another repair attempt —
    an unbounded process chain, or a deadlock if the outer install()
    invocation already holds the exclusive publication lease the recursive
    repair's install() child also needs.
    """
    app_module = importlib.import_module("autoskillit.cli.app")

    monkeypatch.setattr(app_module, "app", lambda: None)
    monkeypatch.setattr(
        "autoskillit.cli._init_helpers.evict_direct_mcp_entry", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.run_update_checks", lambda **kwargs: None
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    repair_calls: list[Path] = []
    monkeypatch.setattr(
        "autoskillit.cli.update._obligation_repair.attempt_obligation_repair",
        lambda home, **kwargs: repair_calls.append(home),
    )
    monkeypatch.setattr(sys, "argv", ["autoskillit", "install", "--maintenance-update"])

    app_module.main()

    assert repair_calls == [], (
        "main() must not call attempt_obligation_repair() for `install` invocations"
    )


def test_main_version_argv_skips_obligation_repair_to_avoid_probe_recursion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The repair helper's version probe must not recursively repair itself."""
    app_module = importlib.import_module("autoskillit.cli.app")

    monkeypatch.setattr(app_module, "app", lambda: None)
    monkeypatch.setattr(
        "autoskillit.cli._init_helpers.evict_direct_mcp_entry", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.run_update_checks", lambda **kwargs: None
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    repair_calls: list[Path] = []
    monkeypatch.setattr(
        "autoskillit.cli.update._obligation_repair.attempt_obligation_repair",
        lambda home, **kwargs: repair_calls.append(home),
    )
    monkeypatch.setattr(sys, "argv", ["autoskillit", "--version"])

    app_module.main()

    assert repair_calls == []


def test_main_non_install_argv_still_calls_obligation_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """main() still observes a pending obligation for normal subcommands."""
    app_module = importlib.import_module("autoskillit.cli.app")

    monkeypatch.setattr(app_module, "app", lambda: None)
    monkeypatch.setattr(
        "autoskillit.cli._init_helpers.evict_direct_mcp_entry", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.run_update_checks", lambda **kwargs: None
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    repair_calls: list[Path] = []

    def fake_repair(home: Path, **kwargs: object) -> MagicMock:
        repair_calls.append(home)
        return MagicMock(outcome="no_obligation", findings=())

    monkeypatch.setattr(
        "autoskillit.cli.update._obligation_repair.attempt_obligation_repair",
        fake_repair,
    )
    monkeypatch.setattr(sys, "argv", ["autoskillit", "doctor"])

    app_module.main()

    assert repair_calls == [tmp_path]


def test_main_repair_diagnostics_never_write_stdout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_module = importlib.import_module("autoskillit.cli.app")
    from autoskillit.cli.update._obligation_repair import ObligationRepairOutcome

    monkeypatch.setattr(app_module, "app", lambda: None)
    monkeypatch.setattr(
        "autoskillit.cli._init_helpers.evict_direct_mcp_entry",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.run_update_checks",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["autoskillit", "doctor", "--json"])
    monkeypatch.setattr(
        "autoskillit.cli.update._obligation_repair.attempt_obligation_repair",
        lambda _home: MagicMock(
            outcome=ObligationRepairOutcome.DEFERRED,
            findings=("run install externally",),
        ),
    )
    warnings: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        app_module.logger,
        "warning",
        lambda event, **kwargs: warnings.append((event, kwargs)),
    )

    app_module.main()

    assert capsys.readouterr().out == ""
    assert warnings == [
        (
            "publication_obligation_repair_incomplete",
            {
                "outcome": ObligationRepairOutcome.DEFERRED.value,
                "finding": "run install externally",
            },
        )
    ]


def test_main_repair_classifies_missing_expected_version_as_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """MISSING_EXPECTED_VERSION is auto-classified as an incomplete repair.

    The caller uses an exclusion-based check (`outcome not in {NO_OBLIGATION,
    CLEARED}`) so any new outcome — including the new MISSING_EXPECTED_VERSION
    for stale obligations — is treated as an incomplete repair that warrants
    the warning emission. This test pins the contract against future refactors
    that might enumerate specific outcomes.
    """
    app_module = importlib.import_module("autoskillit.cli.app")
    from autoskillit.cli.update._obligation_repair import ObligationRepairOutcome

    monkeypatch.setattr(app_module, "app", lambda: None)
    monkeypatch.setattr(
        "autoskillit.cli._init_helpers.evict_direct_mcp_entry",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.run_update_checks",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["autoskillit", "doctor", "--json"])
    monkeypatch.setattr(
        "autoskillit.cli.update._obligation_repair.attempt_obligation_repair",
        lambda _home: MagicMock(
            outcome=ObligationRepairOutcome.MISSING_EXPECTED_VERSION,
            findings=("obligation_stale: expected 0.9.0, observed 1.1.0",),
        ),
    )
    warnings: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        app_module.logger,
        "warning",
        lambda event, **kwargs: warnings.append((event, kwargs)),
    )

    app_module.main()

    assert capsys.readouterr().out == ""
    assert warnings == [
        (
            "publication_obligation_repair_incomplete",
            {
                "outcome": ObligationRepairOutcome.MISSING_EXPECTED_VERSION.value,
                "finding": "obligation_stale: expected 0.9.0, observed 1.1.0",
            },
        )
    ]


def test_serve_activity_check_uses_backend_derived_marker_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """serve() must derive _marker_dir via backend.session_locator().project_log_dir()."""
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

    assert len(captured_activity_check) == 1, "serve_with_signal_guard was not called exactly once"

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
