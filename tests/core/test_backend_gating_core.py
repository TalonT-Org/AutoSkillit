"""Backend gating tests for core/_version_snapshot.py.

Verify that _claude_code_version and _plugins return empty fallbacks
for non-claude-code backends without performing any I/O.
"""

from __future__ import annotations

import json

import pytest

from autoskillit.core._version_snapshot import (
    _claude_code_version,
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


def test_plugins_returns_empty_for_non_claude_code_backend(monkeypatch, tmp_path):
    import autoskillit.core._version_snapshot as mod

    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "headless")
    plugins_dir = tmp_path / ".claude" / "plugins"
    plugins_dir.mkdir(parents=True)
    plugin_data = {"version": 2, "plugins": {"some-ref": [{"version": "1.0"}]}}
    (plugins_dir / "installed_plugins.json").write_text(json.dumps(plugin_data), encoding="utf-8")
    monkeypatch.setattr(mod.Path, "home", classmethod(lambda cls: tmp_path))
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
