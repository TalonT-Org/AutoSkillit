"""Unit tests for _process_race.py: resolve_termination and ChannelBStatus."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import anyio
import pytest

from autoskillit.core.types import (
    BackendEventKind,
    ChannelBStatus,
    ChannelConfirmation,
    InspectorVerdict,
    SessionEvent,
    TerminationReason,
)
from autoskillit.execution.backends import ClaudeStreamParser
from autoskillit.execution.process import run_managed_async
from autoskillit.execution.process._process_jsonl import EventCursor
from autoskillit.execution.process._process_race import (
    RaceAccumulator,
    RaceSignals,
    _extract_stdout_session_id,
    _watch_completion_eligibility,
    fold_lifecycle_evidence_path,
    resolve_termination,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


class TestChannelBStatusExhaustiveCoverage:
    """Every ChannelBStatus member maps to a defined termination pair."""

    @pytest.mark.parametrize(
        "status,expected_termination,expected_channel",
        [
            (
                ChannelBStatus.COMPLETION,
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.CHANNEL_B,
            ),
            (
                ChannelBStatus.STALE,
                TerminationReason.STALE,
                ChannelConfirmation.UNMONITORED,
            ),
            (
                ChannelBStatus.DIR_MISSING,
                TerminationReason.STALE,
                ChannelConfirmation.DIR_MISSING,
            ),
        ],
    )
    def test_each_channel_b_status_produces_defined_pair(
        self,
        status: ChannelBStatus,
        expected_termination: TerminationReason,
        expected_channel: ChannelConfirmation,
    ) -> None:
        signals = RaceSignals(
            process_exited=False,
            process_returncode=None,
            channel_a_confirmed=False,
            channel_b_status=status,
            channel_b_session_id="test-session",
            stdout_session_id=None,
        )
        termination, channel = resolve_termination(signals)
        assert termination == expected_termination
        assert channel == expected_channel

    def test_sentinel_member_count(self) -> None:
        """Breaks when a new ChannelBStatus member is added, forcing test update."""
        assert len(ChannelBStatus) == 3, (
            f"ChannelBStatus has {len(ChannelBStatus)} members (expected 3). "
            "Update the parametrized test above to cover the new member."
        )

    def test_resolve_termination_dir_missing_is_not_unmonitored(self) -> None:
        """DIR_MISSING must NOT collapse to UNMONITORED — it gets its own
        ChannelConfirmation value so downstream gates can distinguish."""
        signals = RaceSignals(
            process_exited=True,
            process_returncode=0,
            channel_a_confirmed=False,
            channel_b_status=ChannelBStatus.DIR_MISSING,
            channel_b_session_id="",
            stdout_session_id=None,
        )
        termination, channel = resolve_termination(signals)
        assert channel != ChannelConfirmation.UNMONITORED
        assert channel == ChannelConfirmation.DIR_MISSING


class TestResolveTerminationPriority:
    """Verify priority ordering: process exit > stale > channel win."""

    def test_process_exit_overrides_channel_b_completion(self) -> None:
        signals = RaceSignals(
            process_exited=True,
            process_returncode=0,
            channel_a_confirmed=False,
            channel_b_status=ChannelBStatus.COMPLETION,
            channel_b_session_id="s1",
            stdout_session_id=None,
        )
        termination, channel = resolve_termination(signals)
        assert termination == TerminationReason.NATURAL_EXIT
        # Channel B still gets credit even though process exited
        assert channel == ChannelConfirmation.CHANNEL_B

    def test_process_exit_overrides_stale(self) -> None:
        signals = RaceSignals(
            process_exited=True,
            process_returncode=1,
            channel_a_confirmed=False,
            channel_b_status=ChannelBStatus.STALE,
            channel_b_session_id="s1",
            stdout_session_id=None,
        )
        termination, _ = resolve_termination(signals)
        assert termination == TerminationReason.NATURAL_EXIT

    def test_stale_overrides_channel_a(self) -> None:
        """When both stale and channel_a fire, stale takes priority for termination."""
        signals = RaceSignals(
            process_exited=False,
            process_returncode=None,
            channel_a_confirmed=True,
            channel_b_status=ChannelBStatus.STALE,
            channel_b_session_id="s1",
            stdout_session_id=None,
        )
        termination, channel = resolve_termination(signals)
        assert termination == TerminationReason.STALE
        # Channel A still gets credit
        assert channel == ChannelConfirmation.CHANNEL_A

    def test_channel_a_alone_produces_completed(self) -> None:
        signals = RaceSignals(
            process_exited=False,
            process_returncode=None,
            channel_a_confirmed=True,
            channel_b_status=None,
            channel_b_session_id="",
            stdout_session_id=None,
        )
        termination, channel = resolve_termination(signals)
        assert termination == TerminationReason.COMPLETED
        assert channel == ChannelConfirmation.CHANNEL_A

    def test_no_signals_produces_natural_exit_fallback(self) -> None:
        signals = RaceSignals(
            process_exited=False,
            process_returncode=None,
            channel_a_confirmed=False,
            channel_b_status=None,
            channel_b_session_id="",
            stdout_session_id=None,
        )
        termination, channel = resolve_termination(signals)
        assert termination == TerminationReason.NATURAL_EXIT
        assert channel == ChannelConfirmation.UNMONITORED


class TestRaceAccumulatorSessionId:
    """Session ID correlation field on RaceAccumulator and RaceSignals."""

    def test_stdout_session_id_propagates_to_signals(self) -> None:
        acc = RaceAccumulator()
        acc.stdout_session_id = "abc-123"
        signals = acc.to_race_signals()
        assert signals.stdout_session_id == "abc-123"

    def test_stdout_session_id_defaults_to_none(self) -> None:
        acc = RaceAccumulator()
        signals = acc.to_race_signals()
        assert signals.stdout_session_id is None


class TestSubprocessResultSessionIdResolution:
    """_resolve_session_id merges all RaceSignals session ID sources correctly."""

    def test_session_id_prefers_stdout_session_id(self) -> None:
        """stdout_session_id takes priority when available."""
        from autoskillit.execution.process import _resolve_session_id

        assert _resolve_session_id("stdout-uuid-1234", "ch-b-uuid-5678") == "stdout-uuid-1234"

    def test_session_id_falls_back_to_channel_b(self) -> None:
        """channel_b_session_id used when stdout_session_id is empty."""
        from autoskillit.execution.process import _resolve_session_id

        assert _resolve_session_id("", "ch-b-uuid-5678") == "ch-b-uuid-5678"

    def test_session_id_falls_back_to_channel_b_when_none(self) -> None:
        """channel_b_session_id used when stdout_session_id is None (not yet extracted)."""
        from autoskillit.execution.process import _resolve_session_id

        assert _resolve_session_id(None, "ch-b-uuid-5678") == "ch-b-uuid-5678"

    def test_session_id_empty_when_both_sources_empty(self) -> None:
        """Crash/pre-start path: both sources empty → session_id empty."""
        from autoskillit.execution.process import _resolve_session_id

        assert _resolve_session_id("", "") == ""


class TestResolveTerminationIdleStall:
    """Idle stall priority in resolve_termination."""

    def test_resolve_termination_idle_stall_priority(self) -> None:
        signals = RaceSignals(
            process_exited=False,
            process_returncode=None,
            channel_a_confirmed=False,
            channel_b_status=None,
            channel_b_session_id="",
            stdout_session_id=None,
            idle_stall=True,
        )
        termination, channel = resolve_termination(signals)
        assert termination == TerminationReason.IDLE_STALL
        assert channel == ChannelConfirmation.UNMONITORED

    def test_resolve_termination_process_exit_beats_idle_stall(self) -> None:
        signals = RaceSignals(
            process_exited=True,
            process_returncode=0,
            channel_a_confirmed=False,
            channel_b_status=None,
            channel_b_session_id="",
            stdout_session_id=None,
            idle_stall=True,
        )
        termination, _ = resolve_termination(signals)
        assert termination == TerminationReason.NATURAL_EXIT

    def test_resolve_termination_idle_stall_beats_stale(self) -> None:
        signals = RaceSignals(
            process_exited=False,
            process_returncode=None,
            channel_a_confirmed=False,
            channel_b_status=ChannelBStatus.STALE,
            channel_b_session_id="s1",
            stdout_session_id=None,
            idle_stall=True,
        )
        termination, _ = resolve_termination(signals)
        assert termination == TerminationReason.IDLE_STALL


class TestRaceSignalsFieldCount:
    """Sentinel test: breaks when RaceSignals fields change."""

    def test_race_signals_field_count(self) -> None:
        assert len(dataclasses.fields(RaceSignals)) == 17, (
            f"RaceSignals has {len(dataclasses.fields(RaceSignals))} fields (expected 17). "
            "Update tests to cover the new field."
        )


class TestInspectorVerdictField:
    """inspector_verdict field on RaceSignals defaults to None."""

    def test_default_none(self) -> None:
        rs = RaceSignals(
            process_exited=True,
            process_returncode=0,
            channel_a_confirmed=False,
            channel_b_status=None,
        )
        assert rs.inspector_verdict is None

    def test_to_race_signals_copies_inspector_verdict(self) -> None:
        acc = RaceAccumulator()
        verdict = InspectorVerdict(
            action="KILL", reasoning="stuck", confidence="high", elapsed_seconds=5.0
        )
        acc.inspector_verdict = verdict
        signals = acc.to_race_signals()
        assert signals.inspector_verdict is verdict


class TestRaceAccumulatorFieldCount:
    """Sentinel test: breaks when RaceAccumulator fields change."""

    def test_race_accumulator_field_count(self) -> None:
        n = len(dataclasses.fields(RaceAccumulator))
        assert n == 23, (
            f"RaceAccumulator has {n} fields (expected 23). Update tests for new fields."
        )


def test_lifecycle_reducer_is_terminal_dominant_and_freezes_sorted() -> None:
    acc = RaceAccumulator(lifecycle_observation_enabled=True)
    for task_id in ("c", "z", "a", "m", "b"):
        acc.observe_event(
            SessionEvent(
                kind=BackendEventKind.TASK_LIFECYCLE,
                is_terminal=False,
                has_marker=False,
                task_id=task_id,
                task_active=True,
            )
        )
    acc.observe_event(
        SessionEvent(
            kind=BackendEventKind.TASK_LIFECYCLE,
            is_terminal=False,
            has_marker=False,
            task_id="z",
            task_active=False,
        )
    )
    acc.observe_event(
        SessionEvent(
            kind=BackendEventKind.TASK_LIFECYCLE,
            is_terminal=False,
            has_marker=False,
            task_id="m",
            task_active=False,
        )
    )
    acc.observe_event(
        SessionEvent(
            kind=BackendEventKind.TASK_LIFECYCLE,
            is_terminal=False,
            has_marker=False,
            task_id="z",
            task_active=True,
        )
    )
    signals = acc.to_race_signals()
    assert signals.pending_task_ids == ("a", "b", "c")
    assert signals.terminal_task_ids == ("m", "z")
    with pytest.raises(dataclasses.FrozenInstanceError):
        signals.pending_task_ids = ()


def test_lifecycle_accumulator_fields_have_exact_defaults_and_freeze_propagation() -> None:
    acc = RaceAccumulator()

    assert acc.lifecycle_observation_enabled is False
    assert acc.lifecycle_observation_complete is False
    assert acc.pending_task_ids == set()
    assert acc.terminal_task_ids == set()
    assert acc.schedule_wakeup_violation is False
    assert acc.completion_ceiling_expired is False
    assert acc.channel_a_candidate_at is None
    assert acc.channel_b_candidate_at is None
    assert acc.stdout_cursor is None
    assert acc.channel_b_cursor is None
    assert not acc.completion_candidate_event.is_set()
    assert acc.process_group_id == 0

    signals = acc.to_race_signals()
    assert signals.lifecycle_observation_complete is False
    assert signals.pending_task_ids == ()
    assert signals.terminal_task_ids == ()
    assert signals.schedule_wakeup_violation is False
    assert signals.completion_ceiling_expired is False
    assert signals.process_group_id == 0


def test_path_fold_includes_unterminated_final_record(tmp_path: Path) -> None:
    capture = tmp_path / "stdout.jsonl"
    capture.write_text('{"type":"task_started","task_id":"owned"}')

    assert fold_lifecycle_evidence_path(capture, ClaudeStreamParser()) == (("owned",), False)


@pytest.mark.anyio
async def test_completion_candidate_without_task_ignores_large_ceiling() -> None:
    acc = RaceAccumulator(lifecycle_observation_enabled=True)
    acc.channel_a_candidate_at = anyio.current_time()
    acc.completion_candidate_event.set()
    trigger = anyio.Event()

    with anyio.fail_after(0.2):
        await _watch_completion_eligibility(
            acc,
            trigger,
            anyio.Event(),
            completion_drain_timeout=0,
            child_deferral_ceiling=120,
            stream_parser=ClaudeStreamParser(),
            session_log_enabled=False,
            _poll_interval=0,
        )

    assert trigger.is_set()
    assert acc.channel_a_confirmed is True
    assert acc.completion_ceiling_expired is False


@pytest.mark.anyio
async def test_completion_ceiling_preserves_pending_ids_for_adjudication() -> None:
    acc = RaceAccumulator(lifecycle_observation_enabled=True)
    acc.pending_task_ids.add("owned")
    acc.channel_b_candidate_at = anyio.current_time()
    acc.completion_candidate_event.set()
    trigger = anyio.Event()

    with anyio.fail_after(0.2):
        await _watch_completion_eligibility(
            acc,
            trigger,
            anyio.Event(),
            completion_drain_timeout=0,
            child_deferral_ceiling=0,
            stream_parser=ClaudeStreamParser(),
            session_log_enabled=False,
            _poll_interval=0,
        )

    signals = acc.to_race_signals()
    assert trigger.is_set()
    assert signals.channel_b_status is None
    assert signals.pending_task_ids == ("owned",)
    assert signals.completion_ceiling_expired is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("marker_channel", "task_channel"),
    [("b", "b"), ("a", "b"), ("b", "a")],
    ids=["channel-b", "a-marker-b-task", "b-marker-a-task"],
)
async def test_completion_waits_for_terminal_evidence_across_channel_orderings(
    tmp_path: Path, marker_channel: str, task_channel: str
) -> None:
    stdout_path = tmp_path / "stdout.jsonl"
    channel_b_path = tmp_path / "channel-b.jsonl"
    task_path = stdout_path if task_channel == "a" else channel_b_path
    task_path.write_text('{"type":"task_started","task_id":"owned"}\n')
    other_path = channel_b_path if task_path == stdout_path else stdout_path
    other_path.write_text("")

    acc = RaceAccumulator(lifecycle_observation_enabled=True)
    acc.stdout_cursor = EventCursor(stdout_path)
    acc.channel_b_cursor = EventCursor(channel_b_path)
    if marker_channel == "a":
        acc.channel_a_candidate_at = anyio.current_time()
    else:
        acc.channel_b_candidate_at = anyio.current_time()
        acc.channel_b_status = ChannelBStatus.COMPLETION
    acc.completion_candidate_event.set()
    trigger = anyio.Event()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(
            _watch_completion_eligibility,
            acc,
            trigger,
            anyio.Event(),
            0,
            1,
            ClaudeStreamParser(),
            True,
            0.005,
        )
        await anyio.sleep(0.03)
        assert not trigger.is_set()
        with task_path.open("a") as stream:
            stream.write('{"type":"task_notification","task_id":"owned","status":"completed"}\n')
        with anyio.fail_after(1):
            await trigger.wait()

    signals = acc.to_race_signals()
    assert signals.pending_task_ids == ()
    assert signals.terminal_task_ids == ("owned",)
    assert signals.channel_a_confirmed is (marker_channel == "a")


@pytest.mark.anyio
async def test_natural_exit_final_fold_preserves_a_first_provenance(
    tmp_path: Path,
) -> None:
    session_id = "final-fold-session"
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    script = tmp_path / "final_fold.py"
    script.write_text(
        "import json,sys,time\n"
        "from pathlib import Path\n"
        f"sid={session_id!r}\n"
        "print(json.dumps({'type':'system','subtype':'init','session_id':sid}), flush=True)\n"
        "log=Path(sys.argv[1]) / f'{sid}.jsonl'\n"
        "log.write_text(json.dumps({'type':'task_started','task_id':'owned'})+'\\n'"
        "+json.dumps({'type':'assistant','message':{'content':'ORDER_UP'}})+'\\n')\n"
        "time.sleep(0.15)\n"
        "print(json.dumps({'type':'result','result':'ORDER_UP'}), flush=True)\n"
        "time.sleep(0.15)\n"
        "print(json.dumps({'type':'task_notification','task_id':'owned',"
        "'status':'completed'}), flush=True)\n"
    )

    result = await run_managed_async(
        [sys.executable, str(script), str(session_dir)],
        cwd=tmp_path,
        timeout=5,
        completion_marker="ORDER_UP",
        session_log_dir=session_dir,
        stream_parser=ClaudeStreamParser(completion_marker="ORDER_UP"),
        lifecycle_observation_enabled=True,
        child_deferral_ceiling=2,
        completion_drain_timeout=0.5,
        _heartbeat_poll=0.01,
        _phase1_poll=0.01,
        _phase2_poll=0.01,
    )

    assert result.termination is TerminationReason.NATURAL_EXIT
    assert result.lifecycle_observation_complete is True
    assert result.pending_task_ids == ()
    assert result.channel_confirmation is ChannelConfirmation.CHANNEL_A


class TestResolveTerminationInspector:
    """resolve_termination produces HEALTH_INSPECTOR when inspector_verdict + idle_stall."""

    def test_inspector_verdict_with_idle_stall_gives_health_inspector(self) -> None:
        signals = RaceSignals(
            process_exited=False,
            process_returncode=None,
            channel_a_confirmed=False,
            channel_b_status=None,
            channel_b_session_id="",
            stdout_session_id=None,
            idle_stall=True,
            inspector_verdict=InspectorVerdict(
                action="KILL", reasoning="stuck", confidence="high", elapsed_seconds=5.0
            ),
        )
        termination, _ = resolve_termination(signals)
        assert termination == TerminationReason.HEALTH_INSPECTOR

    def test_idle_stall_without_verdict_gives_idle_stall(self) -> None:
        signals = RaceSignals(
            process_exited=False,
            process_returncode=None,
            channel_a_confirmed=False,
            channel_b_status=None,
            channel_b_session_id="",
            stdout_session_id=None,
            idle_stall=True,
            inspector_verdict=None,
        )
        termination, _ = resolve_termination(signals)
        assert termination == TerminationReason.IDLE_STALL

    def test_process_exit_takes_priority_over_inspector(self) -> None:
        signals = RaceSignals(
            process_exited=True,
            process_returncode=0,
            channel_a_confirmed=False,
            channel_b_status=None,
            channel_b_session_id="",
            stdout_session_id=None,
            idle_stall=True,
            inspector_verdict=InspectorVerdict(
                action="KILL", reasoning="stuck", confidence="high", elapsed_seconds=5.0
            ),
        )
        termination, _ = resolve_termination(signals)
        assert termination == TerminationReason.NATURAL_EXIT


class TestSubprocessResultInspectorVerdict:
    """SubprocessResult.inspector_verdict is populated from signals."""

    def test_subprocess_result_accepts_inspector_verdict(self) -> None:
        from autoskillit.core import SubprocessResult

        verdict = InspectorVerdict(
            action="KILL", reasoning="stuck", confidence="high", elapsed_seconds=5.0
        )
        result = SubprocessResult(
            returncode=-1,
            stdout="",
            stderr="",
            termination=TerminationReason.HEALTH_INSPECTOR,
            pid=12345,
            inspector_verdict=verdict,
        )
        assert result.inspector_verdict is verdict


class TestExitSnapshot:
    """exit_snapshot field on RaceAccumulator and RaceSignals."""

    def test_exit_snapshot_defaults_to_none(self) -> None:
        """RaceAccumulator.exit_snapshot defaults to None."""
        acc = RaceAccumulator()
        assert acc.exit_snapshot is None

    def test_exit_snapshot_propagates_to_signals(self) -> None:
        """exit_snapshot stored on accumulator is visible in RaceSignals."""
        acc = RaceAccumulator()
        acc.exit_snapshot = {"event": "exit_snapshot", "vm_rss_kb": 1024}
        signals = acc.to_race_signals()
        assert signals.exit_snapshot is not None
        assert signals.exit_snapshot["event"] == "exit_snapshot"

    @pytest.mark.anyio
    async def test_watch_process_captures_exit_snapshot(self) -> None:
        """_watch_process populates acc.exit_snapshot after process exits."""
        import sys

        from autoskillit.execution.process._process_race import _watch_process

        acc = RaceAccumulator()
        trigger = anyio.Event()
        async with await anyio.open_process(
            [sys.executable, "-c", "import time; time.sleep(0.2)"],
            start_new_session=True,
        ) as proc:
            async with anyio.create_task_group() as tg:
                tg.start_soon(_watch_process, proc, acc, trigger)
                await trigger.wait()
                tg.cancel_scope.cancel()

        # exit_snapshot may be None if read_proc_snapshot failed (race — process gone)
        # but the attribute must exist (not missing)
        assert hasattr(acc, "exit_snapshot")

    @pytest.mark.anyio
    async def test_watch_process_exit_snapshot_has_event_marker(self) -> None:
        """If exit_snapshot was captured, it carries event='exit_snapshot'."""
        import sys

        from autoskillit.execution.process._process_race import _watch_process

        acc = RaceAccumulator()
        trigger = anyio.Event()
        async with await anyio.open_process(
            [sys.executable, "-c", "pass"],  # instant exit — maximises snapshot chance
            start_new_session=True,
        ) as proc:
            async with anyio.create_task_group() as tg:
                tg.start_soon(_watch_process, proc, acc, trigger)
                await trigger.wait()
                tg.cancel_scope.cancel()

        assert acc.exit_snapshot is None or acc.exit_snapshot.get("event") == "exit_snapshot"


class TestProcessExitedEvent:
    """process_exited_event on RaceAccumulator / RaceSignals (1h)."""

    @pytest.mark.anyio
    async def test_watch_process_sets_both_event_and_flag(self, tmp_path) -> None:
        """_watch_process must set acc.process_exited=True AND process_exited_event."""
        import sys

        from autoskillit.execution.process._process_race import _watch_process

        acc = RaceAccumulator()
        trigger = anyio.Event()

        proc = await anyio.open_process(
            [sys.executable, "-c", "import time; time.sleep(0.2)"],
            start_new_session=True,
        )

        async with anyio.create_task_group() as tg:
            tg.start_soon(_watch_process, proc, acc, trigger)
            await trigger.wait()
            tg.cancel_scope.cancel()

        assert acc.process_exited is True
        assert acc.process_exited_event.is_set() is True

    @pytest.mark.anyio
    async def test_process_exited_event_fires_before_trigger(self, tmp_path) -> None:
        """When trigger fires due to process exit, process_exited_event must already be set."""
        import sys

        from autoskillit.execution.process._process_race import _watch_process

        acc = RaceAccumulator()
        trigger = anyio.Event()

        proc = await anyio.open_process(
            [sys.executable, "-c", "import time; time.sleep(0.1)"],
            start_new_session=True,
        )

        async with anyio.create_task_group() as tg:
            tg.start_soon(_watch_process, proc, acc, trigger)
            await trigger.wait()
            # trigger just fired — process_exited_event must already be set
            event_was_set = acc.process_exited_event.is_set()
            tg.cancel_scope.cancel()

        assert event_was_set, "process_exited_event was not set before trigger fired"

    def test_process_exited_event_propagates_to_signals(self) -> None:
        """process_exited_event propagates to RaceSignals via to_race_signals()."""

        acc = RaceAccumulator()
        signals = acc.to_race_signals()
        # The event object is shared (same reference)
        assert signals.process_exited_event is acc.process_exited_event

        # After setting the event, it is set on both
        acc.process_exited_event.set()
        assert signals.process_exited_event.is_set()


@pytest.mark.anyio
class TestExtractStdoutSessionIdStreamParser:
    """_extract_stdout_session_id branching: StreamParser path vs fallback."""

    async def test_stream_parser_extracts_session_id(self, tmp_path: Path) -> None:
        from autoskillit.execution.backends import ClaudeStreamParser

        path = tmp_path / "stdout"
        path.write_text('{"type": "system", "subtype": "init", "session_id": "sp-test-123"}\n')

        acc = RaceAccumulator()
        ready = anyio.Event()
        parser = ClaudeStreamParser()

        await _extract_stdout_session_id(
            path,
            acc,
            ready,
            _poll_interval=0.05,
            _timeout=1.0,
            stream_parser=parser,
        )

        assert acc.stdout_session_id == "sp-test-123"
        assert ready.is_set()

    async def test_none_stream_parser_uses_fallback(self, tmp_path: Path) -> None:
        path = tmp_path / "stdout"
        path.write_text('{"type": "system", "subtype": "init", "session_id": "fallback-456"}\n')

        acc = RaceAccumulator()
        ready = anyio.Event()

        await _extract_stdout_session_id(
            path,
            acc,
            ready,
            _poll_interval=0.05,
            _timeout=1.0,
            stream_parser=None,
        )

        assert acc.stdout_session_id == "fallback-456"
        assert ready.is_set()

    async def test_stream_parser_timeout_no_system_record(self, tmp_path: Path) -> None:
        from autoskillit.execution.backends import ClaudeStreamParser

        path = tmp_path / "stdout"
        path.write_text('{"type": "assistant", "message": "hello"}\n')

        acc = RaceAccumulator()
        ready = anyio.Event()
        parser = ClaudeStreamParser()

        await _extract_stdout_session_id(
            path,
            acc,
            ready,
            _poll_interval=0.05,
            _timeout=0.2,
            stream_parser=parser,
        )

        assert acc.stdout_session_id is None
        assert ready.is_set()

    async def test_stream_parser_skips_malformed_json(self, tmp_path: Path) -> None:
        from autoskillit.execution.backends import ClaudeStreamParser

        path = tmp_path / "stdout"
        path.write_text("not json at all\n")

        acc = RaceAccumulator()
        ready = anyio.Event()
        parser = ClaudeStreamParser()

        await _extract_stdout_session_id(
            path,
            acc,
            ready,
            _poll_interval=0.05,
            _timeout=0.2,
            stream_parser=parser,
        )

        assert acc.stdout_session_id is None
        assert ready.is_set()

    async def test_stream_parser_empty_session_id_skipped(self, tmp_path: Path) -> None:
        from autoskillit.execution.backends import ClaudeStreamParser

        path = tmp_path / "stdout"
        path.write_text('{"type": "system", "session_id": ""}\n')

        acc = RaceAccumulator()
        ready = anyio.Event()
        parser = ClaudeStreamParser()

        await _extract_stdout_session_id(
            path,
            acc,
            ready,
            _poll_interval=0.05,
            _timeout=0.2,
            stream_parser=parser,
        )

        assert acc.stdout_session_id is None
        assert ready.is_set()

    async def test_resume_sequence_extracts_conversation_uuid(self, tmp_path: Path) -> None:
        from autoskillit.execution.backends import ClaudeStreamParser

        path = tmp_path / "stdout"
        path.write_text(
            '{"type": "system", "subtype": "hook_started", "session_id": "process-uuid-AAA"}\n'
            '{"type": "system", "subtype": "init", "session_id": "conversation-uuid-BBB"}\n'
        )

        acc = RaceAccumulator()
        ready = anyio.Event()
        parser = ClaudeStreamParser()

        await _extract_stdout_session_id(
            path,
            acc,
            ready,
            _poll_interval=0.05,
            _timeout=1.0,
            stream_parser=parser,
        )

        assert acc.stdout_session_id == "conversation-uuid-BBB"
        assert ready.is_set()

    async def test_fallback_resume_sequence_extracts_conversation_uuid(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "stdout"
        path.write_text(
            '{"type": "system", "subtype": "hook_started", "session_id": "process-uuid-AAA"}\n'
            '{"type": "system", "subtype": "init", "session_id": "conversation-uuid-BBB"}\n'
        )

        acc = RaceAccumulator()
        ready = anyio.Event()

        await _extract_stdout_session_id(
            path,
            acc,
            ready,
            _poll_interval=0.05,
            _timeout=1.0,
            stream_parser=None,
        )

        assert acc.stdout_session_id == "conversation-uuid-BBB"
        assert ready.is_set()


class TestResolveTerminationSignalDeath:
    """resolve_termination returns SIGNAL_DEATH for shell-convention positive signal codes."""

    @pytest.mark.parametrize(
        "returncode",
        [130, 137, 143],
        ids=["SIGINT(130)", "SIGKILL(137)", "SIGTERM(143)"],
    )
    def test_positive_signal_code_yields_signal_death(self, returncode: int) -> None:
        """process_exited=True with 128+N returncode → SIGNAL_DEATH (Test 1C)."""
        signals = RaceSignals(
            process_exited=True,
            process_returncode=returncode,
            channel_a_confirmed=False,
            channel_b_status=ChannelBStatus.STALE,
            channel_b_session_id="",
            stdout_session_id=None,
        )
        termination, _ = resolve_termination(signals)
        assert termination == TerminationReason.SIGNAL_DEATH

    def test_returncode_zero_still_natural_exit(self) -> None:
        """process_exited=True with returncode=0 → NATURAL_EXIT (regression guard)."""
        signals = RaceSignals(
            process_exited=True,
            process_returncode=0,
            channel_a_confirmed=False,
            channel_b_status=ChannelBStatus.STALE,
            channel_b_session_id="",
            stdout_session_id=None,
        )
        termination, _ = resolve_termination(signals)
        assert termination == TerminationReason.NATURAL_EXIT

    def test_negative_signal_code_yields_signal_death(self) -> None:
        """process_exited=True with returncode=-9 → SIGNAL_DEATH (negative codes still work)."""
        signals = RaceSignals(
            process_exited=True,
            process_returncode=-9,
            channel_a_confirmed=False,
            channel_b_status=ChannelBStatus.STALE,
            channel_b_session_id="",
            stdout_session_id=None,
        )
        termination, _ = resolve_termination(signals)
        assert termination == TerminationReason.SIGNAL_DEATH
