"""Unit tests for _session_log_monitor and related session log monitoring behavior."""

from __future__ import annotations

import time

import anyio
import pytest

from autoskillit.core.types import ChannelBStatus
from autoskillit.execution.process import (
    RaceAccumulator,
    _session_log_monitor,
    _watch_session_log,
)
from autoskillit.execution.process._process_monitor import SessionMonitorResult

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

        assert monitor_result[0].status == ChannelBStatus.COMPLETION
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

        assert result.status == ChannelBStatus.STALE
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
        assert monitor_result[0].status == ChannelBStatus.STALE

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

        assert monitor_result[0].status == ChannelBStatus.COMPLETION

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

        assert monitor_result[0].status == ChannelBStatus.COMPLETION


class TestSessionLogMonitorSessionId:
    """_session_log_monitor returns SessionMonitorResult with session ID from filename."""

    @pytest.mark.anyio
    async def test_session_log_monitor_returns_session_id_from_filename(self, tmp_path):
        """_session_log_monitor returns the JSONL filename stem as session_id."""
        import json

        session_uuid = "d9adcc78-3098-4c3e-8720-ddcf3da35fff"
        jsonl_file = tmp_path / f"{session_uuid}.jsonl"
        jsonl_file.write_text(
            json.dumps({"type": "assistant", "message": {"content": "initial"}}) + "\n"
        )

        async def append_marker():
            await anyio.sleep(0.2)
            with jsonl_file.open("a") as f:
                f.write(
                    json.dumps(
                        {"type": "assistant", "message": {"content": "done\n\nCOMPLETION_MARKER"}}
                    )
                    + "\n"
                )

        result_box: list[SessionMonitorResult] = []

        async def run_monitor():
            result_box.append(
                await _session_log_monitor(
                    tmp_path,
                    "COMPLETION_MARKER",
                    stale_threshold=10.0,
                    spawn_time=time.time() - 1,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                )
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(run_monitor)
            tg.start_soon(append_marker)

        assert result_box[0].status == ChannelBStatus.COMPLETION
        assert result_box[0].session_id == session_uuid

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

        assert result.status == ChannelBStatus.STALE
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

        assert result.status == ChannelBStatus.STALE
        assert result.session_id == ""

    @pytest.mark.anyio
    async def test_session_log_monitor_status_is_channel_b_status_enum(self, tmp_path):
        """SessionMonitorResult.status is a ChannelBStatus enum member."""
        import json

        from autoskillit.core.types import ChannelBStatus

        session_uuid = "enum-check-session"
        jsonl_file = tmp_path / f"{session_uuid}.jsonl"
        jsonl_file.write_text(
            json.dumps({"type": "assistant", "message": {"content": "initial"}}) + "\n"
        )

        async def append_marker():
            await anyio.sleep(0.2)
            with jsonl_file.open("a") as f:
                f.write(
                    json.dumps({"type": "assistant", "message": {"content": "done\n\nMARKER"}})
                    + "\n"
                )

        result_box: list[SessionMonitorResult] = []

        async def run_monitor():
            result_box.append(
                await _session_log_monitor(
                    tmp_path,
                    "MARKER",
                    stale_threshold=10.0,
                    spawn_time=time.time() - 1,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                )
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(run_monitor)
            tg.start_soon(append_marker)

        assert isinstance(result_box[0].status, ChannelBStatus)


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

        # Create session A first with non-marker content
        file_a = tmp_path / f"{session_a}.jsonl"
        file_a.write_text(
            json.dumps({"type": "assistant", "message": {"content": "initial"}}) + "\n"
        )

        # Create session B slightly later (newer by ctime)
        await anyio.sleep(0.05)
        file_b = tmp_path / f"{session_b}.jsonl"
        file_b.write_text(
            json.dumps({"type": "assistant", "message": {"content": "initial"}}) + "\n"
        )

        async def append_marker():
            await anyio.sleep(0.2)
            with file_a.open("a") as f:
                f.write(
                    json.dumps({"type": "assistant", "message": {"content": "done\n\nMARKER"}})
                    + "\n"
                )

        result_box: list[SessionMonitorResult] = []

        async def run_monitor():
            result_box.append(
                await _session_log_monitor(
                    tmp_path,
                    "MARKER",
                    stale_threshold=10.0,
                    spawn_time=time.time() - 2,
                    expected_session_id=session_a,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                )
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(run_monitor)
            tg.start_soon(append_marker)

        assert result_box[0].status == ChannelBStatus.COMPLETION
        assert result_box[0].session_id == session_a

    @pytest.mark.anyio
    async def test_session_id_falls_back_to_recency_when_no_match(self, tmp_path):
        """When expected_session_id doesn't match any file, falls back to newest."""
        import json

        session_b = "session-bbb-only"
        file_b = tmp_path / f"{session_b}.jsonl"
        file_b.write_text(
            json.dumps({"type": "assistant", "message": {"content": "initial"}}) + "\n"
        )

        async def append_marker():
            await anyio.sleep(0.2)
            with file_b.open("a") as f:
                f.write(
                    json.dumps({"type": "assistant", "message": {"content": "done\n\nMARKER"}})
                    + "\n"
                )

        result_box: list[SessionMonitorResult] = []

        async def run_monitor():
            result_box.append(
                await _session_log_monitor(
                    tmp_path,
                    "MARKER",
                    stale_threshold=10.0,
                    spawn_time=time.time() - 2,
                    expected_session_id="nonexistent-session-id",
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                )
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(run_monitor)
            tg.start_soon(append_marker)

        assert result_box[0].status == ChannelBStatus.COMPLETION
        assert result_box[0].session_id == session_b


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
        assert elapsed < 2.0  # DIR_MISSING must fire before phase1_timeout (5.0s)
        assert result.session_id == ""


class TestResumeBoundary:
    """Phase 2 must not fire on completion markers that existed before monitoring began."""

    @pytest.mark.anyio
    async def test_monitor_skips_preexisting_completion_marker(self, tmp_path):
        """Phase 2 must NOT fire on a completion marker that existed before monitoring began.

        Reproduces the resume-boundary false-fire: on `claude --resume`, the JSONL
        file already contains the completion marker from the prior session.
        """
        import json

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        marker = "%%L3_DONE::abcd1234%%"
        spawn_time = time.time() - 10

        session_file = log_dir / "session-abc.jsonl"
        # Pre-populate with a completion marker from the "prior session"
        session_file.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": f"Done\n\n{marker}"}],
                    },
                }
            )
            + "\n"
        )

        poll_count = 0

        def count_polls():
            nonlocal poll_count
            poll_count += 1

        async def append_activity():
            for i in range(3):
                await anyio.sleep(0.1)
                with session_file.open("a") as f:
                    f.write(
                        json.dumps(
                            {
                                "type": "assistant",
                                "message": {"role": "assistant", "content": f"resumed msg {i}"},
                            }
                        )
                        + "\n"
                    )

        result_box: list[SessionMonitorResult] = []

        async def run_monitor():
            result_box.append(
                await _session_log_monitor(
                    log_dir,
                    marker,
                    stale_threshold=1.0,
                    spawn_time=spawn_time,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                    _on_poll=count_polls,
                )
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(run_monitor)
            tg.start_soon(append_activity)

        assert result_box[0].status == ChannelBStatus.STALE
        assert poll_count > 2, "Monitor should have polled multiple times, not fired immediately"

    @pytest.mark.anyio
    async def test_monitor_fires_on_new_marker_after_preexisting_content(self, tmp_path):
        """Phase 2 fires on a new marker written after monitoring starts,
        even when the file has substantial pre-existing content."""
        import json

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        marker = "%%L3_DONE::efgh5678%%"
        spawn_time = time.time() - 10

        session_file = log_dir / "session-def.jsonl"
        lines = []
        for i in range(10):
            lines.append(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"role": "assistant", "content": f"prior msg {i}"},
                    }
                )
            )
        session_file.write_text("\n".join(lines) + "\n")

        async def append_new_marker():
            await anyio.sleep(0.3)
            with session_file.open("a") as f:
                f.write(
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "content": f"Completed\n\n{marker}",
                            },
                        }
                    )
                    + "\n"
                )

        result_box: list[SessionMonitorResult] = []

        async def run_monitor():
            result_box.append(
                await _session_log_monitor(
                    log_dir,
                    marker,
                    stale_threshold=30,
                    spawn_time=spawn_time,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                )
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(run_monitor)
            tg.start_soon(append_new_marker)

        assert result_box[0].status == ChannelBStatus.COMPLETION

    @pytest.mark.anyio
    async def test_phase2_no_spurious_read_on_preexisting_content(self, tmp_path):
        """When file has pre-existing content and no new writes occur,
        Phase 2 should go stale without ever triggering a content read."""
        import json

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        marker = "%%L3_DONE::ijkl9012%%"
        spawn_time = time.time() - 10

        session_file = log_dir / "session-ghi.jsonl"
        session_file.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": f"Prior session done\n\n{marker}"}],
                    },
                }
            )
            + "\n"
        )

        result = await _session_log_monitor(
            log_dir,
            marker,
            stale_threshold=0.2,
            spawn_time=spawn_time,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
        )

        assert result.status == ChannelBStatus.STALE


@pytest.mark.anyio
async def test_watch_session_log_passes_marker_dir_to_monitor_kwargs(
    tmp_path: anyio.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """marker_dir and session_id are injected into _monitor_kwargs."""
    captured_kwargs: dict[str, object] = {}

    async def fake_session_log_monitor(*args, **kwargs) -> SessionMonitorResult:
        captured_kwargs.update(kwargs)
        return SessionMonitorResult(status=ChannelBStatus.STALE, session_id="")

    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._session_log_monitor",
        fake_session_log_monitor,
    )

    acc = RaceAccumulator()
    trigger = anyio.Event()
    channel_b_ready = anyio.Event()

    session_file = tmp_path / "session.jsonl"
    await anyio.Path(session_file).write_bytes(b"")

    with anyio.fail_after(3.0):
        await _watch_session_log(
            session_log_dir=tmp_path,
            completion_marker="DONE",
            stale_threshold=60.0,
            spawn_time=time.time(),
            session_record_types=frozenset({"assistant"}),
            pid=12345,
            completion_drain_timeout=1.0,
            acc=acc,
            trigger=trigger,
            channel_b_ready=channel_b_ready,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
            _phase1_timeout=0.1,
            max_suppression_seconds=1800.0,
            marker_dir=tmp_path,
            session_id="parent-session",
        )

    assert captured_kwargs["marker_dir"] == tmp_path
    assert captured_kwargs["caller_session_id"] == "parent-session"


@pytest.mark.anyio
async def test_watch_session_log_omits_marker_kwargs_when_none(
    tmp_path: anyio.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """marker_dir=None and session_id=None — keys absent from _monitor_kwargs."""
    captured_kwargs: dict[str, object] = {}

    async def fake_session_log_monitor(*args, **kwargs) -> SessionMonitorResult:
        captured_kwargs.update(kwargs)
        return SessionMonitorResult(status=ChannelBStatus.STALE, session_id="")

    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._session_log_monitor",
        fake_session_log_monitor,
    )

    acc = RaceAccumulator()
    trigger = anyio.Event()
    channel_b_ready = anyio.Event()

    session_file = tmp_path / "session.jsonl"
    await anyio.Path(session_file).write_bytes(b"")

    with anyio.fail_after(3.0):
        await _watch_session_log(
            session_log_dir=tmp_path,
            completion_marker="DONE",
            stale_threshold=60.0,
            spawn_time=time.time(),
            session_record_types=frozenset({"assistant"}),
            pid=12345,
            completion_drain_timeout=1.0,
            acc=acc,
            trigger=trigger,
            channel_b_ready=channel_b_ready,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
            _phase1_timeout=0.1,
        )

    assert "marker_dir" not in captured_kwargs
    assert "caller_session_id" not in captured_kwargs
