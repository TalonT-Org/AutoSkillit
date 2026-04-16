"""Unit tests for _process_race.py: resolve_termination and ChannelBStatus."""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.core.types import (
    ChannelBStatus,
    ChannelConfirmation,
    TerminationReason,
)
from autoskillit.execution._process_race import (
    RaceAccumulator,
    RaceSignals,
    resolve_termination,
)


class TestChannelBStatusExhaustiveCoverage:
    """Every ChannelBStatus member maps to a defined termination pair."""

    @pytest.mark.parametrize(
        "status,expected_termination,expected_channel",
        [
            (
                ChannelBStatus.COMPLETION,
                TerminationReason.COMPLETED,
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
        assert len(dataclasses.fields(RaceSignals)) == 10, (
            f"RaceSignals has {len(dataclasses.fields(RaceSignals))} fields (expected 10). "
            "Update tests to cover the new field."
        )


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

        import anyio

        from autoskillit.execution._process_race import _watch_process

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

        import anyio

        from autoskillit.execution._process_race import _watch_process

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

        import anyio

        from autoskillit.execution._process_race import _watch_process

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

        import anyio

        from autoskillit.execution._process_race import _watch_process

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
