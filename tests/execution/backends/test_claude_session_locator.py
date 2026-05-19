from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import SessionLocator
from autoskillit.execution.backends import ClaudeSessionLocator

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


class TestClaudeSessionLocator:
    def test_locate_session_finds_existing_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_home = tmp_path / "homedir"
        fake_home.mkdir(parents=True)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        project_dir = fake_home / ".claude" / "projects" / "myproject"
        project_dir.mkdir(parents=True)
        session_file = project_dir / "test-session-id.jsonl"
        session_file.write_text("")

        locator = ClaudeSessionLocator()
        result = locator.locate_session("test-session-id")
        assert result == session_file

    def test_locate_session_returns_none_for_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_home = tmp_path / "homedir"
        fake_home.mkdir(parents=True)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        (fake_home / ".claude" / "projects").mkdir(parents=True)

        locator = ClaudeSessionLocator()
        result = locator.locate_session("nonexistent-session")
        assert result is None

    def test_locate_session_returns_none_for_empty_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        locator = ClaudeSessionLocator()
        result = locator.locate_session("")
        assert result is None

    def test_locate_session_returns_none_for_fallback_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        locator = ClaudeSessionLocator()
        assert locator.locate_session("no_session_abc123") is None
        assert locator.locate_session("crashed_xyz789") is None

    def test_structural_conformance_session_locator(self) -> None:
        assert isinstance(ClaudeSessionLocator(), SessionLocator)

    def test_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        locator = ClaudeSessionLocator()
        with pytest.raises((FrozenInstanceError, TypeError)):
            locator.some_attr = "value"  # type: ignore[misc]
