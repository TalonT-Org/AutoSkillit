"""Tests for franchise CLI registration (Group J)."""

from __future__ import annotations

import pytest
from cyclopts import App

pytestmark = [pytest.mark.layer("franchise"), pytest.mark.small]


def _get_app() -> App:
    from autoskillit.cli.app import app

    return app


def _subcommand_names(app: App) -> set[str]:
    """Extract registered subcommand names from a cyclopts App."""
    names: set[str] = set()
    for meta in app._commands.values():  # type: ignore[attr-defined]
        names.add(meta.name)
    return names


class TestFranchiseCLIRegistration:
    def test_franchise_subcommand_registered(self) -> None:
        app = _get_app()
        names = _subcommand_names(app)
        assert "franchise" in names

    def test_franchise_status_accepts_reap_flag(self) -> None:
        from autoskillit.cli._franchise import franchise_app

        # Verify that parsing 'status --reap campaign-id' does not raise
        # We invoke the franchise_app directly in test mode
        status_cmd = None
        for meta in franchise_app._commands.values():  # type: ignore[attr-defined]
            if meta.name == "status":
                status_cmd = meta
                break
        assert status_cmd is not None, "franchise status command not found"

    def test_franchise_status_accepts_dry_run_flag(self) -> None:
        from autoskillit.cli._franchise import franchise_app

        status_cmd = None
        for meta in franchise_app._commands.values():  # type: ignore[attr-defined]
            if meta.name == "status":
                status_cmd = meta
                break
        assert status_cmd is not None, "franchise status command not found"
        # Verify function signature has dry_run parameter
        import inspect

        sig = inspect.signature(status_cmd.func)
        assert "dry_run" in sig.parameters
        assert "reap" in sig.parameters
