"""Tests for core/session_registry.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core.runtime.session_registry import (
    bridge_claude_session_id,
    read_registry,
    registry_path,
    write_registry_entry,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_write_and_read_entry(tmp_path: Path) -> None:
    write_registry_entry(tmp_path, "abc", "cook", None)
    reg = read_registry(tmp_path)
    assert "abc" in reg
    assert reg["abc"]["session_type"] == "cook"
    assert reg["abc"]["claude_session_id"] is None


def test_write_with_recipe_name(tmp_path: Path) -> None:
    write_registry_entry(tmp_path, "xyz", "order", "my-recipe")
    reg = read_registry(tmp_path)
    assert reg["xyz"]["recipe_name"] == "my-recipe"


def test_write_is_atomic(tmp_path: Path) -> None:
    write_registry_entry(tmp_path, "id1", "cook", None)
    path = registry_path(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "id1" in data


def test_read_returns_empty_on_missing_file(tmp_path: Path) -> None:
    assert read_registry(tmp_path) == {}


def test_read_returns_empty_on_corrupt_json(tmp_path: Path) -> None:
    path = registry_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not valid json", encoding="utf-8")
    assert read_registry(tmp_path) == {}


def test_bridge_claude_session_id(tmp_path: Path) -> None:
    write_registry_entry(tmp_path, "abc", "cook", None)
    bridge_claude_session_id(tmp_path, "abc", "claude-uuid-123")
    reg = read_registry(tmp_path)
    assert reg["abc"]["claude_session_id"] == "claude-uuid-123"


def test_bridge_noop_on_missing_launch_id(tmp_path: Path) -> None:
    write_registry_entry(tmp_path, "abc", "cook", None)
    bridge_claude_session_id(tmp_path, "unknown-id", "claude-uuid-123")
    reg = read_registry(tmp_path)
    assert reg["abc"]["claude_session_id"] is None


def test_registry_path_in_project_temp(tmp_path: Path) -> None:
    path = registry_path(tmp_path)
    assert ".autoskillit" in str(path)
    assert "temp" in str(path)
