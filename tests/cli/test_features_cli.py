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
        "autoskillit.config.load_config", lambda path: type("C", (), {"features": {}})()
    )
    from autoskillit.cli._features import features_list

    features_list()
    out = capsys.readouterr().out
    assert "franchise" in out
    assert "experimental" in out


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
        lambda path: type("C", (), {"features": {"franchise": False}})(),
    )
    from autoskillit.cli._features import features_list

    features_list()
    out = capsys.readouterr().out
    # franchise row should show effective=false and source=config
    franchise_line = next(line for line in out.splitlines() if "franchise" in line)
    assert "false" in franchise_line
    assert "config" in franchise_line


# ---------------------------------------------------------------------------
# T_FEATURES_3: features status <name> shows all FeatureDef fields
# ---------------------------------------------------------------------------


def test_features_status_detail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """features status franchise shows all FeatureDef fields."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "autoskillit.config.load_config", lambda path: type("C", (), {"features": {}})()
    )
    from autoskillit.cli._features import features_status

    features_status("franchise")
    out = capsys.readouterr().out
    assert "franchise" in out
    assert "experimental" in out  # lifecycle
    tier_line = next(line for line in out.splitlines() if "Tier" in line)
    assert "1" in tier_line  # tier value on its own line
    assert "true" in out  # enabled (default_enabled=True for franchise)


# ---------------------------------------------------------------------------
# T_FEATURES_4: features status unknown name exits 1
# ---------------------------------------------------------------------------


def test_features_status_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """features status exits 1 for an unknown feature name."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "autoskillit.config.load_config", lambda path: type("C", (), {"features": {}})()
    )
    from autoskillit.cli._features import features_status

    with pytest.raises(SystemExit) as exc_info:
        features_status("nonexistent-feature")
    assert exc_info.value.code == 1
