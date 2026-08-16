"""Tests for the cli/_marketplace.py module."""

from __future__ import annotations

import pytest

from autoskillit import __version__
from autoskillit.cli._install_contract import InstallMode, InstallRequest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _direct_request() -> InstallRequest:
    return InstallRequest(
        scope="user",
        mode=InstallMode.DIRECT,
        require_registered_plugin=True,
        expected_version=__version__,
    )


def test_install_requires_typed_request() -> None:
    from autoskillit.cli._marketplace import install

    with pytest.raises(TypeError, match="request"):
        install()  # type: ignore[call-arg]


class TestInstallPluginInstallCapableGuard:
    def test_rejects_when_plugin_install_not_capable(self, monkeypatch):
        """install() returns a typed decline when capability is false."""
        from unittest.mock import MagicMock

        from autoskillit.cli import _marketplace
        from autoskillit.cli._install_contract import InstallOutcome

        mock_cfg = MagicMock()
        mock_cfg.agent_backend.backend = "some-backend"
        monkeypatch.setattr("autoskillit.config.load_config", lambda _: mock_cfg)

        mock_backend = MagicMock()
        mock_backend.capabilities.plugin_install_capable = False
        monkeypatch.setattr("autoskillit.execution.get_backend", lambda name: mock_backend)
        monkeypatch.setattr(_marketplace, "is_git_worktree", lambda _path: False)

        from autoskillit.cli._marketplace import install

        result = install(request=_direct_request())

        assert result.outcome is InstallOutcome.DECLINED
        assert "plugin_install_capable" in result.findings[0]

    def test_passes_guard_when_plugin_install_capable(self, monkeypatch):
        """install() reaches the typed session deferral when capability is true."""
        from unittest.mock import MagicMock

        from autoskillit.cli import _marketplace
        from autoskillit.cli._install_contract import InstallOutcome

        mock_cfg = MagicMock()
        mock_cfg.agent_backend.backend = "claude-code"
        monkeypatch.setattr("autoskillit.config.load_config", lambda _: mock_cfg)

        mock_backend = MagicMock()
        mock_backend.capabilities.plugin_install_capable = True
        monkeypatch.setattr("autoskillit.execution.get_backend", lambda name: mock_backend)
        monkeypatch.setattr(_marketplace, "is_git_worktree", lambda _path: False)

        # Stub CLAUDECODE env to trigger the next guard (deferred exit)
        monkeypatch.setenv("CLAUDECODE", "1")

        from autoskillit.cli._marketplace import install

        result = install(request=_direct_request())

        assert result.outcome is InstallOutcome.DEFERRED
        assert all("plugin_install_capable" not in finding for finding in result.findings)
