"""Tests for cli/_ansi.py terminal color utilities."""

from __future__ import annotations

import pytest

from autoskillit.cli._ansi import supports_color


def test_supports_color_respects_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """NO_COLOR env var disables color."""
    monkeypatch.setenv("NO_COLOR", "1")
    assert not supports_color()


def test_supports_color_respects_dumb_term(monkeypatch: pytest.MonkeyPatch) -> None:
    """TERM=dumb disables color."""
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert not supports_color()


def test_supports_color_false_when_not_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-TTY stdout returns False."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    # In test runners stdout is typically not a tty
    assert not supports_color()
