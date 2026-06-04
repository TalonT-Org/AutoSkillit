"""Tests for core/_version_snapshot.py — Protocol dispatch and env-var fallback."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from autoskillit.core._version_snapshot import collect_version_snapshot

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _make_backend(name: str, version: str = "", plugins: list | None = None) -> Mock:
    """Create a mock CodingAgentBackend with the given identity and return values."""
    mock = Mock()
    mock.name = name
    mock.version.return_value = version
    mock.list_plugins.return_value = plugins if plugins is not None else []
    return mock


def test_claude_backend_populates_version_and_plugins():
    backend = _make_backend("claude-code", version="1.2.3", plugins=[{"ref": "p"}])
    result = collect_version_snapshot(backend)
    assert result["claude_code_version"] == "1.2.3"
    assert result["plugins"] == [{"ref": "p"}]
    assert result["codex_version"] == ""
    assert result["codex_plugins"] == []


def test_codex_backend_populates_version_and_plugins():
    backend = _make_backend("codex", version="0.5.0", plugins=[{"ref": "q"}])
    result = collect_version_snapshot(backend)
    assert result["codex_version"] == "0.5.0"
    assert result["codex_plugins"] == [{"ref": "q"}]
    assert result["claude_code_version"] == ""
    assert result["plugins"] == []


def test_backend_version_error_returns_empty():
    backend = Mock()
    backend.name = "claude-code"
    backend.version.side_effect = Exception("boom")
    backend.list_plugins.return_value = []
    result = collect_version_snapshot(backend)
    assert result["claude_code_version"] == ""


def test_backend_list_plugins_error_returns_empty():
    backend = Mock()
    backend.name = "claude-code"
    backend.version.return_value = "1.0"
    backend.list_plugins.side_effect = Exception("boom")
    result = collect_version_snapshot(backend)
    assert result["plugins"] == []


def test_unknown_backend_name_all_zero_values():
    backend = _make_backend("unknown", version="1.0", plugins=[{"ref": "x"}])
    result = collect_version_snapshot(backend)
    assert result["claude_code_version"] == ""
    assert result["plugins"] == []
    assert result["codex_version"] == ""
    assert result["codex_plugins"] == []


def test_none_backend_falls_back_to_env_path(monkeypatch):
    import autoskillit.core._version_snapshot as mod

    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)

    called = []

    def _fake_run(*args, **kwargs):
        called.append(args)
        raise FileNotFoundError("claude not found")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    result = collect_version_snapshot()
    assert len(called) >= 1
    assert result["claude_code_version"] == ""


def test_no_subprocess_call_with_protocol_backend(monkeypatch):
    import autoskillit.core._version_snapshot as mod

    def _no_call(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr(mod.subprocess, "run", _no_call)
    backend = _make_backend("claude-code", version="1.0", plugins=[])
    result = collect_version_snapshot(backend)
    assert result["claude_code_version"] == "1.0"
