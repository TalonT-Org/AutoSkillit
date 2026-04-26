"""Tests for cli/_session_picker.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.cli._session_picker import (
    _classify_session,
    _run_picker,
    pick_session,
)
from autoskillit.core.session_registry import write_registry_entry

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _make_index(tmp_path: Path, entries: list[dict]) -> Path:
    """Write sessions-index.json into a temp project dir and return that dir."""
    index_path = tmp_path / "sessions-index.json"
    index_path.write_text(json.dumps(entries), encoding="utf-8")
    return tmp_path


def test_pick_session_no_sessions_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    claude_dir = tmp_path / "claude-projects"
    claude_dir.mkdir()
    monkeypatch.setattr(
        "autoskillit.cli._session_picker.claude_code_project_dir",
        lambda _: claude_dir,
    )
    result = pick_session("cook", project_dir)
    assert result is None


def test_pick_session_filters_cook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()

    entries = [
        {"sessionId": "cook-uuid-1", "firstPrompt": "What's the issue?", "isSidechain": False},
        {"sessionId": "order-uuid-1", "firstPrompt": "Kitchen's open! Hello", "isSidechain": False},
    ]
    (claude_dir / "sessions-index.json").write_text(json.dumps(entries), encoding="utf-8")

    write_registry_entry(project_dir, "lid-cook", "cook", None)
    write_registry_entry(project_dir, "lid-order", "order", None)

    from autoskillit.core.session_registry import bridge_claude_session_id

    bridge_claude_session_id(project_dir, "lid-cook", "cook-uuid-1")
    bridge_claude_session_id(project_dir, "lid-order", "order-uuid-1")

    monkeypatch.setattr(
        "autoskillit.cli._session_picker.claude_code_project_dir",
        lambda _: claude_dir,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": "1")

    result = pick_session("cook", project_dir)
    assert result == "cook-uuid-1"


def test_pick_session_filters_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()

    entries = [
        {"sessionId": "cook-uuid-1", "firstPrompt": "What's the issue?", "isSidechain": False},
        {"sessionId": "order-uuid-1", "firstPrompt": "Kitchen's open! Hello", "isSidechain": False},
    ]
    (claude_dir / "sessions-index.json").write_text(json.dumps(entries), encoding="utf-8")

    write_registry_entry(project_dir, "lid-cook", "cook", None)
    write_registry_entry(project_dir, "lid-order", "order", None)

    from autoskillit.core.session_registry import bridge_claude_session_id

    bridge_claude_session_id(project_dir, "lid-cook", "cook-uuid-1")
    bridge_claude_session_id(project_dir, "lid-order", "order-uuid-1")

    monkeypatch.setattr(
        "autoskillit.cli._session_picker.claude_code_project_dir",
        lambda _: claude_dir,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": "1")

    result = pick_session("order", project_dir)
    assert result == "order-uuid-1"


def test_greeting_heuristic_order_session() -> None:
    entry = {"sessionId": "s1", "firstPrompt": "Order up! Today's special: myrecipe."}
    result = _classify_session(entry, {})
    assert result == "order"


def test_greeting_heuristic_cook_session() -> None:
    entry = {"sessionId": "s1", "firstPrompt": "What's the issue?"}
    result = _classify_session(entry, {})
    assert result == "cook"


def test_greeting_heuristic_open_kitchen_order() -> None:
    entry = {"sessionId": "s1", "firstPrompt": "Kitchen's open! What are we cooking today?"}
    result = _classify_session(entry, {})
    assert result == "order"


def test_sidechain_sessions_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()

    entries = [
        {"sessionId": "sidechain-uuid", "firstPrompt": "What's the issue?", "isSidechain": True},
    ]
    (claude_dir / "sessions-index.json").write_text(json.dumps(entries), encoding="utf-8")

    monkeypatch.setattr(
        "autoskillit.cli._session_picker.claude_code_project_dir",
        lambda _: claude_dir,
    )

    result = pick_session("cook", project_dir)
    assert result is None


def test_user_selects_numbered_session(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = [
        {"sessionId": "uuid-1", "firstPrompt": "What's the issue?"},
        {"sessionId": "uuid-2", "firstPrompt": "Fix the bug"},
    ]
    inputs = iter(["1"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    result = _run_picker(sessions, "cook", {})
    assert result == "uuid-1"


def test_user_selects_fresh_start(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = [{"sessionId": "uuid-1", "firstPrompt": "Hello"}]
    monkeypatch.setattr("builtins.input", lambda _prompt="": "0")
    result = _run_picker(sessions, "cook", {})
    assert result is None


def test_user_selects_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = [{"sessionId": "uuid-1", "firstPrompt": "Hello"}]
    inputs = iter(["99", "0"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    result = _run_picker(sessions, "cook", {})
    assert result is None
