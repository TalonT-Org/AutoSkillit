"""Unit tests for _session_log_monitor and related session log monitoring behavior."""

from __future__ import annotations

import time
from unittest.mock import patch

import anyio
import pytest

from autoskillit.core.types import ChannelBStatus
from autoskillit.execution.process import (
    RaceAccumulator,
    _session_log_monitor,
    _watch_session_log,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

# Script that writes non-matching output then hangs
PARTIAL_OUTPUT_THEN_HANG_SCRIPT = (
    "import sys, time\n"
    'sys.stdout.write("partial output\\n")\n'
    "sys.stdout.flush()\n"
    "time.sleep(3600)\n"
)


class TestSessionLogMonitor:
    """Session log monitor detects completion and staleness."""

    @pytest.mark.anyio
    async def test_session_log_monitor_detects_completion(self, tmp_path):
        """Session log with completion marker in assistant record returns 'completion'."""
        import json

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        spawn_time = time.time() - 1  # slightly in the past

        session_file = log_dir / "abc123.jsonl"
        session_file.write_text(
            json.dumps(
                {"type": "assistant", "message": {"role": "assistant", "content": "working..."}}
            )
            + "\n"
        )

        async def append_marker():
            await anyio.sleep(0.5)
            with session_file.open("a") as f:
                f.write(
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "content": "Done\n\n%%AUTOSKILLIT_COMPLETE%%",
                            },
                        }
                    )
                    + "\n"
                )

        monitor_result: list[str] = []

        async def _run_monitor() -> None:
            monitor_result.append(
                await _session_log_monitor(
                    log_dir,
                    "%%AUTOSKILLIT_COMPLETE%%",
                    stale_threshold=30,
                    spawn_time=spawn_time,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                )
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(append_marker)
            tg.start_soon(_run_monitor)

        assert monitor_result[0].status == "completion"
        assert monitor_result[0].session_id == "abc123"

    @pytest.mark.anyio
    async def test_session_log_monitor_detects_staleness(self, tmp_path):
        """Session log that stops being written to returns 'stale'."""
        import json

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        spawn_time = time.time() - 1

        session_file = log_dir / "abc123.jsonl"
        session_file.write_text(
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "hello"}})
            + "\n"
        )

        start = time.monotonic()
        result = await _session_log_monitor(
            log_dir,
            "%%AUTOSKILLIT_COMPLETE%%",
            stale_threshold=0.2,
            spawn_time=spawn_time,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
        )
        elapsed = time.monotonic() - start

        assert result.status == "stale"
        assert result.session_id == "abc123"
        assert elapsed < 1.0, f"Staleness should fire after ~0.2s, took {elapsed:.1f}s"

    @pytest.mark.anyio
    async def test_staleness_resets_on_activity(self, tmp_path):
        """Session log that keeps getting written to does not fire staleness."""
        import json

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        spawn_time = time.time() - 1

        session_file = log_dir / "abc123.jsonl"
        session_file.write_text(
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "start"}})
            + "\n"
        )

        async def keep_writing():
            for i in range(5):
                await anyio.sleep(0.05)
                with session_file.open("a") as f:
                    f.write(
                        json.dumps(
                            {
                                "type": "assistant",
                                "message": {"role": "assistant", "content": f"msg {i}"},
                            }
                        )
                        + "\n"
                    )
            # After writing stops, staleness should eventually fire
            await anyio.sleep(0.5)

        monitor_result: list[str] = []

        async with anyio.create_task_group() as tg:

            async def _run_monitor() -> None:
                monitor_result.append(
                    await _session_log_monitor(
                        log_dir,
                        "NONEXISTENT_MARKER",
                        stale_threshold=0.3,
                        spawn_time=spawn_time,
                        _phase1_poll=0.01,
                        _phase2_poll=0.05,
                    )
                )
                tg.cancel_scope.cancel()  # cancel writer once monitor fires

            tg.start_soon(keep_writing)
            tg.start_soon(_run_monitor)

        # Staleness should have fired AFTER the writing stopped, not during
        assert monitor_result[0].status == "stale"

    @pytest.mark.anyio
    async def test_monitor_ignores_marker_in_non_assistant_records(self, tmp_path):
        """Monitor must NOT fire on completion marker in non-assistant records.

        Reproduces the false-fire: Claude Code writes the prompt (containing
        the completion marker) into a queue-operation/enqueue record at byte 0.
        The monitor should ignore it. Only an assistant-type record triggers.
        """
        import json

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        spawn_time = time.time() - 1

        marker = "%%AUTOSKILLIT_COMPLETE%%"
        # Pre-populate with a queue-operation record containing the marker
        # (this is what Claude Code writes immediately from the injected prompt)
        session_file = log_dir / "abc123.jsonl"

        enqueue_record = json.dumps(
            {
                "type": "queue-operation",
                "operation": "enqueue",
                "content": f"Do the task\n\nORCHESTRATION DIRECTIVE: {marker}",
            }
        )
        session_file.write_text(enqueue_record + "\n")

        poll_count = 0
        polls_done = anyio.Event()

        def on_poll() -> None:
            nonlocal poll_count
            poll_count += 1
            if poll_count >= 5:
                polls_done.set()

        monitor_result: list[str] = []

        async with anyio.create_task_group() as tg:

            async def _run_monitor() -> None:
                monitor_result.append(
                    await _session_log_monitor(
                        log_dir,
                        marker,
                        stale_threshold=30,
                        spawn_time=spawn_time,
                        _phase1_poll=0.01,
                        _phase2_poll=0.05,
                        _on_poll=on_poll,
                    )
                )

            tg.start_soon(_run_monitor)
            with anyio.fail_after(10.0):
                await polls_done.wait()
            assert not monitor_result, "Monitor fired on non-assistant record — false-fire bug"

            # Now append an assistant record with the marker — should fire
            assistant_record = json.dumps(
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "content": f"Done\n\n{marker}"},
                }
            )
            with session_file.open("a") as f:
                f.write(assistant_record + "\n")
            # task group awaits _run_monitor to detect assistant record and complete

        assert monitor_result[0].status == "completion"

    @pytest.mark.anyio
    async def test_monitor_realistic_jsonl_sequence(self, tmp_path):
        """Monitor correctly handles the realistic 3-record JSONL sequence.

        Claude Code writes:
        1. queue-operation/enqueue (immediate, contains marker in prompt)
        2. user message (immediate, contains marker in prompt)
        3. assistant message (after delay, contains marker in response)

        Only record 3 should trigger completion.
        """
        import json

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        spawn_time = time.time() - 1

        marker = "%%AUTOSKILLIT_COMPLETE%%"

        # Write records 1 and 2 immediately (both contain the marker)
        session_file = log_dir / "abc123.jsonl"
        records_12 = (
            json.dumps(
                {
                    "type": "queue-operation",
                    "operation": "enqueue",
                    "content": f"Task prompt {marker}",
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": f"Task prompt {marker}"},
                }
            )
            + "\n"
        )
        session_file.write_text(records_12)

        poll_count = 0
        polls_done = anyio.Event()

        def on_poll() -> None:
            nonlocal poll_count
            poll_count += 1
            if poll_count >= 5:
                polls_done.set()

        monitor_result: list[str] = []

        async with anyio.create_task_group() as tg:

            async def _run_monitor() -> None:
                monitor_result.append(
                    await _session_log_monitor(
                        log_dir,
                        marker,
                        stale_threshold=30,
                        spawn_time=spawn_time,
                        _phase1_poll=0.01,
                        _phase2_poll=0.05,
                        _on_poll=on_poll,
                    )
                )

            tg.start_soon(_run_monitor)
            with anyio.fail_after(10.0):
                await polls_done.wait()
            assert not monitor_result, "Monitor fired on user/enqueue records"

            # Write record 3 (assistant with marker as standalone line)
            assistant_record = json.dumps(
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "content": f"All done\n\n{marker}"},
                }
            )
            with session_file.open("a") as f:
                f.write(assistant_record + "\n")
            # task group awaits _run_monitor to detect assistant record and complete

        assert monitor_result[0].status == "completion"


class TestSessionLogMonitorStaleSuppressionGate:
    """_session_log_monitor suppresses stale when process has an active port-443 connection."""

    @pytest.mark.anyio
    async def test_suppresses_stale_when_port_443_connection_active(self, tmp_path):
        """
        File stops growing. Monitor reaches stale_threshold. But process has
        an ESTABLISHED port-443 connection -> suppression fires, clock resets,
        monitor continues. On second check (connection dropped) -> stale fires.
        """
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        call_count = {"n": 0}

        def side_effect(pid):
            call_count["n"] += 1
            return call_count["n"] == 1  # True on first call, False on second

        with patch(
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
            side_effect=side_effect,
        ):
            with anyio.fail_after(5.0):
                result = await _session_log_monitor(
                    tmp_path,
                    "DONE",
                    stale_threshold=0.05,
                    spawn_time=spawn_time,
                    pid=99999,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                )
        assert result.status == "stale"
        assert call_count["n"] == 2

    @pytest.mark.anyio
    async def test_fires_stale_immediately_when_no_api_connection(self, tmp_path):
        """Standard stale: file silent, no pid provided, stale fires as before."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10
        with anyio.fail_after(2.0):
            result = await _session_log_monitor(
                tmp_path,
                "DONE",
                stale_threshold=0.05,
                spawn_time=spawn_time,
                # pid omitted (defaults to None)
                _phase1_poll=0.01,
                _phase2_poll=0.05,
            )
        assert result.status == "stale"

    @pytest.mark.anyio
    async def test_fires_stale_when_pid_is_none_regardless_of_tcp(self, tmp_path):
        """pid=None bypasses TCP check entirely — existing behavior preserved."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        with patch(
            "autoskillit.execution.process._process_monitor._has_active_api_connection"
        ) as mock_tcp:
            with anyio.fail_after(2.0):
                result = await _session_log_monitor(
                    tmp_path,
                    "DONE",
                    stale_threshold=0.05,
                    spawn_time=spawn_time,
                    pid=None,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                )
        assert result.status == "stale"
        mock_tcp.assert_not_called()

    @pytest.mark.anyio
    async def test_suppression_emits_warning(self, tmp_path, capsys):
        """A suppression event must log a warning with elapsed time."""
        import structlog

        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        calls = {"n": 0}

        def side_effect(pid):
            calls["n"] += 1
            return calls["n"] == 1

        with patch(
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
            side_effect=side_effect,
        ):
            with structlog.testing.capture_logs() as logs:
                with anyio.fail_after(5.0):
                    await _session_log_monitor(
                        tmp_path,
                        "DONE",
                        stale_threshold=0.05,
                        spawn_time=spawn_time,
                        pid=99999,
                        _phase1_poll=0.01,
                        _phase2_poll=0.05,
                    )
        # capture_logs() intercepts when structlog is in default state.
        # In a parallel worker where configure_logging() ran in a prior test,
        # bound loggers may use a stale processor reference and write to stdout.
        captured = capsys.readouterr().out
        warning_in_logs = any(
            "port-443" in str(log.get("event", "")) or "ESTABLISHED" in str(log.get("event", ""))
            for log in logs
        )
        warning_in_stdout = "port-443" in captured or "ESTABLISHED" in captured
        assert warning_in_logs or warning_in_stdout, (
            "Suppression warning must appear in structlog capture or stdout"
        )

    @pytest.mark.anyio
    async def test_suppresses_stale_when_child_cpu_active_no_api_connection(
        self, tmp_path, monkeypatch
    ):
        """Child CPU activity suppresses stale kill even when no port-443 connection."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10  # wall time — compared against st_ctime in phase 1
        call_count: dict[str, int] = {"cpu": 0}

        def fake_api_conn(pid):
            return False  # No port-443 connection

        def fake_child_cpu(pid):
            call_count["cpu"] += 1
            return call_count["cpu"] == 1  # True first, False second

        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
            fake_api_conn,
        )
        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_child_processes",
            fake_child_cpu,
        )
        result = await _session_log_monitor(
            tmp_path,
            "DONE",
            stale_threshold=0.05,
            spawn_time=spawn_time,
            pid=9999,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
        )
        assert result.status == ChannelBStatus.STALE
        assert call_count["cpu"] == 2  # suppressed once, then fired


class TestStaleSuppressionBounded:
    """Bounded suppression: max_suppression_seconds caps stale deferral."""

    @pytest.mark.anyio
    async def test_stale_suppression_bounded_by_max_duration(self, tmp_path, monkeypatch):
        """Stale fires after max_suppression_seconds despite ESTABLISHED connection."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
            lambda pid: True,
        )

        with anyio.fail_after(8.0):
            result = await _session_log_monitor(
                tmp_path,
                "DONE",
                stale_threshold=0.05,
                spawn_time=spawn_time,
                pid=9999,
                _phase1_poll=0.01,
                _phase2_poll=0.05,
                max_suppression_seconds=1.0,
            )
        assert result.status == ChannelBStatus.STALE

    @pytest.mark.anyio
    async def test_stale_suppression_resets_on_genuine_activity(self, tmp_path, monkeypatch):
        """Suppression counter resets when JSONL file grows."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
            lambda pid: True,
        )

        async def write_activity() -> None:
            import json as _json

            for i in range(6):
                await anyio.sleep(0.5)
                with session_file.open("a") as f:
                    record = {"type": "assistant", "message": {"content": f"msg-{i}"}}
                    f.write(_json.dumps(record) + "\n")

        with anyio.fail_after(10.0):
            async with anyio.create_task_group() as tg:
                tg.start_soon(write_activity)
                result = await _session_log_monitor(
                    tmp_path,
                    "DONE",
                    stale_threshold=0.05,
                    spawn_time=spawn_time,
                    pid=9999,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                    max_suppression_seconds=2.0,
                )
                tg.cancel_scope.cancel()

        assert result.status == ChannelBStatus.STALE

    @pytest.mark.anyio
    async def test_stale_suppression_logs_warning_on_bounded_kill(
        self, tmp_path, monkeypatch, capsys
    ):
        """Warning log emitted when bounded suppression fires."""
        import structlog.testing

        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
            lambda pid: True,
        )

        with anyio.fail_after(8.0):
            with structlog.testing.capture_logs() as logs:
                result = await _session_log_monitor(
                    tmp_path,
                    "DONE",
                    stale_threshold=0.05,
                    spawn_time=spawn_time,
                    pid=9999,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                    max_suppression_seconds=1.0,
                )
        assert result.status == ChannelBStatus.STALE
        captured = capsys.readouterr().out + capsys.readouterr().err
        bounded_in_logs = any("Suppression bounded" in str(log.get("event", "")) for log in logs)
        bounded_in_stdout = "Suppression bounded" in captured
        assert bounded_in_logs or bounded_in_stdout


class TestSessionLogMonitorSessionId:
    """_session_log_monitor returns SessionMonitorResult with session ID from filename."""

    @pytest.mark.anyio
    async def test_session_log_monitor_returns_session_id_from_filename(self, tmp_path):
        """_session_log_monitor returns the JSONL filename stem as session_id."""
        import json

        session_uuid = "d9adcc78-3098-4c3e-8720-ddcf3da35fff"
        jsonl_file = tmp_path / f"{session_uuid}.jsonl"
        jsonl_file.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": "done\n\nCOMPLETION_MARKER"},
                }
            )
            + "\n"
        )

        result = await _session_log_monitor(
            tmp_path,
            "COMPLETION_MARKER",
            stale_threshold=10.0,
            spawn_time=time.time() - 1,
        )

        assert result.status == "completion"
        assert result.session_id == session_uuid

    @pytest.mark.anyio
    async def test_session_log_monitor_returns_session_id_on_stale(self, tmp_path):
        """Even stale sessions capture the session ID from the discovered file."""
        import json

        session_uuid = "abc12345-dead-beef-cafe-123456789abc"
        jsonl_file = tmp_path / f"{session_uuid}.jsonl"
        jsonl_file.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": "no marker"},
                }
            )
            + "\n"
        )

        result = await _session_log_monitor(
            tmp_path,
            "MARKER",
            stale_threshold=0.1,
            spawn_time=time.time() - 1,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
        )

        assert result.status == "stale"
        assert result.session_id == session_uuid

    @pytest.mark.anyio
    async def test_session_log_monitor_empty_session_id_when_no_file_found(self, tmp_path):
        """When no JSONL file is discovered (phase1 timeout), session_id is empty."""
        result = await _session_log_monitor(
            tmp_path,
            "MARKER",
            stale_threshold=10.0,
            spawn_time=time.time() - 1,
            _phase1_timeout=0.1,
        )

        assert result.status == "stale"
        assert result.session_id == ""

    @pytest.mark.anyio
    async def test_session_log_monitor_status_is_channel_b_status_enum(self, tmp_path):
        """SessionMonitorResult.status is a ChannelBStatus enum member."""
        import json

        from autoskillit.core.types import ChannelBStatus

        session_uuid = "enum-check-session"
        jsonl_file = tmp_path / f"{session_uuid}.jsonl"
        jsonl_file.write_text(
            json.dumps({"type": "assistant", "message": {"content": "done\n\nMARKER"}}) + "\n"
        )
        result = await _session_log_monitor(
            tmp_path,
            "MARKER",
            stale_threshold=10.0,
            spawn_time=time.time() - 1,
        )
        assert isinstance(result.status, ChannelBStatus)


class TestWatchSessionLogSessionId:
    """_watch_session_log deposits session ID from monitor into accumulator."""

    @pytest.mark.anyio
    async def test_watch_session_log_deposits_session_id(self, tmp_path):
        """_watch_session_log writes channel_b_session_id to the accumulator."""
        import json

        acc = RaceAccumulator()
        trigger = anyio.Event()
        channel_b_ready = anyio.Event()

        session_uuid = "test-uuid-from-channel-b"
        jsonl_file = tmp_path / f"{session_uuid}.jsonl"
        jsonl_file.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": "done MARKER"},
                }
            )
            + "\n"
        )

        async with anyio.create_task_group() as tg:
            tg.start_soon(
                _watch_session_log,
                tmp_path,
                "MARKER",
                10.0,
                time.time() - 1,
                frozenset({"assistant"}),
                12345,
                5.0,
                acc,
                trigger,
                channel_b_ready,
                0.1,
                0.1,
                30.0,
            )
            await channel_b_ready.wait()
            tg.cancel_scope.cancel()

        assert acc.channel_b_session_id == session_uuid


class TestSessionIdBasedSelection:
    """Phase 1 identity-based JSONL file selection."""

    @pytest.mark.anyio
    async def test_session_id_selects_correct_file_over_newer(self, tmp_path):
        """When expected_session_id is provided, selects matching file regardless of ctime."""
        import json

        session_a = "session-aaa-target"
        session_b = "session-bbb-newer"

        # Create session A first
        file_a = tmp_path / f"{session_a}.jsonl"
        file_a.write_text(
            json.dumps({"type": "assistant", "message": {"content": "done\n\nMARKER"}}) + "\n"
        )

        # Create session B slightly later (newer by ctime)
        await anyio.sleep(0.05)
        file_b = tmp_path / f"{session_b}.jsonl"
        file_b.write_text(
            json.dumps({"type": "assistant", "message": {"content": "done\n\nMARKER"}}) + "\n"
        )

        result = await _session_log_monitor(
            tmp_path,
            "MARKER",
            stale_threshold=10.0,
            spawn_time=time.time() - 2,
            expected_session_id=session_a,
        )
        assert result.status == "completion"
        assert result.session_id == session_a

    @pytest.mark.anyio
    async def test_session_id_falls_back_to_recency_when_no_match(self, tmp_path):
        """When expected_session_id doesn't match any file, falls back to newest."""
        import json

        session_b = "session-bbb-only"
        file_b = tmp_path / f"{session_b}.jsonl"
        file_b.write_text(
            json.dumps({"type": "assistant", "message": {"content": "done\n\nMARKER"}}) + "\n"
        )

        result = await _session_log_monitor(
            tmp_path,
            "MARKER",
            stale_threshold=10.0,
            spawn_time=time.time() - 2,
            expected_session_id="nonexistent-session-id",
        )
        assert result.status == "completion"
        assert result.session_id == session_b


class TestSessionLogMonitorDirMissing:
    """DIR_MISSING: _session_log_monitor returns immediately when dir is absent."""

    @pytest.mark.anyio
    async def test_session_log_monitor_returns_dir_missing_when_dir_absent(self, tmp_path):
        """When session_log_dir does not exist, monitor returns DIR_MISSING immediately
        instead of burning phase1_timeout absorbing OSError."""
        nonexistent = tmp_path / "does_not_exist"  # NOT created
        t0 = time.monotonic()
        result = await _session_log_monitor(
            nonexistent,
            "MARKER",
            stale_threshold=10.0,
            spawn_time=time.time() - 1,
            _phase1_timeout=5.0,
            _phase1_poll=0.01,  # fast poll so DIR_MISSING returns within one cycle
        )
        elapsed = time.monotonic() - t0
        assert result.status == ChannelBStatus.DIR_MISSING
        assert elapsed < 0.1  # FileNotFoundError is immediate after the poll sleep
        assert result.session_id == ""
