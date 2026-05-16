"""Unit tests for _is_plugin_installed backend guard."""

from __future__ import annotations

import subprocess

import pytest

from autoskillit.cli._init_helpers import _is_plugin_installed

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


class TestIsPluginInstalledBackendGuard:
    """Backend guard returns False without subprocess for non-claude-code."""

    def test_non_claude_code_backend_returns_false(self):
        result = _is_plugin_installed(agent_backend="aider")
        assert result is False

    def test_claude_code_backend_calls_subprocess(self, monkeypatch):
        called = []

        def fake_run(*args, **kwargs):
            called.append(args)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="autoskillit\n")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _is_plugin_installed(agent_backend="claude-code")
        assert result is True
        assert len(called) == 1
