"""Tests verifying collect_version_snapshot() codex routing via mock backends."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

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


def test_codex_version_populated_with_codex_backend():
    backend = _make_backend("codex", "1.2.3", [])
    result = collect_version_snapshot(backend)
    assert result["codex_version"] == "1.2.3"


def test_codex_version_empty_with_claude_backend():
    backend = _make_backend("claude-code", "1.0", [])
    result = collect_version_snapshot(backend)
    assert result["codex_version"] == ""


def test_codex_version_empty_with_no_backend():
    result = collect_version_snapshot()
    assert result["codex_version"] == ""


def test_no_cross_contamination_claude_version_empty_for_codex_backend():
    backend = _make_backend("codex", "1.2.3", [])
    result = collect_version_snapshot(backend)
    assert result["claude_code_version"] == ""


def test_codex_version_key_present_in_snapshot_result():
    result = collect_version_snapshot()
    assert "codex_version" in result
