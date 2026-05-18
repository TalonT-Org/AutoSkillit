"""Tests for hook drift false-positive fix when plugin is marketplace-installed.

Site 1 coverage: _hooks_signal() in cli/update/_update_checks.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.hook_registry import HookDriftResult

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def test_hooks_signal_silent_when_plugin_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Site 1: _hooks_signal() returns None when plugin is installed (missing drift suppressed)."""
    from autoskillit.cli.update._update_checks import _hooks_signal

    monkeypatch.setattr("autoskillit.cli._init_helpers._is_plugin_installed", lambda **_: True)
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')

    sig = _hooks_signal(settings)
    assert sig is None


def test_hooks_signal_orphaned_still_fires_when_plugin_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Site 1: orphaned hook detection remains unconditional when plugin is installed."""
    from autoskillit.cli.update._update_checks import _hooks_signal

    monkeypatch.setattr("autoskillit.cli._init_helpers._is_plugin_installed", lambda **_: True)
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks._count_hook_registry_drift",
        lambda _path: HookDriftResult(missing=5, orphaned=2),
    )
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')

    sig = _hooks_signal(settings)
    assert sig is not None
    assert sig.kind == "hooks"
    assert "orphaned" in sig.message.lower()
