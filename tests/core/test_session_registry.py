"""Tests for core/session_registry.py."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from autoskillit.core.runtime.session_registry import (
    bind_session_owner,
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


def test_bind_session_owner_preserves_launch_metadata(tmp_path: Path) -> None:
    write_registry_entry(tmp_path, "abc", "cook", "recipe")
    bridge_claude_session_id(tmp_path, "abc", "claude-session")

    bind_session_owner(tmp_path, "abc", os.getpid())

    entry = read_registry(tmp_path)["abc"]
    assert entry["session_type"] == "cook"
    assert entry["recipe_name"] == "recipe"
    assert entry["claude_session_id"] == "claude-session"
    assert entry["owner_pid"] == os.getpid()
    assert entry["owner_boot_id"]
    assert entry["owner_starttime_ticks"] > 0


def test_bind_session_owner_rejects_unknown_launch_id(tmp_path: Path) -> None:
    write_registry_entry(tmp_path, "abc", "cook", None)

    with pytest.raises(KeyError, match="unknown launch ID"):
        bind_session_owner(tmp_path, "missing", os.getpid())

    assert set(read_registry(tmp_path)) == {"abc"}


@pytest.mark.parametrize(
    ("contents", "expected_error"),
    [(None, FileNotFoundError), ("not valid json", json.JSONDecodeError)],
)
def test_bind_session_owner_preserves_registry_read_errors(
    tmp_path: Path,
    contents: str | None,
    expected_error: type[Exception],
) -> None:
    if contents is not None:
        path = registry_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text(contents, encoding="utf-8")

    with pytest.raises(expected_error):
        bind_session_owner(tmp_path, "abc", os.getpid())


def test_bind_session_owner_fails_closed_without_linux_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    write_registry_entry(tmp_path, "abc", "cook", None)
    monkeypatch.setattr(
        "autoskillit.core.runtime.session_registry.read_starttime_ticks",
        lambda _pid: None,
    )

    with pytest.raises(RuntimeError, match="unable to capture Linux owner identity"):
        bind_session_owner(tmp_path, "abc", os.getpid())

    assert "owner_pid" not in read_registry(tmp_path)["abc"]


def test_registry_path_in_project_temp(tmp_path: Path) -> None:
    path = registry_path(tmp_path)
    assert ".autoskillit" in str(path)
    assert "temp" in str(path)
