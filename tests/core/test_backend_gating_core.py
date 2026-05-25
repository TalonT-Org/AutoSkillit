"""Backend gating tests for core/_version_snapshot.py.

Verify that _claude_code_version and _plugins return empty fallbacks
for non-claude-code backends without performing any I/O.
"""

from __future__ import annotations

import subprocess

import pytest

from autoskillit.core._version_snapshot import (
    _claude_code_version,
    _codex_plugins,
    _codex_version,
    _plugins,
    collect_version_snapshot,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _clear_snapshot_cache():
    collect_version_snapshot.cache_clear()
    yield
    collect_version_snapshot.cache_clear()


def test_claude_code_version_returns_empty_for_non_claude_code_backend(monkeypatch):
    import autoskillit.core._version_snapshot as mod

    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "headless")

    def _no_call(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr(mod.subprocess, "run", _no_call)
    assert _claude_code_version() == ""


def test_plugins_returns_empty_for_non_claude_code_backend(monkeypatch):
    import autoskillit.core._version_snapshot as mod

    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "headless")

    def _no_read(*args, **kwargs):
        raise AssertionError("Path.home should not be called")

    monkeypatch.setattr(mod.Path, "home", staticmethod(_no_read))
    assert _plugins() == []


def test_collect_version_snapshot_skips_claude_fields(monkeypatch):
    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "headless")
    result = collect_version_snapshot()
    assert result["claude_code_version"] == ""
    assert result["plugins"] == []


def test_claude_code_version_still_runs_for_claude_code_backend(monkeypatch):
    import autoskillit.core._version_snapshot as mod

    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
    called = []

    def _fake_run(*args, **kwargs):
        called.append(args)
        raise FileNotFoundError("claude not found")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    _claude_code_version()
    assert len(called) == 1


def test_codex_version_returns_empty_for_non_codex_backend(monkeypatch):
    import autoskillit.core._version_snapshot as mod

    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "headless")

    def _no_call(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr(mod.subprocess, "run", _no_call)
    assert _codex_version() == ""


def test_codex_version_graceful_on_timeout(monkeypatch):
    import autoskillit.core._version_snapshot as mod

    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "codex")

    def _raise(*args, **kwargs):
        raise subprocess.TimeoutExpired("codex", 5)

    monkeypatch.setattr(mod.subprocess, "run", _raise)
    assert _codex_version() == ""


def test_codex_version_graceful_on_file_not_found_for_codex_backend(monkeypatch):
    import autoskillit.core._version_snapshot as mod

    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "codex")

    def _raise(*args, **kwargs):
        raise FileNotFoundError("codex not found")

    monkeypatch.setattr(mod.subprocess, "run", _raise)
    assert _codex_version() == ""


def test_codex_plugins_returns_empty_for_non_codex_backend(monkeypatch):
    import autoskillit.core._version_snapshot as mod

    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "headless")

    def _no_call(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr(mod.subprocess, "run", _no_call)
    assert _codex_plugins() == []


def test_codex_plugins_graceful_on_subprocess_error(monkeypatch):
    import autoskillit.core._version_snapshot as mod

    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "codex")

    def _raise(*args, **kwargs):
        raise FileNotFoundError("codex not found")

    monkeypatch.setattr(mod.subprocess, "run", _raise)
    assert _codex_plugins() == []


def test_codex_version_not_called_without_backend_env(monkeypatch):
    import autoskillit.core._version_snapshot as mod

    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
    called = []

    def _fake_run(*args, **kwargs):
        called.append(args)
        raise FileNotFoundError("codex not found")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    assert _codex_version() == ""
    assert len(called) == 0
