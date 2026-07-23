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
    async def test_callback_is_a_repeated_candidate_signal(self, tmp_path: Path) -> None:
        """Provider attempts may report different candidates through the same callback."""
        import anyio

        from autoskillit.execution.process._process_race import (
            RaceAccumulator,
            _extract_stdout_session_id,
        )

        captured: list[str] = []
        for attempt, session_id in enumerate(("attempt-one", "attempt-two")):
            stdout_path = tmp_path / f"stdout-{attempt}.jsonl"
            stdout_path.write_text(
                json.dumps({"type": "system", "subtype": "init", "session_id": session_id}) + "\n"
            )
            await _extract_stdout_session_id(
                stdout_path,
                RaceAccumulator(),
                anyio.Event(),
                on_session_id_resolved=captured.append,
            )

        assert captured == ["attempt-one", "attempt-two"]

    @pytest.mark.anyio
    async def test_channel_b_session_id_fires_candidate_without_stdout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Channel B identity is observable when stdout never contains an init record."""
        import anyio

        from autoskillit.core import ChannelBStatus
        from autoskillit.execution.process import _process_race
        from autoskillit.execution.process._process_monitor import SessionMonitorResult

        async def fake_monitor(*args: Any, **kwargs: Any) -> SessionMonitorResult:
            return SessionMonitorResult(ChannelBStatus.DIR_MISSING, "channel-b-only")

        monkeypatch.setattr(_process_race, "_session_log_monitor", fake_monitor)
        acc = _process_race.RaceAccumulator()
        captured: list[str] = []

        await _process_race._watch_session_log(
            tmp_path,
            "",
            1.0,
            0.0,
            frozenset({"result"}),
            1,
            0.0,
            acc,
            anyio.Event(),
            anyio.Event(),
            0.01,
            0.01,
            0.01,
            on_session_id_resolved=captured.append,
        )

        assert acc.channel_b_session_id == "channel-b-only"
        assert captured == ["channel-b-only"]

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
