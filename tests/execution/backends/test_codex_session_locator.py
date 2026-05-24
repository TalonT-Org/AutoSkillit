"""Tests for CodexSessionLocator."""

from __future__ import annotations

from pathlib import Path

import pytest
import zstandard

from autoskillit.core import SessionLocator
from autoskillit.execution.backends.codex import CodexSessionLocator

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestCodexSessionLocator:
    def test_locate_session_finds_file_via_explicit_codex_home(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "sessions" / "2025" / "05" / "24"
        session_dir.mkdir(parents=True)
        session_file = session_dir / "thread_abc123.jsonl.zst"
        session_file.write_bytes(b"fake zstd content")

        locator = CodexSessionLocator()
        result = locator.locate_session("thread_abc123", codex_home=tmp_path)

        assert result == session_file

    def test_locate_session_finds_file_via_home_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        codex_sessions = tmp_path / ".codex" / "sessions" / "2025" / "05" / "24"
        codex_sessions.mkdir(parents=True)
        session_file = codex_sessions / "thread_xyz789.jsonl.zst"
        session_file.write_bytes(b"fake zstd content")

        locator = CodexSessionLocator()
        result = locator.locate_session("thread_xyz789")

        assert result == session_file

    def test_locate_session_finds_file_via_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CODEX_HOME", raising=False)
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))

        sessions_dir = tmp_path / "sessions" / "2025" / "05" / "24"
        sessions_dir.mkdir(parents=True)
        session_file = sessions_dir / "thread_env123.jsonl.zst"
        session_file.write_bytes(b"fake zstd content")

        locator = CodexSessionLocator()
        result = locator.locate_session("thread_env123")

        assert result == session_file

    def test_locate_session_returns_none_for_missing_thread_id(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "sessions" / "2025" / "05" / "24"
        session_dir.mkdir(parents=True)
        (session_dir / "other_id.jsonl.zst").write_bytes(b"content")

        locator = CodexSessionLocator()
        result = locator.locate_session("nonexistent")

        assert result is None

    def test_locate_session_returns_none_for_empty_id(self) -> None:
        locator = CodexSessionLocator()
        result = locator.locate_session("")

        assert result is None

    def test_locate_session_returns_none_for_fallback_prefix_ids(self) -> None:
        locator = CodexSessionLocator()
        assert locator.locate_session("no_session_abc123") is None
        assert locator.locate_session("crashed_xyz789") is None

    def test_locate_session_returns_none_when_sessions_dir_absent(self, tmp_path: Path) -> None:
        locator = CodexSessionLocator()
        result = locator.locate_session("any_id", codex_home=tmp_path)

        assert result is None

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

    def test_read_session_returns_empty_list_for_empty_file(self, tmp_path: Path) -> None:
        raw_data = zstandard.ZstdCompressor().compress(b"")
        session_file = tmp_path / "empty.jsonl.zst"
        session_file.write_bytes(raw_data)

        locator = CodexSessionLocator()
        result = locator.read_session(session_file)

        assert result == []

    def test_read_session_handles_corrupt_data_gracefully(self, tmp_path: Path) -> None:
        session_file = tmp_path / "corrupt.jsonl.zst"
        session_file.write_bytes(b"not zstandard data")

        locator = CodexSessionLocator()
        result = locator.read_session(session_file)

        assert result == []

    def test_read_session_skips_malformed_json_lines(self, tmp_path: Path) -> None:
        ndjson_lines = [
            '{"valid": true}',
            "not json at all",
            '{"also": "valid"}',
        ]
        raw_data = zstandard.ZstdCompressor().compress("\n".join(ndjson_lines).encode("utf-8"))
        session_file = tmp_path / "mixed.jsonl.zst"
        session_file.write_bytes(raw_data)

        locator = CodexSessionLocator()
        result = locator.read_session(session_file)

        assert len(result) == 2
        assert result[0] == {"valid": True}
        assert result[1] == {"also": "valid"}

    def test_isinstance_session_locator(self) -> None:
        assert isinstance(CodexSessionLocator(), SessionLocator)

    def test_codex_home_priority_over_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_path = tmp_path / "env_sessions"
        env_path.mkdir(parents=True)
        env_session = env_path / "sessions" / "2025" / "05" / "24" / "thread_priority.jsonl.zst"
        env_session.parent.mkdir(parents=True)
        env_session.write_bytes(b"env content")

        param_path = tmp_path / "param_sessions"
        param_path.mkdir(parents=True)
        param_session = (
            param_path / "sessions" / "2025" / "05" / "24" / "thread_priority.jsonl.zst"
        )
        param_session.parent.mkdir(parents=True)
        param_session.write_bytes(b"param content")

        monkeypatch.setenv("CODEX_HOME", str(env_path))

        locator = CodexSessionLocator()
        result = locator.locate_session("thread_priority", codex_home=param_path)

        assert result == param_session
