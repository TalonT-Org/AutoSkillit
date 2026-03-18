"""Unit tests for _process_race.py: resolve_termination and ChannelBStatus."""

from __future__ import annotations

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
        assert len(ChannelBStatus) == 2, (
            f"ChannelBStatus has {len(ChannelBStatus)} members (expected 2). "
            "Update the parametrized test above to cover the new member."
        )


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
