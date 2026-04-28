"""Tests: fleet CLI list command."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.cli._fleet import fleet_list as _fleet_list
from tests.cli._fleet_helpers import _setup_campaign_recipes

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium, pytest.mark.feature("fleet")]


def test_fleet_list_no_campaigns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """fleet list prints 'No campaigns found' when directory is empty."""
    monkeypatch.chdir(tmp_path)
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
