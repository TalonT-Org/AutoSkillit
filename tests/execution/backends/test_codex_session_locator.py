"""Tests for CodexSessionLocator."""

from __future__ import annotations

from pathlib import Path

import pytest
import zstandard

from autoskillit.core import SessionLocator
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
    def test_locate_session_finds_rollout_by_thread_id(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "sessions" / "2026" / "05" / "26"
        session_dir.mkdir(parents=True)
        rollout = _make_rollout(session_dir, "tid_abc123")
        locator = CodexSessionLocator()
        result = locator.locate_session("tid_abc123", codex_home=tmp_path)
        assert result == rollout

    def test_locate_session_skips_non_matching_thread_id(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "sessions" / "2026" / "05" / "26"
        session_dir.mkdir(parents=True)
        _make_rollout(session_dir, "tid_other")
        locator = CodexSessionLocator()
        assert locator.locate_session("tid_wanted") is None

    def test_locate_session_searches_permanent_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        perm_dir = tmp_path / "logs" / "codex-sessions" / "2026" / "05" / "26"
        perm_dir.mkdir(parents=True)
        rollout = _make_rollout(perm_dir, "tid_perm")
        monkeypatch.setattr(
            "autoskillit.execution.backends.codex.default_log_dir", lambda: tmp_path / "logs"
        )
        locator = CodexSessionLocator()
        result = locator.locate_session("tid_perm")
        assert result == rollout

    def test_locate_via_home_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(
            "autoskillit.execution.backends.codex.default_log_dir",
            lambda: tmp_path / "nonexistent_logs",
        )
        session_dir = tmp_path / ".codex" / "sessions" / "2026" / "05" / "26"
        session_dir.mkdir(parents=True)
        rollout = _make_rollout(session_dir, "tid_home")
        locator = CodexSessionLocator()
        result = locator.locate_session("tid_home")
        assert result == rollout

    def test_locate_via_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "autoskillit.execution.backends.codex.default_log_dir",
            lambda: tmp_path / "nonexistent_logs",
        )
        monkeypatch.delenv("CODEX_HOME", raising=False)
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        session_dir = tmp_path / "sessions" / "2026" / "05" / "26"
        session_dir.mkdir(parents=True)
        rollout = _make_rollout(session_dir, "tid_env")
        locator = CodexSessionLocator()
        result = locator.locate_session("tid_env")
        assert result == rollout

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

    def test_codex_home_priority_over_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_path = tmp_path / "env_sessions"
        env_path.mkdir(parents=True)
        env_session_dir = env_path / "sessions" / "2026" / "05" / "26"
        env_session_dir.mkdir(parents=True)
        _make_rollout(env_session_dir, "tid_priority", "rollout-env.jsonl")

        param_path = tmp_path / "param_sessions"
        param_path.mkdir(parents=True)
        param_session_dir = param_path / "sessions" / "2026" / "05" / "26"
        param_session_dir.mkdir(parents=True)
        rollout = _make_rollout(param_session_dir, "tid_priority", "rollout-param.jsonl")

        monkeypatch.setenv("CODEX_HOME", str(env_path))

        locator = CodexSessionLocator()
        result = locator.locate_session("tid_priority", codex_home=param_path)

        assert result == rollout

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

    def test_file_matches_thread_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        assert CodexSessionLocator._file_matches_thread(f, "any") is False

    def test_file_matches_thread_no_thread_started(self, tmp_path: Path) -> None:
        f = tmp_path / "no_start.jsonl"
        f.write_text('{"type":"turn.completed"}\n')
        assert CodexSessionLocator._file_matches_thread(f, "any") is False

    @pytest.mark.parametrize("fmt", ["thread_started", "session_meta"])
    def test_file_matches_thread_both_formats(self, tmp_path: Path, fmt: str) -> None:
        rollout = _make_rollout(tmp_path, "tid_fmt", fmt=fmt)
        assert CodexSessionLocator._file_matches_thread(rollout, "tid_fmt") is True
        assert CodexSessionLocator._file_matches_thread(rollout, "wrong_id") is False

    def test_locate_session_finds_session_meta_format(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "sessions" / "2026" / "05" / "26"
        session_dir.mkdir(parents=True)
        rollout = _make_rollout(session_dir, "tid_meta", fmt="session_meta")
        locator = CodexSessionLocator()
        result = locator.locate_session("tid_meta", codex_home=tmp_path)
        assert result == rollout
