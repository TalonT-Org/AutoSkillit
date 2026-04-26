"""Tests for the features CLI subcommand."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


# ---------------------------------------------------------------------------
# T_FEATURES_1: features list shows all registered features
# ---------------------------------------------------------------------------


def test_features_list_shows_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """features list output includes all registered feature names and lifecycle values."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "autoskillit.config.load_config",
        lambda path: type("C", (), {"features": {}, "experimental_enabled": False})(),
    )
    from autoskillit.cli._features import features_list

    features_list()
    out = capsys.readouterr().out
    assert "fleet" in out
    assert "experimental" in out
    assert "FEATURE" in out
    assert "LIFECYCLE" in out
    assert "EFFECTIVE" in out
    fleet_line = next(
        line for line in out.splitlines() if "fleet" in line and "FEATURE" not in line
    )
    assert len(fleet_line.split()) >= 6


# ---------------------------------------------------------------------------
# T_FEATURES_2: effective column reflects config override
# ---------------------------------------------------------------------------


def test_features_list_shows_effective_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """features list shows effective=false and source=config when feature is overridden."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "autoskillit.config.load_config",
        lambda path: type(
            "C", (), {"features": {"fleet": False}, "experimental_enabled": False}
        )(),
    )
    from autoskillit.cli._features import features_list

    features_list()
    out = capsys.readouterr().out
    # fleet row should show effective=false and source=config
    fleet_line = next(line for line in out.splitlines() if "fleet" in line)
    assert "false" in fleet_line
    assert "config" in fleet_line


# ---------------------------------------------------------------------------
# T_FEATURES_3: features status <name> shows all FeatureDef fields
# ---------------------------------------------------------------------------


def test_features_status_detail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """features status fleet shows all FeatureDef fields."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "autoskillit.config.load_config",
        lambda path: type("C", (), {"features": {}, "experimental_enabled": False})(),
    )
    from autoskillit.cli._features import features_status

    features_status("fleet")
    out = capsys.readouterr().out
    assert "fleet" in out
    assert "experimental" in out  # lifecycle
    tier_line = next(line for line in out.splitlines() if "Tier" in line)
    assert "1" in tier_line  # tier value on its own line
    assert "false" in out  # disabled (default_enabled=False for fleet)


# ---------------------------------------------------------------------------
# T_FEATURES_4: features status unknown name exits 1
# ---------------------------------------------------------------------------


def test_features_status_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """features status exits 1 for an unknown feature name."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "autoskillit.config.load_config",
        lambda path: type("C", (), {"features": {}, "experimental_enabled": False})(),
    )
    from autoskillit.cli._features import features_status

    with pytest.raises(SystemExit) as exc_info:
        features_status("nonexistent-feature")
    assert exc_info.value.code == 1
