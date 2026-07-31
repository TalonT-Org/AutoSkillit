"""Pure platform path resolution for the GitHub review mutation ledger."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import github_review_ledger_path

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_linux_uses_xdg_state_home_without_touching_filesystem(tmp_path: Path) -> None:
    xdg_state = tmp_path / "state"
    result = github_review_ledger_path(
        home=tmp_path / "home",
        environ={"XDG_STATE_HOME": str(xdg_state)},
        platform="linux",
    )
    assert result == xdg_state / "autoskillit" / "github-review" / "ledger.sqlite3"
    assert not xdg_state.exists()


def test_linux_falls_back_to_home_local_state(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = github_review_ledger_path(home=home, environ={}, platform="linux")
    assert result == home / ".local" / "state" / "autoskillit" / "github-review" / "ledger.sqlite3"


def test_darwin_uses_application_support_and_ignores_xdg(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = github_review_ledger_path(
        home=home,
        environ={"XDG_STATE_HOME": str(tmp_path / "ignored")},
        platform="darwin",
    )
    assert result == (
        home
        / "Library"
        / "Application Support"
        / "autoskillit"
        / "github-review"
        / "ledger.sqlite3"
    )


def test_explicit_arguments_make_path_resolution_environment_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "ambient"))
    expected = github_review_ledger_path(
        home=tmp_path / "explicit-home",
        environ={},
        platform="linux",
    )
    assert expected.is_absolute()
    assert "ambient" not in str(expected)
