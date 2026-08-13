from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core import (
    SessionLocator,
    SessionSummary,
    bridge_claude_session_id,
    read_registry,
    write_registry_entry,
)
from autoskillit.execution.backends import ClaudeSessionLocator
from tests._helpers import seed_registry_owner

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


class TestClaudeSessionLocator:
    def test_list_sessions_translates_native_index_and_normalizes_cwd(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        project = tmp_path / "project"
        project.mkdir()
        write_registry_entry(project, "0123456789abcdef", "cook", None)
        seed_registry_owner(project, "0123456789abcdef")
        bridge_claude_session_id(project, "0123456789abcdef", "claude-1")
        fake_home = tmp_path / "home"
        index_dir = fake_home / ".claude" / "projects" / "-ignored"
        index_dir.mkdir(parents=True)
        (index_dir / "sessions-index.json").write_text(
            json.dumps(
                [
                    {
                        "sessionId": "claude-1",
                        "cwd": str(project / ".." / "project"),
                        "firstPrompt": "What's the issue?",
                        "summary": None,
                        "gitBranch": "main",
                        "modified": None,
                        "isSidechain": False,
                    }
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(
            "autoskillit.execution.backends.claude.claude_code_project_dir",
            lambda _cwd: index_dir,
        )

        assert ClaudeSessionLocator().list_sessions(str(project)) == (
            SessionSummary(
                backend_name="claude-code",
                session_id="claude-1",
                launch_id="0123456789abcdef",
                cwd=str(project.resolve()),
                first_prompt="What's the issue?",
                summary="",
                git_branch="main",
                modified=None,
                is_sidechain=False,
                session_type_hint="cook",
            ),
        )
        entry = read_registry(project)["0123456789abcdef"]
        assert entry["owner_pid"] == 321
        assert entry["owner_boot_id"] == "boot-id"
        assert entry["owner_starttime_ticks"] == 654

    def test_list_sessions_filters_sidechains_and_other_projects(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        project = tmp_path / "project"
        project.mkdir()
        index_dir = tmp_path / "claude-project"
        index_dir.mkdir()
        (index_dir / "sessions-index.json").write_text(
            json.dumps(
                [
                    {
                        "sessionId": "side",
                        "cwd": str(project),
                        "firstPrompt": "x",
                        "isSidechain": True,
                    },
                    {
                        "sessionId": "other",
                        "cwd": str(tmp_path / "other"),
                        "firstPrompt": "x",
                        "isSidechain": False,
                    },
                    {
                        "sessionId": "order",
                        "cwd": str(project),
                        "firstPrompt": "Kitchen's open!",
                        "isSidechain": False,
                    },
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "autoskillit.execution.backends.claude.claude_code_project_dir",
            lambda _cwd: index_dir,
        )

        summaries = ClaudeSessionLocator().list_sessions(str(project / "."))
        assert [item.session_id for item in summaries] == ["order"]
        assert summaries[0].session_type_hint == "order"

    @pytest.mark.parametrize("contents", ["", "not-json", "null", "{}"])
    def test_list_sessions_empty_or_corrupt_index_is_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        contents: str,
    ) -> None:
        index_dir = tmp_path / "claude-project"
        index_dir.mkdir()
        (index_dir / "sessions-index.json").write_text(contents, encoding="utf-8")
        monkeypatch.setattr(
            "autoskillit.execution.backends.claude.claude_code_project_dir",
            lambda _cwd: index_dir,
        )

        assert ClaudeSessionLocator().list_sessions(str(tmp_path)) == ()

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

    def test_project_log_dir_returns_claude_project_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_home = tmp_path / "homedir"
        fake_home.mkdir(parents=True)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        locator = ClaudeSessionLocator()
        result = locator.project_log_dir("/some/project/path")
        assert result == fake_home / ".claude" / "projects" / "-some-project-path"
