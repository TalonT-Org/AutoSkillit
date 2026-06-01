from __future__ import annotations

from unittest.mock import Mock

import pytest

from autoskillit.core._version_snapshot import collect_version_snapshot

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _clear_snapshot_cache():
    collect_version_snapshot.cache_clear()
    yield
    collect_version_snapshot.cache_clear()


def test_codex_version_populated_with_codex_backend() -> None:
    backend = Mock()
    backend.name = "codex"
    backend.version.return_value = "1.2.3"
    backend.list_plugins.return_value = []
    result = collect_version_snapshot(backend)
    assert result["codex_version"] == "1.2.3"


def test_codex_version_empty_with_claude_code_backend() -> None:
    backend = Mock()
    backend.name = "claude-code"
    backend.version.return_value = "1.0"
    backend.list_plugins.return_value = []
    result = collect_version_snapshot(backend)
    assert result["codex_version"] == ""


def test_codex_version_empty_when_no_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.core._version_snapshot as mod

    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)

    def _no_codex_call(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("codex should not be called when backend is unset")

    monkeypatch.setattr(mod.subprocess, "run", _no_codex_call)
    result = collect_version_snapshot()
    assert result["codex_version"] == ""


def test_no_cross_contamination_claude_version_empty_for_codex() -> None:
    backend = Mock()
    backend.name = "codex"
    backend.version.return_value = "1.2.3"
    backend.list_plugins.return_value = []
    result = collect_version_snapshot(backend)
    assert result["claude_code_version"] == ""


def test_codex_version_key_present_in_snapshot() -> None:
    backend = Mock()
    backend.name = "codex"
    backend.version.return_value = "1.0"
    backend.list_plugins.return_value = []
    result = collect_version_snapshot(backend)
    assert "codex_version" in result
