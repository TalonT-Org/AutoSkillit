"""Tests for core/_version_snapshot.py."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import Mock

import pytest

from autoskillit.core._version_snapshot import collect_version_snapshot

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _clear_snapshot_cache():
    collect_version_snapshot.cache_clear()
    yield
    collect_version_snapshot.cache_clear()


def test_collect_version_snapshot_returns_required_keys():
    result = collect_version_snapshot()
    assert set(result.keys()) == {
        "autoskillit_version",
        "install_type",
        "commit_id",
        "claude_code_version",
        "plugins",
        "codex_version",
        "codex_plugins",
    }


def test_collect_version_snapshot_is_cached():
    first = collect_version_snapshot()
    second = collect_version_snapshot()
    assert first is second


def test_collect_version_snapshot_cache_clear_works():
    first = collect_version_snapshot()
    collect_version_snapshot.cache_clear()
    second = collect_version_snapshot()
    assert first is not second


def test_autoskillit_version_is_nonempty_string():
    result = collect_version_snapshot()
    assert isinstance(result["autoskillit_version"], str)
    assert result["autoskillit_version"] != ""


def test_claude_code_version_graceful_on_subprocess_error(monkeypatch):
    import autoskillit.core._version_snapshot as mod

    def _raise(*args, **kwargs):
        raise FileNotFoundError("claude not found")

    monkeypatch.setattr(mod.subprocess, "run", _raise)
    result = collect_version_snapshot()
    assert result["claude_code_version"] == ""


def test_claude_code_version_graceful_on_timeout(monkeypatch):
    import autoskillit.core._version_snapshot as mod

    def _raise(*args, **kwargs):
        raise subprocess.TimeoutExpired("claude", 5)

    monkeypatch.setattr(mod.subprocess, "run", _raise)
    result = collect_version_snapshot()
    assert result["claude_code_version"] == ""


def test_plugins_graceful_when_file_absent(monkeypatch, tmp_path):
    import autoskillit.core._version_snapshot as mod

    monkeypatch.setattr(mod.Path, "home", classmethod(lambda cls: tmp_path))
    result = collect_version_snapshot()
    assert result["plugins"] == []


def test_plugins_reads_version(monkeypatch, tmp_path):
    import autoskillit.core._version_snapshot as mod

    plugins_dir = tmp_path / ".claude" / "plugins"
    plugins_dir.mkdir(parents=True)
    plugin_data = {
        "version": 2,
        "plugins": {
            "ref": [{"version": "1.0", "extra": "ignored"}],
        },
    }
    (plugins_dir / "installed_plugins.json").write_text(json.dumps(plugin_data), encoding="utf-8")
    monkeypatch.setattr(mod.Path, "home", classmethod(lambda cls: tmp_path))
    result = collect_version_snapshot()
    assert len(result["plugins"]) == 1
    entry = result["plugins"][0]
    assert entry["ref"] == "ref"
    assert entry["version"] == "1.0"
    assert "name" not in entry


def test_plugins_graceful_on_corrupt_json(monkeypatch, tmp_path):
    import autoskillit.core._version_snapshot as mod

    plugins_dir = tmp_path / ".claude" / "plugins"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "installed_plugins.json").write_text("not valid json", encoding="utf-8")
    monkeypatch.setattr(mod.Path, "home", classmethod(lambda cls: tmp_path))
    result = collect_version_snapshot()
    assert result["plugins"] == []


def test_claude_code_version_skipped_for_codex_backend():
    backend = Mock()
    backend.name = "codex"
    backend.version.return_value = "0.5.0"
    backend.list_plugins.return_value = []
    result = collect_version_snapshot(backend)
    assert result["claude_code_version"] == ""


def test_plugins_skipped_for_codex_backend():
    backend = Mock()
    backend.name = "codex"
    backend.version.return_value = "0.5.0"
    backend.list_plugins.return_value = []
    result = collect_version_snapshot(backend)
    assert result["plugins"] == []


def test_subprocess_path_exercised_without_backend_env(monkeypatch):
    import autoskillit.core._version_snapshot as mod

    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
    called = []

    def _fake_run(*args, **kwargs):
        called.append(args)
        raise FileNotFoundError("claude not found")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    result = collect_version_snapshot()
    assert result["claude_code_version"] == ""
    assert len(called) == 1


def test_lru_cache_maxsize_is_four():
    assert hasattr(collect_version_snapshot, "__wrapped__")
    info = collect_version_snapshot.cache_info()
    assert info.maxsize == 4


def test_backend_dispatch_populates_correct_keys():
    claude_backend = Mock()
    claude_backend.name = "claude-code"
    claude_backend.version.return_value = "2.0"
    claude_backend.list_plugins.return_value = [{"ref": "p"}]
    r1 = collect_version_snapshot(claude_backend)
    assert r1["claude_code_version"] == "2.0"

    codex_backend = Mock()
    codex_backend.name = "codex"
    codex_backend.version.return_value = "1.5"
    codex_backend.list_plugins.return_value = [{"ref": "q"}]
    r2 = collect_version_snapshot(codex_backend)
    assert r2["codex_version"] == "1.5"
