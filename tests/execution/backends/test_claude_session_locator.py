from __future__ import annotations

import json
from pathlib import Path

import pytest

import autoskillit.execution.backends._claude.session_locator as _locator_module
from autoskillit.core import (
    SessionLocator,
    SessionSummary,
    bridge_claude_session_id,
    read_registry,
    write_registry_entry,
)
from autoskillit.core.runtime.session_registry import registry_path
from autoskillit.execution.backends import ClaudeSessionLocator
from tests._helpers import seed_registry_owner

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


class TestClaudeSessionLocator:
    @pytest.mark.parametrize(
        "record",
        [
            pytest.param(
                {"type": "system", "subtype": "compact_boundary"},
                id="synthetic-without-optional-metadata",
            ),
            pytest.param(
                {
                    "type": "system",
                    "subtype": "compact_boundary",
                    "renamed_compaction_metadata": {"synthetic": True},
                },
                id="synthetic-with-renamed-optional-metadata",
            ),
        ],
    )
    def test_session_context_was_compacted_matches_exact_pair_without_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        record: dict[str, object],
    ) -> None:
        """Synthetic JSONL records need only the exact top-level type/subtype pair."""
        transcript = tmp_path / "synthetic-compact-boundary.jsonl"
        transcript.write_text(json.dumps(record) + "\n", encoding="utf-8")
        monkeypatch.setattr(
            ClaudeSessionLocator,
            "session_log_path",
            lambda _self, _cwd, _session_id: transcript,
        )

        assert ClaudeSessionLocator().session_context_was_compacted("/project", "session")

    def test_session_context_was_compacted_ignores_misleading_and_malformed_candidates(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Synthetic malformed, non-object, nested, and substring candidates are ignored."""
        candidates = [
            "not JSON",
            '{"subtype":"compact_boundary",',
            json.dumps(None),
            json.dumps([]),
            json.dumps({"type": "systemish", "subtype": "compact_boundary"}),
            json.dumps({"type": "system", "subtype": "pre_compact_boundary"}),
            json.dumps({"nested": {"type": "system", "subtype": "compact_boundary"}}),
        ]
        transcript = tmp_path / "synthetic-non-boundary-candidates.jsonl"
        transcript.write_text("\n".join(candidates) + "\n", encoding="utf-8")
        monkeypatch.setattr(
            ClaudeSessionLocator,
            "session_log_path",
            lambda _self, _cwd, _session_id: transcript,
        )

        assert not ClaudeSessionLocator().session_context_was_compacted("/project", "session")

    @pytest.mark.parametrize("transcript_kind", ["unresolved", "missing", "clean"])
    def test_session_context_was_compacted_returns_false_for_missing_or_clean_transcripts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        transcript_kind: str,
    ) -> None:
        if transcript_kind == "unresolved":
            transcript_path = None
        elif transcript_kind == "missing":
            transcript_path = tmp_path / "missing-synthetic-transcript.jsonl"
        else:
            transcript_path = tmp_path / "synthetic-clean-transcript.jsonl"
            transcript_path.write_text(
                json.dumps({"type": "assistant", "text": "ordinary response"}) + "\n",
                encoding="utf-8",
            )
        monkeypatch.setattr(
            ClaudeSessionLocator,
            "session_log_path",
            lambda _self, _cwd, _session_id: transcript_path,
        )

        assert not ClaudeSessionLocator().session_context_was_compacted("/project", "session")

    def test_session_context_was_compacted_propagates_unreadable_transcript(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        transcript = tmp_path / "synthetic-unreadable-transcript.jsonl"
        transcript.write_text(
            json.dumps({"type": "system", "subtype": "compact_boundary"}) + "\n",
            encoding="utf-8",
        )
        original_open = Path.open

        def _raise_permission_error(path: Path, *args, **kwargs):
            if path == transcript:
                raise PermissionError("synthetic transcript unreadable")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", _raise_permission_error)
        monkeypatch.setattr(
            ClaudeSessionLocator,
            "session_log_path",
            lambda _self, _cwd, _session_id: transcript,
        )

        with pytest.raises(PermissionError, match="synthetic transcript unreadable"):
            ClaudeSessionLocator().session_context_was_compacted("/project", "session")

    def test_session_context_was_compacted_propagates_invalid_utf8(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        transcript = tmp_path / "synthetic-invalid-utf8-transcript.jsonl"
        transcript.write_bytes(b"\xff\n")
        monkeypatch.setattr(
            ClaudeSessionLocator,
            "session_log_path",
            lambda _self, _cwd, _session_id: transcript,
        )

        with pytest.raises(UnicodeDecodeError):
            ClaudeSessionLocator().session_context_was_compacted("/project", "session")

    def test_list_sessions_translates_native_index_and_normalizes_cwd(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        project = tmp_path / "project"
        project.mkdir()
        write_registry_entry(project, "0123456789abcdef", "cook", None)
        seed_registry_owner(project, "0123456789abcdef")
        seeded_owner = {
            key: value
            for key, value in read_registry(project)["0123456789abcdef"].items()
            if key.startswith("owner_")
        }
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
            _locator_module,
            "claude_code_project_dir",
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
        assert {
            key: value for key, value in entry.items() if key.startswith("owner_")
        } == seeded_owner

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
            _locator_module,
            "claude_code_project_dir",
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
            _locator_module,
            "claude_code_project_dir",
            lambda _cwd: index_dir,
        )

        assert ClaudeSessionLocator().list_sessions(str(tmp_path)) == ()

    def test_list_sessions_rejects_duplicate_registry_conversation_ids_without_mutation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        project = tmp_path / "project"
        project.mkdir()
        registry_path(project).parent.mkdir(parents=True)
        registry_path(project).write_text(
            json.dumps(
                {
                    "launch-one": {"claude_session_id": "claude-duplicate"},
                    "launch-two": {"claude_session_id": "claude-duplicate"},
                }
            ),
            encoding="utf-8",
        )
        registry_bytes = registry_path(project).read_bytes()
        index_dir = tmp_path / "claude-project"
        index_dir.mkdir()
        (index_dir / "sessions-index.json").write_text("[]", encoding="utf-8")
        monkeypatch.setattr(
            _locator_module,
            "claude_code_project_dir",
            lambda _cwd: index_dir,
        )

        with pytest.raises(ValueError) as error:
            ClaudeSessionLocator().list_sessions(str(project))

        assert "claude-duplicate" in str(error.value)
        assert "launch-one" in str(error.value)
        assert "launch-two" in str(error.value)
        assert "removing one duplicate mapping" in str(error.value)
        assert registry_path(project).read_bytes() == registry_bytes

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
