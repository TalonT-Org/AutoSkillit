"""Tests for the on_session_id_resolved callback fired from inside the task group."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium, pytest.mark.feature("execution")]


class TestOnSessionIdResolvedCallback:
    """on_session_id_resolved callback fires during live subprocess execution."""

    @pytest.mark.anyio
    async def test_callback_fires_with_resolved_session_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When stdout emits a system init record, on_session_id_resolved fires."""
        import anyio

        from autoskillit.execution.process._process_race import (
            RaceAccumulator,
            _extract_stdout_session_id,
        )

        stdout_path = tmp_path / "stdout.jsonl"
        session_id = "sess-callback-123"
        init_record: dict[str, Any] = {
            "type": "system",
            "subtype": "init",
            "session_id": session_id,
        }
        stdout_path.write_text(json.dumps(init_record) + "\n")

        acc = RaceAccumulator()
        ready = anyio.Event()
        captured: list[str] = []

        def on_sid(sid: str) -> None:
            captured.append(sid)

        await _extract_stdout_session_id(
            stdout_path,
            acc,
            ready,
            on_session_id_resolved=on_sid,
        )

        assert ready.is_set()
        assert acc.stdout_session_id == session_id
        assert captured == [session_id]

    @pytest.mark.anyio
    async def test_callback_not_called_when_no_session_id_found(self, tmp_path: Path) -> None:
        """When no init record is present, callback is never invoked."""
        import anyio

        from autoskillit.execution.process._process_race import (
            RaceAccumulator,
            _extract_stdout_session_id,
        )

        stdout_path = tmp_path / "stdout.jsonl"
        stdout_path.write_text("")

        acc = RaceAccumulator()
        ready = anyio.Event()
        captured: list[str] = []

        await _extract_stdout_session_id(
            stdout_path,
            acc,
            ready,
            on_session_id_resolved=lambda sid: captured.append(sid),
            _timeout=0.5,
        )

        assert not acc.stdout_session_id
        assert captured == []
