"""Tests for franchise CLI registration (Group J)."""

from __future__ import annotations

import pytest
from cyclopts import App

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _get_app() -> App:
    from autoskillit.cli.app import app

    return app


def _subcommand_names(app: App) -> set[str]:
    """Extract registered subcommand names from a cyclopts App."""
    names: set[str] = set()
    for meta in app._commands.values():  # type: ignore[attr-defined]
        name = meta.name
        if isinstance(name, tuple):
            names.update(name)
        elif isinstance(name, str):
            names.add(name)
    return names


def _find_command(app: App, name: str) -> object:
    """Find a command meta by command key (the registered name string)."""
    return app._commands.get(name)  # type: ignore[attr-defined]


class TestFranchiseCLIRegistration:
    def test_franchise_subcommand_registered(self) -> None:
        app = _get_app()
        names = _subcommand_names(app)
        assert "franchise" in names

    def test_franchise_status_accepts_reap_flag(self) -> None:
        from autoskillit.cli._franchise import franchise_app

        status_cmd = _find_command(franchise_app, "status")
        assert status_cmd is not None, "franchise status command not found"

    def test_franchise_status_accepts_dry_run_flag(self) -> None:
        import inspect

        from autoskillit.cli._franchise import franchise_status

        sig = inspect.signature(franchise_status)
        assert "dry_run" in sig.parameters
        assert "reap" in sig.parameters
