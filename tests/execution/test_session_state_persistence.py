"""T3: Early session ID persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.execution.session._session_state import (
    SessionState,
    clear_session_state,
    persist_session_state,
    read_session_state,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestPersistSessionState:
    def test_write_and_read(self, tmp_path: Path) -> None:
        state = SessionState(
            session_id="sess-abc",
            pid=1234,
            boot_id="boot-xyz",
            starttime_ticks=99999,
        )
        persist_session_state(state, tmp_path)
        restored = read_session_state(tmp_path)
        assert restored is not None
        assert restored.session_id == "sess-abc"
        assert restored.pid == 1234
        assert restored.boot_id == "boot-xyz"
        assert restored.starttime_ticks == 99999

    def test_optional_fields(self, tmp_path: Path) -> None:
        state = SessionState(
            session_id="s1",
            pid=1,
            boot_id="b",
            starttime_ticks=0,
            checkpoint_path="/tmp/cp.json",
            infra_exit_category="context_exhausted",
        )
        persist_session_state(state, tmp_path)
        restored = read_session_state(tmp_path)
        assert restored is not None
        assert restored.checkpoint_path == "/tmp/cp.json"
        assert restored.infra_exit_category == "context_exhausted"

    def test_read_missing_returns_none(self, tmp_path: Path) -> None:
        assert read_session_state(tmp_path) is None

    def test_read_corrupt_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "dispatch_session_state.json").write_text("not json")
        assert read_session_state(tmp_path) is None

    def test_read_malformed_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "dispatch_session_state.json").write_text('{"session_id": "x"}')
        assert read_session_state(tmp_path) is None

    def test_clear_removes_file(self, tmp_path: Path) -> None:
        state = SessionState(session_id="s", pid=1, boot_id="b", starttime_ticks=0)
        persist_session_state(state, tmp_path)
        assert read_session_state(tmp_path) is not None
        clear_session_state(tmp_path)
        assert read_session_state(tmp_path) is None

    def test_clear_missing_is_noop(self, tmp_path: Path) -> None:
        clear_session_state(tmp_path)

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "nested" / "dir"
        state = SessionState(session_id="s", pid=1, boot_id="b", starttime_ticks=0)
        persist_session_state(state, nested)
        assert read_session_state(nested) is not None
