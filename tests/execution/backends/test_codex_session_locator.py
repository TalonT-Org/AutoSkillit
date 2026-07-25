"""Tests for CodexSessionLocator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import zstandard

from autoskillit.core import SessionLocator, SessionSummary
from autoskillit.execution.backends.codex import CodexSessionLocator

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _make_rollout(
    parent: Path,
    thread_id: str,
    name: str = "rollout-2026-05-26T07-30-33-abc.jsonl",
    fmt: str = "thread_started",
) -> Path:
    """Helper: create a rollout NDJSON file with a session-start event."""
    f = parent / name
    if fmt == "session_meta":
        first_line = f'{{"type":"session_meta","payload":{{"id":"{thread_id}"}}}}'
    else:
        first_line = f'{{"type":"thread.started","thread_id":"{thread_id}"}}'
    f.write_text(f'{first_line}\n{{"type":"turn.completed"}}\n')
    return f


class TestCodexSessionLocator:
    def test_list_sessions_reads_injected_index_without_scanning_history(
        self, tmp_path: Path
    ) -> None:
        project = tmp_path / "project"
        project.mkdir()
        store_root = tmp_path / "store"
        store_root.mkdir()
        index_path = tmp_path / "codex-session-index.json"
        index_path.write_text(
            json.dumps(
                [
                    {
                        "backend_name": "codex",
                        "session_id": "thread-new",
                        "launch_id": "0123456789abcdef",
                        "cwd": str(project / ".." / "project"),
                        "first_prompt": "Fix startup",
                        "summary": "Keep retained history off the startup path",
                        "git_branch": "feature/startup",
                        "modified": "2026-07-23T17:00:00Z",
                        "is_sidechain": False,
                        "session_type_hint": "cook",
                    }
                ]
            ),
            encoding="utf-8",
        )
        locator = CodexSessionLocator(store_root=store_root, index_path=index_path)

        assert locator.list_sessions(str(project)) == (
            SessionSummary(
                backend_name="codex",
                session_id="thread-new",
                launch_id="0123456789abcdef",
                cwd=str(project.resolve()),
                first_prompt="Fix startup",
                summary="Keep retained history off the startup path",
                git_branch="feature/startup",
                modified="2026-07-23T17:00:00Z",
                is_sidechain=False,
                session_type_hint="cook",
            ),
        )

    @pytest.mark.parametrize("contents", ["", "{not-json", "null", "{}"])
    def test_list_sessions_empty_or_corrupt_index_is_empty(
        self, tmp_path: Path, contents: str
    ) -> None:
        index_path = tmp_path / "codex-session-index.json"
        index_path.write_text(contents, encoding="utf-8")
        locator = CodexSessionLocator(store_root=tmp_path / "store", index_path=index_path)

        assert locator.list_sessions(str(tmp_path)) == ()

    def test_list_sessions_filters_project_sidechains_and_preserves_source_order(
        self, tmp_path: Path
    ) -> None:
        wanted = tmp_path / "wanted"
        wanted.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        index_path = tmp_path / "codex-session-index.json"
        records = [
            {
                "backend_name": "codex",
                "session_id": "newest",
                "launch_id": None,
                "cwd": str(wanted / "."),
                "first_prompt": "new",
                "summary": "",
                "git_branch": None,
                "modified": None,
                "is_sidechain": False,
                "session_type_hint": "cook",
            },
            {
                "backend_name": "codex",
                "session_id": "sidechain",
                "launch_id": None,
                "cwd": str(wanted),
                "first_prompt": "hidden",
                "summary": "hidden",
                "git_branch": None,
                "modified": None,
                "is_sidechain": True,
                "session_type_hint": "cook",
            },
            {
                "backend_name": "codex",
                "session_id": "other-project",
                "launch_id": None,
                "cwd": str(other),
                "first_prompt": "hidden",
                "summary": "hidden",
                "git_branch": None,
                "modified": None,
                "is_sidechain": False,
                "session_type_hint": "cook",
            },
            {
                "backend_name": "codex",
                "session_id": "older",
                "launch_id": None,
                "cwd": str(wanted),
                "first_prompt": "old",
                "summary": "",
                "git_branch": None,
                "modified": None,
                "is_sidechain": False,
                "session_type_hint": "cook",
            },
        ]
        import json

        index_path.write_text(json.dumps(records), encoding="utf-8")
        locator = CodexSessionLocator(store_root=tmp_path / "store", index_path=index_path)

        assert [
            item.session_id for item in locator.list_sessions(str(wanted / ".." / "wanted"))
        ] == [
            "newest",
            "older",
        ]

    def test_locate_session_skips_non_matching_thread_id(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "codex-sessions" / "2026" / "05" / "26"
        session_dir.mkdir(parents=True)
        _make_rollout(session_dir, "tid_other")
        locator = CodexSessionLocator(store_root=tmp_path)
        assert locator.locate_session("tid_wanted") is None

    def test_locate_session_searches_permanent_dir(self, tmp_path: Path) -> None:
        perm_dir = tmp_path / "codex-sessions" / "2026" / "05" / "26"
        perm_dir.mkdir(parents=True)
        rollout = _make_rollout(perm_dir, "tid_perm")
        locator = CodexSessionLocator(store_root=tmp_path)
        result = locator.locate_session("tid_perm")
        assert result == rollout

    def test_locate_ignores_ambient_and_source_codex_homes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ambient_home = tmp_path / "ambient"
        source_home = tmp_path / "source"
        for home in (ambient_home, source_home):
            session_dir = home / "sessions" / "2026" / "05" / "26"
            session_dir.mkdir(parents=True)
            _make_rollout(session_dir, "tid_noncanonical")
        monkeypatch.setenv("CODEX_HOME", str(ambient_home))
        monkeypatch.setattr(Path, "home", lambda: source_home)

        locator = CodexSessionLocator(store_root=tmp_path / "canonical")

        assert locator.locate_session("tid_noncanonical") is None

    def test_locate_session_returns_none_for_empty_id(self) -> None:
        locator = CodexSessionLocator()
        result = locator.locate_session("")
        assert result is None

    def test_locate_session_returns_none_for_fallback_prefix_ids(self) -> None:
        locator = CodexSessionLocator()
        assert locator.locate_session("no_session_abc123") is None
        assert locator.locate_session("crashed_xyz789") is None

    def test_locate_session_returns_none_when_sessions_dir_absent(self, tmp_path: Path) -> None:
        locator = CodexSessionLocator(store_root=tmp_path)
        result = locator.locate_session("any_id")
        assert result is None

    def test_read_session_handles_plain_jsonl(self, tmp_path: Path) -> None:
        f = tmp_path / "rollout.jsonl"
        f.write_text('{"valid": true}\n{"also": "valid"}\n')
        locator = CodexSessionLocator()
        result = locator.read_session(f)
        assert len(result) == 2
        assert result[0] == {"valid": True}

    def test_read_session_handles_zstd_fallback(self, tmp_path: Path) -> None:
        raw = zstandard.ZstdCompressor().compress(b'{"legacy": true}\n')
        f = tmp_path / "session.jsonl.zst"
        f.write_bytes(raw)
        locator = CodexSessionLocator()
        result = locator.read_session(f)
        assert result == [{"legacy": True}]

    def test_read_session_returns_empty_list_for_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        locator = CodexSessionLocator()
        result = locator.read_session(f)
        assert result == []

    def test_read_session_handles_corrupt_data_gracefully(self, tmp_path: Path) -> None:
        f = tmp_path / "corrupt.jsonl"
        f.write_bytes(b"not valid data")
        locator = CodexSessionLocator()
        result = locator.read_session(f)
        assert result == []

    def test_read_session_skips_malformed_json_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "mixed.jsonl"
        f.write_text('{"valid": true}\nnot json at all\n{"also": "valid"}\n')
        locator = CodexSessionLocator()
        result = locator.read_session(f)
        assert len(result) == 2
        assert result[0] == {"valid": True}
        assert result[1] == {"also": "valid"}

    def test_read_session_decompresses_and_parses_ndjson(self, tmp_path: Path) -> None:
        ndjson_lines = [
            '{"turn": 1, "content": "hello"}',
            '{"turn": 2, "content": "world"}',
        ]
        raw_data = zstandard.ZstdCompressor().compress("\n".join(ndjson_lines).encode("utf-8"))
        session_file = tmp_path / "session.jsonl.zst"
        session_file.write_bytes(raw_data)
        locator = CodexSessionLocator()
        result = locator.read_session(session_file)
        assert len(result) == 2
        assert result[0] == {"turn": 1, "content": "hello"}
        assert result[1] == {"turn": 2, "content": "world"}

    def test_isinstance_session_locator(self) -> None:
        assert isinstance(CodexSessionLocator(), SessionLocator)

    def test_locate_session_finds_session_meta_format(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "codex-sessions" / "2026" / "05" / "26"
        session_dir.mkdir(parents=True)
        rollout = _make_rollout(session_dir, "tid_meta", fmt="session_meta")
        locator = CodexSessionLocator(store_root=tmp_path)
        result = locator.locate_session("tid_meta")
        assert result == rollout

    def test_project_log_dir_returns_codex_sessions_subdir(self, tmp_path: Path) -> None:
        locator = CodexSessionLocator(store_root=tmp_path / "logs")
        result = locator.project_log_dir("/any/cwd")
        assert result == tmp_path / "logs" / "codex-sessions"

    def test_project_log_dir_ignores_cwd_argument(self, tmp_path: Path) -> None:
        locator = CodexSessionLocator(store_root=tmp_path / "logs")
        assert locator.project_log_dir("/path/a") == locator.project_log_dir("/path/b")
