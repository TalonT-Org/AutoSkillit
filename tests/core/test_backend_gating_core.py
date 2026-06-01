"""Backend gating tests for core/_version_snapshot.py.

Verify that collect_version_snapshot() dispatches to the supplied backend's
``version()`` and ``list_plugins()`` methods, that the appropriate keys are
populated per backend, and that subprocess/filesystem are not touched when a
backend is provided.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from autoskillit.core import _version_snapshot as mod
from autoskillit.core._version_snapshot import collect_version_snapshot

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _clear_snapshot_cache():
    collect_version_snapshot.cache_clear()
    yield
    collect_version_snapshot.cache_clear()


def _make_backend(name: str, version: str = "", plugins: list | None = None) -> MagicMock:
    b = MagicMock()
    b.name = name
    b.version.return_value = version
    b.list_plugins.return_value = plugins if plugins is not None else []
    return b


def test_claude_backend_populates_claude_keys():
    backend = _make_backend("claude-code", "1.2.3", [{"ref": "p1"}])
    result = collect_version_snapshot(backend)
    assert result["claude_code_version"] == "1.2.3"
    assert result["plugins"] == [{"ref": "p1"}]
    assert result["codex_version"] == ""
    assert result["codex_plugins"] == []


def test_codex_backend_populates_codex_keys():
    backend = _make_backend("codex", "4.5.6", [{"ref": "p2"}])
    result = collect_version_snapshot(backend)
    assert result["codex_version"] == "4.5.6"
    assert result["codex_plugins"] == [{"ref": "p2"}]
    assert result["claude_code_version"] == ""
    assert result["plugins"] == []


def test_claude_backend_no_cross_contamination():
    backend = _make_backend("claude-code", "1.2.3", [{"ref": "p1"}])
    result = collect_version_snapshot(backend)
    assert result["codex_version"] == ""
    assert result["codex_plugins"] == []


def test_codex_backend_no_cross_contamination():
    backend = _make_backend("codex", "4.5.6", [{"ref": "p2"}])
    result = collect_version_snapshot(backend)
    assert result["claude_code_version"] == ""
    assert result["plugins"] == []


def test_unknown_backend_name_logs_warning(caplog):
    backend = _make_backend("unknown", "1.0", [{"ref": "p1"}])
    with caplog.at_level(logging.WARNING, logger="autoskillit.core._version_snapshot"):
        result = collect_version_snapshot(backend)
    assert result["claude_code_version"] == ""
    assert result["plugins"] == []
    assert result["codex_version"] == ""
    assert result["codex_plugins"] == []
    assert any("Unknown backend name 'unknown'" in rec.message for rec in caplog.records)


def test_backend_version_called_exactly_once():
    backend = _make_backend("claude-code", "1.2.3")
    collect_version_snapshot(backend)
    assert backend.version.call_count == 1


def test_backend_list_plugins_called_exactly_once():
    backend = _make_backend("claude-code", "1.2.3", [{"ref": "p1"}])
    collect_version_snapshot(backend)
    assert backend.list_plugins.call_count == 1


def test_private_helpers_not_importable():
    for name in ("_claude_code_version", "_codex_version", "_codex_plugins", "_plugins"):
        assert name not in dir(mod)
    for name in ("_claude_code_version", "_codex_version", "_codex_plugins", "_plugins"):
        with pytest.raises(ImportError):
            __import__(f"autoskillit.core._version_snapshot.{name}", fromlist=[name])


def test_no_subprocess_called_with_backend(monkeypatch):
    backend = _make_backend("claude-code", "1.2.3", [{"ref": "p1"}])

    def _no_call(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr(mod.subprocess, "run", _no_call)
    result = collect_version_snapshot(backend)
    assert result["claude_code_version"] == "1.2.3"


def test_no_filesystem_read_with_backend(monkeypatch):
    backend = _make_backend("claude-code", "1.2.3", [{"ref": "p1"}])

    def _no_read(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("Path.home should not be called")

    monkeypatch.setattr(mod.Path, "home", staticmethod(_no_read))
    result = collect_version_snapshot(backend)
    assert result["plugins"] == [{"ref": "p1"}]
