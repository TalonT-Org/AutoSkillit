"""Tests: fleet CLI list command."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.cli.fleet import fleet_list as _fleet_list
from tests.cli._fleet_helpers import (
    _find_command,
    _get_app,
    _setup_campaign_recipes,
    _subcommand_names,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium, pytest.mark.feature("fleet")]


def test_fleet_list_no_campaigns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """fleet list prints 'No campaigns found' when directory is empty and no builtins exist."""
    import autoskillit.recipe.io as _rio

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_rio, "pkg_root", lambda: tmp_path)
    _fleet_list()
    assert "No campaigns found" in capsys.readouterr().out


def test_fleet_list_shows_campaigns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """fleet list shows tabular campaign listing."""
    _setup_campaign_recipes(tmp_path, ["alpha", "beta"])
    monkeypatch.chdir(tmp_path)
    _fleet_list()
    output = capsys.readouterr().out
    assert "alpha" in output
    assert "beta" in output


def test_fleet_list_exits_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fleet_list exits 1 when fleet feature is disabled via feature gate."""
    monkeypatch.chdir(tmp_path)
    checked_features: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli._fleet.is_feature_enabled",
        lambda name, features, *, experimental_enabled=False: (
            checked_features.append(name) or False
        ),
    )
    monkeypatch.setattr(
        "autoskillit.config.load_config",
        lambda path: type("C", (), {"features": {}, "experimental_enabled": False})(),
    )
    with pytest.raises(SystemExit) as exc_info:
        _fleet_list()
    assert exc_info.value.code == 1
    assert "fleet" in checked_features


# ---------------------------------------------------------------------------
# CLI registration tests (fleet surface, not list-specific)
# ---------------------------------------------------------------------------


class TestFleetCLIRegistration:
    def test_fleet_subcommand_registered(self) -> None:
        app = _get_app()
        names = _subcommand_names(app)
        assert "fleet" in names

    def test_fleet_status_accepts_reap_flag(self) -> None:
        from autoskillit.cli.fleet import fleet_app

        status_cmd = _find_command(fleet_app, "status")
        assert status_cmd is not None, "fleet status command not found"

    def test_fleet_status_accepts_dry_run_flag(self) -> None:
        import inspect

        from autoskillit.cli.fleet import fleet_status

        sig = inspect.signature(fleet_status)
        assert "dry_run" in sig.parameters
        assert "reap" in sig.parameters

    def test_fleet_dispatch_command_registered(self) -> None:
        from autoskillit.cli.fleet import fleet_app

        assert "dispatch" in _subcommand_names(fleet_app)

    def test_fleet_campaign_command_registered(self) -> None:
        from autoskillit.cli.fleet import fleet_app

        assert "campaign" in _subcommand_names(fleet_app)

    def test_fleet_run_command_not_registered(self) -> None:
        from autoskillit.cli.fleet import fleet_app

        assert "run" not in _subcommand_names(fleet_app)
