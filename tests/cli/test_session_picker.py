"""Tests for the backend-neutral typed session picker."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.cli.session._session_picker import (
    _classify_session,
    _format_session_row,
    _run_picker,
    pick_session,
)
from autoskillit.core import SessionSummary
from autoskillit.core.runtime.session_registry import write_registry_entry

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _summary(
    session_id: str,
    *,
    backend_name: str = "claude-code",
    launch_id: str | None = None,
    cwd: str = "/project",
    first_prompt: str = "What's the issue?",
    summary: str = "",
    git_branch: str | None = None,
    modified: str | None = None,
    is_sidechain: bool = False,
    session_type_hint: str | None = None,
) -> SessionSummary:
    return SessionSummary(
        backend_name=backend_name,
        session_id=session_id,
        launch_id=launch_id,
        cwd=cwd,
        first_prompt=first_prompt,
        summary=summary,
        git_branch=git_branch,
        modified=modified,
        is_sidechain=is_sidechain,
        session_type_hint=session_type_hint,
    )


class _StubLocator:
    def __init__(self, summaries: tuple[SessionSummary, ...]) -> None:
        self._summaries = summaries

    def list_sessions(self, cwd: str) -> tuple[SessionSummary, ...]:
        return self._summaries


def test_pick_session_no_sessions_returns_none(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    result = pick_session("cook", project_dir, _StubLocator(()))
    assert result is None


def test_pick_session_filters_cook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    entries = (
        _summary("cook-uuid-1", launch_id="lid-cook"),
        _summary(
            "order-uuid-1",
            launch_id="lid-order",
            first_prompt="Kitchen's open! Hello",
        ),
    )

    write_registry_entry(project_dir, "lid-cook", "cook", None)
    write_registry_entry(project_dir, "lid-order", "order", None)

    from autoskillit.core.runtime.session_registry import bridge_claude_session_id

    bridge_claude_session_id(project_dir, "lid-cook", "cook-uuid-1")
    bridge_claude_session_id(project_dir, "lid-order", "order-uuid-1")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "1")

    result = pick_session("cook", project_dir, _StubLocator(entries))
    assert result == "cook-uuid-1"


def test_pick_session_filters_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    entries = (
        _summary("cook-uuid-1", launch_id="lid-cook"),
        _summary(
            "order-uuid-1",
            launch_id="lid-order",
            first_prompt="Kitchen's open! Hello",
        ),
    )

    write_registry_entry(project_dir, "lid-cook", "cook", None)
    write_registry_entry(project_dir, "lid-order", "order", None)

    from autoskillit.core.runtime.session_registry import bridge_claude_session_id

    bridge_claude_session_id(project_dir, "lid-cook", "cook-uuid-1")
    bridge_claude_session_id(project_dir, "lid-order", "order-uuid-1")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "1")

    result = pick_session("order", project_dir, _StubLocator(entries))
    assert result == "order-uuid-1"


def test_backend_hint_classifies_order_session() -> None:
    entry = _summary("s1", session_type_hint="order")
    result = _classify_session(entry, {})
    assert result == "order"


def test_missing_backend_hint_defaults_to_cook() -> None:
    entry = _summary("s1")
    result = _classify_session(entry, {})
    assert result == "cook"


def test_sidechain_sessions_excluded(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    entries = (_summary("sidechain-uuid", is_sidechain=True),)

    result = pick_session("cook", project_dir, _StubLocator(entries))
    assert result is None


def test_user_selects_numbered_session(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = [
        _summary("uuid-1"),
        _summary("uuid-2", first_prompt="Fix the bug"),
    ]
    inputs = iter(["1"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    result = _run_picker(sessions, "cook", {})
    assert result == "uuid-1"


def test_user_selects_fresh_start(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = [_summary("uuid-1", first_prompt="Hello")]
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "0")
    result = _run_picker(sessions, "cook", {})
    assert result is None


def test_user_selects_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = [_summary("uuid-1", first_prompt="Hello")]
    inputs = iter(["99", "0"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    result = _run_picker(sessions, "cook", {})
    assert result is None


def test_launch_id_registry_classification_wins_over_backend_hint(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    write_registry_entry(project_dir, "0123456789abcdef", "order", "planner")
    from autoskillit.core import read_registry

    entry = _summary(
        "codex-thread",
        backend_name="codex",
        launch_id="0123456789abcdef",
        session_type_hint="cook",
    )

    assert _classify_session(entry, read_registry(project_dir)) == "order"


def test_codex_hint_classifies_unregistered_session_as_cook() -> None:
    entry = _summary(
        "codex-thread",
        backend_name="codex",
        first_prompt="",
        session_type_hint="cook",
    )
    assert _classify_session(entry, {}) == "cook"


def test_launch_id_registry_precedes_greeting_hint(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    write_registry_entry(project_dir, "fedcba9876543210", "cook", None)
    from autoskillit.core import read_registry
    from autoskillit.core.runtime.session_registry import bridge_claude_session_id

    bridge_claude_session_id(project_dir, "fedcba9876543210", "claude-uuid")
    entry = _summary(
        "claude-uuid",
        launch_id="fedcba9876543210",
        first_prompt="Kitchen's open!",
    )

    assert _classify_session(entry, read_registry(project_dir)) == "cook"


def test_format_prefers_summary_then_first_prompt_and_displays_metadata() -> None:
    with_summary = _summary(
        "codex-thread",
        backend_name="codex",
        summary="Durable startup",
        first_prompt="Ignored prompt",
        git_branch="feature/startup",
        modified="2 minutes ago",
    )
    prompt_only = _summary("claude-thread", first_prompt="Fix the picker")

    assert _format_session_row(with_summary, "cook", {}) == (
        "[cook]  Durable startup  feature/startup  2 minutes ago"
    )
    assert _format_session_row(prompt_only, "cook", {}) == "[cook]  Fix the picker"


def test_picker_returns_backend_session_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    summaries = (
        _summary(
            "thread-id-not-launch-id",
            backend_name="codex",
            launch_id="0123456789abcdef",
            session_type_hint="cook",
        ),
    )
    write_registry_entry(project_dir, "0123456789abcdef", "cook", None)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "1")

    assert pick_session("cook", project_dir, _StubLocator(summaries)) == (
        "thread-id-not-launch-id"
    )
