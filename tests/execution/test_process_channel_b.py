"""Integration tests for Channel B drain-race and COMPLETED pipeline adjudication."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import anyio
import pytest

from autoskillit.core.types import (
    ChannelConfirmation,
    CompletionCandidateSource,
    CompletionCandidateState,
    LifecycleDecision,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.process import run_managed_async
from tests.execution.conftest import WRITE_RESULT_THEN_HANG_SCRIPT

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

# Script that:
#   (1) writes %%ORDER_UP%% to a JSONL session file (Channel B fires)
#   (2) writes type=result to stdout after a delay (Channel A confirms within drain window)
#   (3) hangs until killed
# Pass session_dir as sys.argv[1].
CHANNEL_B_THEN_A_CONFIRM_SCRIPT = textwrap.dedent("""\
    import sys, time, json, os
    session_dir = sys.argv[1]
    os.makedirs(session_dir, exist_ok=True)
    sys.stdout.write(json.dumps({"type": "system", "subtype": "init",
                                "session_id": "stdout-session"}) + "\\n")
    sys.stdout.flush()
    # Small delay to ensure file ctime > spawn_time recorded in run_managed_async
    time.sleep(0.1)
    jsonl_path = os.path.join(session_dir, "channel-b-log-session.jsonl")
    with open(jsonl_path, "w") as f:
        init = {"type": "assistant", "message": {"role": "assistant",
                "content": "working..."}}
        f.write(json.dumps(init) + "\\n")
        f.flush()
    # Delay must exceed session_id_timeout + Phase 1 poll so Phase 2 initializes
    # scan_pos from discovery boundary before the marker arrives.
    # 3s margin handles xdist -n 4 event-loop saturation on WSL2 where coroutine
    # scheduling jitter can delay Phase 1 discovery by >1s.
    time.sleep(3.0)
    channel_a_record = {"type": "assistant", "uuid": "parent-channel-b-then-a",
                        "session_id": "channel-a-candidate-session",
                        "message": {"id": "message-channel-a",
                        "role": "assistant", "content": [{"type": "text",
                        "text": "%%ORDER_UP%%"}]}}
    sys.stdout.write(json.dumps(channel_a_record) + "\\n")
    sys.stdout.flush()
    with open(jsonl_path, "a") as f:
        record = {"type": "assistant", "uuid": "parent-channel-b-then-a",
                  "session_id": "channel-b-candidate-session",
                  "message": {"id": "message-channel-b",
                  "role": "assistant", "content": [{"type": "text",
                  "text": "%%ORDER_UP%%"}]}}
        f.write(json.dumps(record) + "\\n")
        f.flush()
    # Wait until after Channel B fires (phase1_poll + phase2_poll), then write stdout.
    # Callers pass this delay as sys.argv[2]; default 4.0 matches production poll defaults.
    time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 4.0)
    result = {"type": "result", "subtype": "success", "is_error": False,
              "result": "done", "session_id": "result-envelope-session"}
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\\n")
    sys.stdout.flush()
    time.sleep(3600)
""")

# Script that writes %%ORDER_UP%% to session JSONL but never writes type=result to stdout.
# Simulates CLI hung post-completion — drain timeout should expire and kill anyway.
# Pass session_dir as sys.argv[1].
CHANNEL_B_NO_STDOUT_SCRIPT = textwrap.dedent("""\
    import sys, time, json, os
    session_dir = sys.argv[1]
    os.makedirs(session_dir, exist_ok=True)
    time.sleep(0.1)
    jsonl_path = os.path.join(session_dir, "session.jsonl")
    with open(jsonl_path, "w") as f:
        init = {"type": "assistant", "message": {"role": "assistant",
                "content": "working..."}}
        f.write(json.dumps(init) + "\\n")
        f.flush()
    time.sleep(3.0)
    with open(jsonl_path, "a") as f:
        record = {"type": "assistant", "uuid": "parent-channel-b-no-stdout",
                  "session_id": "session", "message": {"id": "message-channel-b-no-stdout",
                  "role": "assistant", "content": [{"type": "text",
                  "text": "%%ORDER_UP%%"}]}}
        f.write(json.dumps(record) + "\\n")
        f.flush()
    time.sleep(3600)
""")

# Script that:
#   (1) writes %%ORDER_UP%% to a JSONL session file (Channel B fires)
#   (2) writes type=result with EMPTY result field to stdout (Channel A must NOT confirm this)
#   (3) hangs until killed
# This simulates the drain-race false negative: CLI flushes the result record envelope
# before populating its content.
# Pass session_dir as sys.argv[1].
CHANNEL_B_THEN_A_EMPTY_RESULT_SCRIPT = textwrap.dedent("""\
    import sys, time, json, os
    session_dir = sys.argv[1]
    os.makedirs(session_dir, exist_ok=True)
    sys.stdout.write(json.dumps({"type": "system", "session_id": "session"}) + "\\n")
    sys.stdout.flush()
    # Small delay to ensure file ctime > spawn_time recorded in run_managed_async
    time.sleep(0.1)
    jsonl_path = os.path.join(session_dir, "session.jsonl")
    with open(jsonl_path, "w") as f:
        init = {"type": "assistant", "message": {"role": "assistant",
                "content": "working..."}}
        f.write(json.dumps(init) + "\\n")
        f.flush()
    # Delay must exceed session_id_timeout + Phase 1 poll so Phase 2 initializes
    # scan_pos from discovery boundary before the marker arrives.
    # 3s margin handles xdist -n 4 event-loop saturation on WSL2 where coroutine
    # scheduling jitter can delay Phase 1 discovery by >1s.
    time.sleep(3.0)
    with open(jsonl_path, "a") as f:
        record = {"type": "assistant", "uuid": "parent-channel-b-empty",
                  "session_id": "session", "message": {"id": "message-channel-b-empty",
                  "role": "assistant", "content": [{"type": "text",
                  "text": "%%ORDER_UP%%"}]}}
        f.write(json.dumps(record) + "\\n")
        f.flush()
    # Short delay then write an empty-result type=result record
    time.sleep(0.15)
    result = {"type": "result", "subtype": "success", "is_error": False,
              "result": "", "session_id": "s1"}
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\\n")
    sys.stdout.flush()
    time.sleep(3600)
""")

# Script that writes %%ORDER_UP%% to session JSONL then immediately exits rc=0
# with an empty type=result on stdout. Used with _phase1_poll=1.0 so the process
# exits before the first Phase 1 poll, exercising the post-exit drain window.
# Pass session_dir as sys.argv[1].
PROCESS_EXIT_THEN_CHANNEL_B_FIRES_SCRIPT = textwrap.dedent("""\
    import sys, json, os, time
    session_dir = sys.argv[1]
    os.makedirs(session_dir, exist_ok=True)
    sys.stdout.write(json.dumps({"type": "system", "session_id": "session"}) + "\\n")
    sys.stdout.flush()
    # Small delay ensures file ctime > spawn_time recorded in run_managed_async
    time.sleep(0.1)
    jsonl_path = os.path.join(session_dir, "session.jsonl")
    with open(jsonl_path, "w") as f:
        init = {"type": "assistant", "message": {"role": "assistant",
                "content": "working..."}}
        f.write(json.dumps(init) + "\\n")
        f.flush()
    # Delay must exceed session_id_timeout + Phase 1 poll so Phase 2 initializes
    # scan_pos from discovery boundary before the marker arrives.
    time.sleep(2.0)
    with open(jsonl_path, "a") as f:
        record = {"type": "assistant", "uuid": "parent-process-exit",
                  "session_id": "test-stop-delay",
                  "message": {"id": "message-process-exit", "role": "assistant",
                  "content": [{"type": "text", "text": "%%ORDER_UP%%"}]}}
        f.write(json.dumps(record) + "\\n")
        f.flush()
    payload = {"type": "result", "subtype": "success", "is_error": False,
               "result": "", "session_id": "test-drain"}
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\\n")
    sys.stdout.flush()
    sys.exit(0)
""")


class TestChannelBDrainWait:
    """Channel B (session monitor) winning before Channel A triggers bounded drain wait."""

    @pytest.mark.timeout(360)
    @pytest.mark.anyio
    async def test_channel_b_wins_then_channel_a_confirms_within_drain(self, tmp_path):
        """Channel B fires first; drain wait allows Channel A to confirm stdout data.

        Sequence (fast poll params):
          t=0.00s  subprocess starts
          t=0.10s  script creates session JSONL with initial content
          t~0.11s  Phase 1 poll discovers file, Phase 2 initializes scan_pos
          t=3.10s  script writes %%ORDER_UP%% to session JSONL (Channel B target)
          t~3.15s  Phase 2 detects marker → Channel B fires → drain starts
          t=3.25s  script writes type=result to stdout (0.15s after JSONL write)
          t~3.30s  heartbeat fires → Channel A confirms → drain completes
          t~3.30s  process killed with confirmed stdout

        The 3.0s gap between file creation and marker write ensures Phase 1 discovers
        the JSONL file and Phase 2 initializes scan_pos BEFORE the marker arrives —
        preventing a race where Phase 2 sets scan_pos past the marker under xdist -n 4
        event-loop saturation on WSL2.

        timeout=300s: guards against the outer wall-clock expiring under xdist -n 4 load.
        _phase1_timeout=400: must exceed outer timeout (300s) so that Phase 1 never fires
        STALE before the outer wall-clock guard cancels — prevents spurious TIMED_OUT when
        subprocess startup is slow under WSL2 + xdist load.
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "channel_b_then_a.py"
        script.write_text(CHANNEL_B_THEN_A_CONFIRM_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir), "0.15"],
            cwd=tmp_path,
            timeout=300,
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            stream_parser_factory=ClaudeCodeBackend().stream_parser_factory("%%ORDER_UP%%"),
            parent_candidate_normalizer=ClaudeCodeBackend().parent_candidate_normalizer(
                "%%ORDER_UP%%"
            ),
            marker_scope_session_id="marker-scope-session",
            completion_drain_timeout=10.0,
            _phase1_timeout=400,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
            _heartbeat_poll=5.0,
            _session_id_timeout=0.01,
        )

        assert result.termination == TerminationReason.COMPLETED
        assert result.lifecycle_decision is LifecycleDecision.ELIGIBLE
        assert result.eligible_source is CompletionCandidateSource.CHANNEL_A
        assert result.channel_confirmation is ChannelConfirmation.CHANNEL_A
        assert result.session_id == "stdout-session"
        assert result.channel_b_session_id == "channel-b-log-session"
        assert tuple(sighting.source for sighting in result.sightings) == (
            CompletionCandidateSource.CHANNEL_A,
            CompletionCandidateSource.CHANNEL_B,
        )
        assert tuple(sighting.backend_session_id for sighting in result.sightings) == (
            "channel-a-candidate-session",
            "channel-b-candidate-session",
        )
        assert "result-envelope-session" in result.stdout
        assert (
            len(
                {
                    "marker-scope-session",
                    result.session_id,
                    result.channel_b_session_id,
                    *(sighting.backend_session_id for sighting in result.sightings),
                    "result-envelope-session",
                }
            )
            == 6
        )
        # Drain wait confirmed Channel A fired: stdout is non-empty
        assert result.stdout.strip()

    @pytest.mark.timeout(360)
    @pytest.mark.anyio
    async def test_channel_b_wins_drain_timeout_still_kills(self, tmp_path):
        """Channel B fires; Channel A never fires; drain times out and process is killed.

        Sequence (fast poll params):
          t=0.10s  script creates session JSONL with initial content
          t=3.10s  script writes %%ORDER_UP%% to session JSONL
          t~3.15s  Channel B fires → drain wait starts with 0.5s timeout
          t~3.65s  drain times out (script never wrote to stdout)
          t~3.65s  process killed with empty stdout

        The 3.0s gap between file creation and marker write ensures Phase 1 discovers
        the JSONL file before the marker arrives, preventing a race where Phase 2
        initializes scan_pos past the marker under xdist -n 4 event loop saturation
        (2.0s proved insufficient under WSL2 + xdist load; 3.0s matches the passing
        adjudication sibling).

        timeout=300s: guards against the outer wall-clock expiring under xdist -n 4 load.
        _phase1_timeout=400: must exceed outer timeout (300s) so that Phase 1 never fires
        first with STALE when subprocess startup is slow under WSL2 + xdist load; the
        outer 300s guard cancels all tasks before Phase 1 can timeout independently.
        natural_exit_grace_seconds=0.1: script never exits naturally (time.sleep(3600)),
        so shorten grace window to reduce total test time and avoid asyncio-waitpid
        thread contention under CI load (default 3.0s grace + 3.0s kill = 6s total).
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "channel_b_no_stdout.py"
        script.write_text(CHANNEL_B_NO_STDOUT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=300,
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            stream_parser_factory=ClaudeCodeBackend().stream_parser_factory("%%ORDER_UP%%"),
            parent_candidate_normalizer=ClaudeCodeBackend().parent_candidate_normalizer(
                "%%ORDER_UP%%"
            ),
            completion_drain_timeout=0.5,
            natural_exit_grace_seconds=0.1,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
            _session_id_timeout=0.01,
            _phase1_timeout=400,
        )

        assert result.termination == TerminationReason.COMPLETED
        assert result.lifecycle_decision is LifecycleDecision.ELIGIBLE
        assert result.eligible_source is CompletionCandidateSource.CHANNEL_B
        assert result.channel_confirmation is ChannelConfirmation.CHANNEL_B
        assert tuple(sighting.source for sighting in result.sightings) == (
            CompletionCandidateSource.CHANNEL_B,
        )
        # Drain timed out: CLI hung and never flushed its result record
        assert not result.stdout.strip()

    @pytest.mark.timeout(90)
    @pytest.mark.anyio
    async def test_channel_a_wins_unchanged_behavior(self, tmp_path):
        """Channel A (heartbeat) wins before any session monitor: no drain wait needed.

        Sequence:
          t=0     script writes type=result to stdout immediately
          t~0.5s  heartbeat fires, Channel A confirmed → kill immediately
          No drain wait: heartbeat_task is in done set
        """
        script = tmp_path / "result_hang.py"
        script.write_text(WRITE_RESULT_THEN_HANG_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=60,
            # No session_log_dir: Channel B cannot fire
            _heartbeat_poll=0.05,
        )

        assert result.termination == TerminationReason.COMPLETED
        assert result.lifecycle_decision is LifecycleDecision.ELIGIBLE
        assert result.eligible_source is CompletionCandidateSource.CHANNEL_A
        assert result.lifecycle_candidate is None
        assert result.sightings == ()
        assert result.stdout.strip()  # Channel A confirmed: stdout is non-empty

    @pytest.mark.timeout(360)
    @pytest.mark.anyio
    async def test_data_confirmed_false_set_on_drain_timeout(self, tmp_path):
        """Channel B wins the race; drain timeout expires without Channel A confirming.

        Verifies that SubprocessResult.data_confirmed is False when the bounded
        drain wait times out — i.e. Channel A never confirmed stdout data.
        timeout=300s: guards against the outer wall-clock expiring under xdist -n 4 load.
        _phase1_timeout=400: must exceed outer timeout (300s) so that Phase 1 never fires
        STALE before the outer wall-clock guard cancels — prevents spurious TIMED_OUT when
        subprocess startup is slow under WSL2 + xdist load.
        natural_exit_grace_seconds=0.1: script never exits naturally (time.sleep(3600)),
        so shorten grace window to reduce total test time and avoid asyncio-waitpid
        thread contention under CI load (default 3.0s grace + 3.0s kill = 6s total).
        _session_id_timeout=0.01: script never writes stdout, so session ID timeout
        should expire immediately to let Phase 1 start before the marker is written.
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "channel_b_no_stdout.py"
        script.write_text(CHANNEL_B_NO_STDOUT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=300,
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            stream_parser_factory=ClaudeCodeBackend().stream_parser_factory("%%ORDER_UP%%"),
            parent_candidate_normalizer=ClaudeCodeBackend().parent_candidate_normalizer(
                "%%ORDER_UP%%"
            ),
            completion_drain_timeout=2.0,
            natural_exit_grace_seconds=0.1,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
            _session_id_timeout=0.01,
            _phase1_timeout=400,
        )

        assert result.termination == TerminationReason.COMPLETED
        assert result.channel_confirmation == ChannelConfirmation.CHANNEL_B
        assert result.eligible_source is CompletionCandidateSource.CHANNEL_B

    @pytest.mark.timeout(90)
    @pytest.mark.anyio
    async def test_data_confirmed_true_when_channel_a_wins(self, tmp_path):
        """Channel A (heartbeat) wins; data_confirmed must be True.

        When the heartbeat fires before Channel B (or with no Channel B),
        data availability is guaranteed and data_confirmed must remain True.
        """
        script = tmp_path / "result_hang.py"
        script.write_text(WRITE_RESULT_THEN_HANG_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=60,
            # No session_log_dir: Channel B cannot fire
            _heartbeat_poll=0.05,
        )

        assert result.termination == TerminationReason.COMPLETED
        assert result.channel_confirmation == ChannelConfirmation.CHANNEL_A
        assert result.eligible_source is CompletionCandidateSource.CHANNEL_A
        assert result.lifecycle_candidate is None
        assert result.sightings == ()

    @pytest.mark.timeout(360)
    @pytest.mark.anyio
    async def test_channel_b_then_a_empty_result_data_confirmed_is_false(self, tmp_path):
        """Channel B fires (%%ORDER_UP%% in JSONL).

        Within the drain window, Claude CLI writes a type=result record with
        result="". Channel A must NOT confirm on this — data_confirmed must
        remain False so the provenance bypass can fire.

        Sequence (fast poll params):
          t=0.00s  subprocess starts, writes type=system to stdout
          t=0.10s  script creates session JSONL with initial content
          t~0.11s  Phase 1 poll discovers file, Phase 2 initializes scan_pos
          t=3.10s  script writes %%ORDER_UP%% to session JSONL (Channel B target)
          t~3.15s  Phase 2 detects marker → Channel B fires → drain starts
          t=3.25s  script writes type=result with result="" to stdout
          t~3.30s  heartbeat sees empty result, does NOT confirm → drain continues
          t~5.15s  drain timeout expires (2.0s), Channel B wins

        The 3.0s gap between file creation and marker write ensures Phase 1 discovers
        the JSONL file and Phase 2 initializes scan_pos BEFORE the marker arrives —
        preventing a race where Phase 2 sets scan_pos past the marker under xdist -n 4
        event-loop saturation on WSL2.

        timeout=300: guards against the outer wall-clock expiring under xdist -n 4 load.
        _phase1_timeout=400: must exceed outer timeout (300s) so Phase 1 never fires
        STALE before the outer wall-clock guard cancels — prevents spurious TIMED_OUT
        when subprocess startup is slow under WSL2 + xdist load.
        natural_exit_grace_seconds=0.1: script never exits naturally (time.sleep(3600)),
        so shorten the grace window to reduce total test time under load.
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "channel_b_empty.py"
        script.write_text(CHANNEL_B_THEN_A_EMPTY_RESULT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=300,
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            stream_parser_factory=ClaudeCodeBackend().stream_parser_factory("%%ORDER_UP%%"),
            parent_candidate_normalizer=ClaudeCodeBackend().parent_candidate_normalizer(
                "%%ORDER_UP%%"
            ),
            completion_drain_timeout=2.0,
            natural_exit_grace_seconds=0.1,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
            _heartbeat_poll=0.05,
            _session_id_timeout=0.01,
            _phase1_timeout=400,
        )
        assert result.termination == TerminationReason.COMPLETED
        assert (
            result.channel_confirmation == ChannelConfirmation.CHANNEL_B
        )  # FAILS before fix: True


@pytest.mark.timeout(360)
class TestChannelBFullPipelineAdjudication:
    """Full end-to-end adjudication for Channel B drain-race scenarios."""

    @pytest.mark.anyio
    async def test_channel_b_then_a_empty_result_produces_success(self, tmp_path):
        """Full end-to-end: Channel B fires, CLI writes type=result with result="".

        With strengthened Channel A, data_confirmed=False, provenance bypass fires.
        Result: success=True, needs_retry=False (no wasteful retry of completed session).

        Timing notes:
        - completion_drain_timeout=0.5s: the heartbeat has already seen the empty result
          and failed to confirm by the time Channel B fires (~3s after task group start),
          so 0.5s of additional drain time is more than sufficient semantically.
        - timeout=120s: subprocess wall-clock guard. Must be less than pytest.mark.timeout
          (180s on the class) so run_managed_async completes before pytest kills the test.
          _phase1_timeout=250 must exceed outer timeout so Phase 1 never fires STALE before
          the outer guard when subprocess startup is slow under WSL2 + xdist load.
        - _session_id_timeout=0.5s: 0.5s is generous for session ID extraction (from stdout)
          while ensuring Phase 1 starts before the marker arrives.
        """
        from autoskillit.execution.headless import _build_skill_result

        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "channel_b_empty.py"
        script.write_text(CHANNEL_B_THEN_A_EMPTY_RESULT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=300,
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            stream_parser_factory=ClaudeCodeBackend().stream_parser_factory("%%ORDER_UP%%"),
            parent_candidate_normalizer=ClaudeCodeBackend().parent_candidate_normalizer(
                "%%ORDER_UP%%"
            ),
            completion_drain_timeout=2.0,
            natural_exit_grace_seconds=0.1,
            _phase1_timeout=400,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
            _heartbeat_poll=0.05,
            _session_id_timeout=0.5,
        )
        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="test-command",
            audit=None,
            backend=ClaudeCodeBackend(),
        )
        assert skill_result.success is True  # FAILS before fix: False
        assert skill_result.needs_retry is False  # FAILS before fix: True


class TestChannelBDrainRacePipelineAdjudication:
    """Integration: COMPLETED (Channel B drain timeout) flows through _build_skill_result.

    Session monitor fires, drain expires, process is killed with empty stdout.
    _build_skill_result must apply the Channel B provenance bypass
    (data_confirmed=False → success=True without calling _compute_success).
    """

    @pytest.mark.timeout(360)
    @pytest.mark.anyio
    async def test_channel_b_drain_timeout_produces_success_skill_result(self, tmp_path):
        """COMPLETED + data_confirmed=False + empty stdout → success=True, needs_retry=False.

        Channel B provenance bypass: when session monitor wins and drain expires,
        _build_skill_result returns success=True immediately, bypassing _compute_success.

        Timing notes:
        - timeout=300 / _phase1_timeout=400: Phase 1 timeout exceeds outer timeout so
          Phase 1 never fires STALE before the outer guard under WSL2 + xdist load.
        - natural_exit_grace_seconds=0.1: script never exits naturally (time.sleep(3600)),
          so shorten grace window to avoid asyncio-waitpid thread contention under CI load.
        """
        from autoskillit.execution.headless import _build_skill_result

        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "channel_b_no_stdout.py"
        script.write_text(CHANNEL_B_NO_STDOUT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=300,
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            stream_parser_factory=ClaudeCodeBackend().stream_parser_factory("%%ORDER_UP%%"),
            parent_candidate_normalizer=ClaudeCodeBackend().parent_candidate_normalizer(
                "%%ORDER_UP%%"
            ),
            completion_drain_timeout=0.5,
            natural_exit_grace_seconds=0.1,
            _phase1_timeout=400,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
            _session_id_timeout=0.01,
        )

        assert result.termination == TerminationReason.COMPLETED
        assert result.channel_confirmation == ChannelConfirmation.CHANNEL_B

        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="resolve-failures",
            audit=None,
            backend=ClaudeCodeBackend(),
        )

        assert skill_result.success is True
        assert skill_result.needs_retry is False


class TestNaturalExitWithChannelConfirmation:
    """NATURAL_EXIT + channel signals flow correctly through _build_skill_result.

    Test 1C: Validates the downstream adjudication path for the combination
    produced by the signal-accumulation fix when wait_task and session_monitor
    both complete in the same event loop tick.
    """

    def test_natural_exit_channel_b_empty_stdout_is_success(self):
        """NATURAL_EXIT + CHANNEL_B + empty stdout → success=True, no retry.

        _compute_success: CHANNEL_B provenance bypass fires → True.
        _compute_retry: NATURAL_EXIT + CHANNEL_B channel guard fires → (False, NONE).
        """
        from autoskillit.execution.headless import _build_skill_result

        result = SubprocessResult(
            returncode=0,
            stdout="",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
        )
        skill_result = _build_skill_result(
            result,
            completion_marker="",
            skill_command="test",
            audit=None,
            backend=ClaudeCodeBackend(),
        )
        assert skill_result.success is True
        assert skill_result.needs_retry is False


class TestPostExitDrainWindow:
    """Symmetric drain window: process exits first, Channel B gets a bounded window to deposit."""

    @pytest.mark.timeout(180)
    @pytest.mark.anyio
    async def test_drain_window_allows_channel_b_to_deposit(self, tmp_path):
        """Process exits after writing marker; drain window lets Channel B detect it.

        The script writes initial content, waits for Phase 1 to discover the file,
        then writes the marker and exits. The drain window gives Channel B time to
        complete its Phase 2 poll and detect the marker, producing CHANNEL_B confirmation.

        timeout=120 / _phase1_timeout=250: _phase1_timeout must exceed the outer timeout
        so Phase 1 never fires STALE before the outer guard under WSL2 + xdist load.
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "process_exit_then_channel_b.py"
        script.write_text(PROCESS_EXIT_THEN_CHANNEL_B_FIRES_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=120,
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            stream_parser_factory=ClaudeCodeBackend().stream_parser_factory("%%ORDER_UP%%"),
            parent_candidate_normalizer=ClaudeCodeBackend().parent_candidate_normalizer(
                "%%ORDER_UP%%"
            ),
            completion_drain_timeout=60.0,
            _phase1_timeout=250,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
            _heartbeat_poll=0.05,
            _session_id_timeout=0.01,
        )

        assert result.channel_confirmation == ChannelConfirmation.CHANNEL_B

    @pytest.mark.timeout(30)
    @pytest.mark.anyio
    async def test_drain_window_times_out_when_no_session_jsonl(self, tmp_path):
        """Process exits with no session JSONL; drain window times out, UNMONITORED preserved.

        The drain window expires after completion_drain_timeout seconds without
        Channel B depositing. Existing behavior (UNMONITORED) is unchanged.
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        # Script that writes empty result to stdout and exits — no JSONL written
        script = tmp_path / "empty_exit.py"
        script.write_text(
            textwrap.dedent("""\
            import sys, json
            payload = {"type": "result", "subtype": "success", "is_error": False,
                       "result": "", "session_id": "test-stop-delay"}
            sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\\n")
            sys.stdout.flush()
            sys.exit(0)
        """)
        )

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=10,
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            stream_parser_factory=ClaudeCodeBackend().stream_parser_factory("%%ORDER_UP%%"),
            parent_candidate_normalizer=ClaudeCodeBackend().parent_candidate_normalizer(
                "%%ORDER_UP%%"
            ),
            completion_drain_timeout=0.2,
            _phase1_poll=0.05,
            _phase2_poll=0.05,
            _heartbeat_poll=0.05,
            _session_id_timeout=0.01,
        )

        assert result.channel_confirmation == ChannelConfirmation.UNMONITORED


# Script that:
#   (1) writes static %%ORDER_UP%% to JSONL (simulating sub-skill emission)
#   (2) later writes %%ORDER_UP::{unique}%% to JSONL (the parent's real marker)
#   (3) writes type=result to stdout within the drain window
#   (4) hangs until killed
# Pass session_dir as sys.argv[1], unique marker as sys.argv[2].
CHANNEL_B_SUB_SKILL_COLLISION_SCRIPT = textwrap.dedent("""\
    import sys, time, json, os
    session_dir = sys.argv[1]
    unique_marker = sys.argv[2]
    os.makedirs(session_dir, exist_ok=True)
    sys.stdout.write(json.dumps({"type": "system", "session_id": "session"}) + "\\n")
    sys.stdout.flush()
    time.sleep(0.1)
    jsonl_path = os.path.join(session_dir, "session.jsonl")
    with open(jsonl_path, "w") as f:
        init = {"type": "assistant", "message": {"role": "assistant",
                "content": "working..."}}
        f.write(json.dumps(init) + "\\n")
        f.flush()
    # Delay must exceed session_id_timeout + Phase 1 poll so Phase 2 initializes
    # scan_pos from discovery boundary before sub-skill marker arrives.
    time.sleep(1.0)
    with open(jsonl_path, "a") as f:
        # Sub-skill emits static marker — should NOT trigger completion
        sub_skill_record = {"type": "assistant", "message": {"role": "assistant",
                  "content": "%%ORDER_UP%%"}}
        f.write(json.dumps(sub_skill_record) + "\\n")
        f.flush()
    time.sleep(0.3)
    with open(jsonl_path, "a") as f:
        # Parent emits its unique marker — SHOULD trigger completion
        parent_record = {"type": "assistant", "uuid": "parent-unique-marker",
                  "session_id": "session", "message": {"id": "message-unique-marker",
                  "role": "assistant", "content": [{"type": "text",
                  "text": unique_marker}]}}
        f.write(json.dumps(parent_record) + "\\n")
        f.flush()
    time.sleep(0.15)
    result = {"type": "result", "subtype": "success", "is_error": False,
              "result": "done", "session_id": "s1"}
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\\n")
    sys.stdout.flush()
    time.sleep(3600)
""")


class TestChannelBSubSkillCollision:
    """Channel B ignores static markers when monitoring for a unique marker."""

    @pytest.mark.timeout(400)
    @pytest.mark.anyio
    async def test_channel_b_ignores_sub_skill_marker(self, tmp_path):
        """Channel B must not trigger on a sub-skill's static %%ORDER_UP%% marker.

        timeout=300s / _phase1_timeout=600: _phase1_timeout must exceed the outer timeout so
        Phase 1 never fires STALE before the outer guard under WSL2 + xdist load.
        _session_id_timeout=0.5: script writes system record to stdout immediately;
        0.5s is generous for session ID extraction while ensuring Phase 1 starts
        before the JSONL markers are written.

        Timeouts are set at 3x the isolation baseline (~14s) to absorb event-loop
        slowdowns under 4-worker xdist load on WSL2, where anyio.sleep() can run
        10-20x longer than nominal due to scheduler saturation.
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        unique_marker = "%%ORDER_UP::test1234%%"
        script = tmp_path / "sub_skill_collision.py"
        script.write_text(CHANNEL_B_SUB_SKILL_COLLISION_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir), unique_marker],
            cwd=tmp_path,
            timeout=300,
            session_log_dir=session_dir,
            completion_marker=unique_marker,
            stream_parser_factory=ClaudeCodeBackend().stream_parser_factory(unique_marker),
            parent_candidate_normalizer=ClaudeCodeBackend().parent_candidate_normalizer(
                unique_marker
            ),
            completion_drain_timeout=5.0,
            _phase1_timeout=600,
            _phase1_poll=0.05,
            _phase2_poll=0.05,
            _heartbeat_poll=0.05,
            _session_id_timeout=0.5,
        )

        assert result.termination == TerminationReason.COMPLETED
        assert result.channel_confirmation == ChannelConfirmation.CHANNEL_B


def _jsonl(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


@pytest.mark.timeout(90)
@pytest.mark.anyio
async def test_lifecycle_channel_b_persists_with_exact_offsets_and_distinct_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three actor-correlated B replies preserve tailing, generations, and offsets."""
    import autoskillit.execution.process._lifecycle_actor as lifecycle_actor
    from autoskillit.execution.process._lifecycle_actor import (
        ChannelBProposal,
        LifecycleActorReply,
        LifecycleReplyDisposition,
    )
    from autoskillit.execution.process._process_monitor import _SessionLogScanComplete

    marker = "%%PERSISTENT_CHANNEL_B%%"
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    probe_seen = tmp_path / "probe-seen"
    partial_ready = tmp_path / "partial-ready"
    release_partial = tmp_path / "release-partial"
    old_reply_seen = tmp_path / "old-reply-seen"
    middle_reply_seen = tmp_path / "middle-reply-seen"
    release_final = tmp_path / "release-final"
    partial_scan_seen = anyio.Event()
    replies: dict[str, LifecycleActorReply] = {}

    real_tail = lifecycle_actor._tail_session_log_events

    async def observed_tail(*args: Any, **kwargs: Any):
        async for event in real_tail(*args, **kwargs):
            if isinstance(event, _SessionLogScanComplete) and event.incomplete_carry:
                partial_scan_seen.set()
            yield event

    monkeypatch.setattr(lifecycle_actor, "_tail_session_log_events", observed_tail)

    real_submit = lifecycle_actor.submit_actor_request_nowait

    class RecordingReplySend:
        def __init__(self, delegate: Any, candidate_id: str) -> None:
            self._delegate = delegate
            self._candidate_id = candidate_id

        def send_nowait(self, reply: LifecycleActorReply) -> None:
            self._delegate.send_nowait(reply)
            replies[self._candidate_id] = reply
            if self._candidate_id == "parent-old":
                old_reply_seen.write_text("replied", encoding="utf-8")
            if self._candidate_id == "parent-middle":
                middle_reply_seen.write_text("replied", encoding="utf-8")

        def close(self) -> None:
            self._delegate.close()

    def observed_submit(
        endpoint: Any,
        semaphore: Any,
        proposal: Any,
        reply_send: Any,
        deadline: float,
    ) -> Any:
        if isinstance(proposal, ChannelBProposal) and proposal.candidate_sighting is not None:
            reply_send = RecordingReplySend(
                reply_send,
                proposal.candidate_sighting.native_uuid,
            )
        return real_submit(endpoint, semaphore, proposal, reply_send, deadline)

    monkeypatch.setattr(lifecycle_actor, "submit_actor_request_nowait", observed_submit)

    script = tmp_path / "persistent_channel_b.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import json
            import signal
            import sys
            import time
            from pathlib import Path

            session_dir = Path(sys.argv[1])
            probe_seen = Path(sys.argv[2])
            partial_ready = Path(sys.argv[3])
            release_partial = Path(sys.argv[4])
            old_reply_seen = Path(sys.argv[5])
            middle_reply_seen = Path(sys.argv[6])
            release_final = Path(sys.argv[7])
            marker = {marker!r}
            log = session_dir / "channel-b-log-session.jsonl"
            log.write_bytes(b"")

            def emit_stdout(record):
                print(json.dumps(record, separators=(",", ":")), flush=True)

            def append_log(record):
                raw = (json.dumps(record, ensure_ascii=False,
                                  separators=(",", ":")) + "\\n").encode()
                with log.open("ab", buffering=0) as stream:
                    stream.write(raw)
                return raw

            def wait_for(path):
                deadline = time.monotonic() + 10
                while not path.exists():
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"timed out waiting for {{path}}")
                    time.sleep(0.01)

            emit_stdout({{
                "type": "system", "subtype": "init",
                "session_id": "stdout-transport-session"
            }})
            emit_stdout({{
                "type": "system", "subtype": "task_started",
                "agent_id": "agent-1", "task_id": "task-1",
                "tool_use_id": "toolu-1", "uuid": "start-1"
            }})

            time.sleep(0.5)
            append_log({{
                "type": "assistant", "uuid": "probe",
                "message": {{"content": "working"}}
            }})
            wait_for(probe_seen)

            split_record = {{
                "type": "assistant", "uuid": "split-non-marker",
                "session_id": "non-marker-candidate-session",
                "message": {{"content": "still working"}}
            }}
            split_raw = (json.dumps(split_record, separators=(",", ":")) + "\\n").encode()
            split_at = len(split_raw) // 2
            with log.open("ab", buffering=0) as stream:
                stream.write(split_raw[:split_at])
            partial_ready.write_text("ready")
            wait_for(release_partial)
            with log.open("ab", buffering=0) as stream:
                stream.write(split_raw[split_at:])

            append_log({{
                "type": "user", "uuid": "non-marker-user",
                "message": {{"content": "diagnostic"}}
            }})
            append_log({{
                "type": "assistant", "uuid": "parent-old",
                "session_id": "old-b-candidate-session",
                "message": {{"id": "message-old", "content": [
                    {{"type": "text", "text": marker}}
                ]}}
            }})
            wait_for(old_reply_seen)

            emit_stdout({{
                "type": "assistant", "uuid": "parent-middle",
                "session_id": "middle-a-candidate-session",
                "message": {{"id": "message-middle-a", "content": [
                    {{"type": "text", "text": marker}}
                ]}}
            }})
            append_log({{
                "type": "assistant", "uuid": "parent-middle",
                "session_id": "middle-b-candidate-session",
                "message": {{"id": "message-middle-b", "content": [
                    {{"type": "text", "text": marker}}
                ]}}
            }})
            wait_for(middle_reply_seen)
            wait_for(release_final)

            emit_stdout({{
                "type": "system", "subtype": "task_notification",
                "status": "completed", "agent_id": "agent-1",
                "task_id": "task-1", "tool_use_id": "toolu-1",
                "uuid": "notification-1"
            }})
            emit_stdout({{
                "type": "user", "uuid": "delivery-1",
                "message": {{"id": "delivery-message-1", "content": [{{
                    "type": "tool_result", "tool_use_id": "toolu-1",
                    "content": {{"status": "completed", "agentId": "agent-1"}}
                }}]}}
            }})
            emit_stdout({{
                "type": "result", "subtype": "success", "is_error": False,
                "session_id": "result-envelope-session", "result": marker
            }})
            append_log({{
                "type": "assistant", "uuid": "parent-final",
                "session_id": "final-b-candidate-session",
                "message": {{"id": "message-final-b", "content": [
                    {{"type": "text", "text": marker}}
                ]}}
            }})
            signal.pause()
            """
        ),
        encoding="utf-8",
    )

    backend_normalizer = ClaudeCodeBackend().parent_candidate_normalizer(marker)
    calls: list[tuple[dict[str, Any], int]] = []

    def recording_normalizer(record: dict[str, Any], offset: int) -> Any:
        calls.append((record, offset))
        if record.get("uuid") == "probe":
            probe_seen.write_text("seen", encoding="utf-8")
        return backend_normalizer(record, offset)

    result_box: list[SubprocessResult] = []

    async def run_process() -> None:
        result_box.append(
            await run_managed_async(
                [
                    sys.executable,
                    str(script),
                    str(session_dir),
                    str(probe_seen),
                    str(partial_ready),
                    str(release_partial),
                    str(old_reply_seen),
                    str(middle_reply_seen),
                    str(release_final),
                ],
                cwd=tmp_path,
                timeout=60,
                session_log_dir=session_dir,
                completion_marker=marker,
                stream_parser_factory=ClaudeCodeBackend().stream_parser_factory(marker),
                parent_candidate_normalizer=recording_normalizer,
                marker_scope_session_id="marker-scope-session",
                completion_drain_timeout=2.0,
                natural_exit_grace_seconds=0.05,
                cleanup_budget_seconds=3.0,
                _phase1_timeout=120,
                _phase1_poll=0.005,
                _phase2_poll=0.005,
                _heartbeat_poll=0.005,
                _session_id_timeout=1.0,
            )
        )

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_process)
        with anyio.fail_after(10):
            while not partial_ready.exists():
                await anyio.sleep(0.01)
        with anyio.fail_after(10):
            await partial_scan_seen.wait()
        assert [record["uuid"] for record, _offset in calls] == ["probe"]
        release_partial.write_text("release", encoding="utf-8")
        with anyio.fail_after(10):
            while not middle_reply_seen.exists():
                await anyio.sleep(0.01)
        middle_snapshot = replies["parent-middle"].snapshot
        assert middle_snapshot is not None
        assert dict(middle_snapshot.candidate_states) == {
            "parent-old": CompletionCandidateState.SUPERSEDED,
            "parent-middle": CompletionCandidateState.DEFERRED,
        }
        release_final.write_text("release", encoding="utf-8")

    result = result_box[0]
    records = [
        {"type": "assistant", "uuid": "probe", "message": {"content": "working"}},
        {
            "type": "assistant",
            "uuid": "split-non-marker",
            "session_id": "non-marker-candidate-session",
            "message": {"content": "still working"},
        },
        {"type": "user", "uuid": "non-marker-user", "message": {"content": "diagnostic"}},
        {
            "type": "assistant",
            "uuid": "parent-old",
            "session_id": "old-b-candidate-session",
            "message": {
                "id": "message-old",
                "content": [{"type": "text", "text": marker}],
            },
        },
        {
            "type": "assistant",
            "uuid": "parent-middle",
            "session_id": "middle-b-candidate-session",
            "message": {
                "id": "message-middle-b",
                "content": [{"type": "text", "text": marker}],
            },
        },
        {
            "type": "assistant",
            "uuid": "parent-final",
            "session_id": "final-b-candidate-session",
            "message": {
                "id": "message-final-b",
                "content": [{"type": "text", "text": marker}],
            },
        },
    ]
    expected_offsets: list[int] = []
    cursor = 0
    for record in records:
        cursor += len(_jsonl(record))
        expected_offsets.append(cursor)

    assert [record for record, _offset in calls] == records
    assert [offset for _record, offset in calls] == expected_offsets
    assert result.termination is TerminationReason.COMPLETED, result.lifecycle_snapshot
    assert result.lifecycle_decision is LifecycleDecision.ELIGIBLE
    assert result.eligible_source is CompletionCandidateSource.CHANNEL_B
    assert result.channel_confirmation is ChannelConfirmation.CHANNEL_B
    assert result.lifecycle_candidate is not None
    assert result.lifecycle_candidate.candidate_id == "parent-final"
    assert result.session_id == "stdout-transport-session"
    assert result.channel_b_session_id == "channel-b-log-session"
    assert result.lifecycle_snapshot is not None
    assert dict(result.lifecycle_snapshot.candidate_states) == {
        "parent-old": CompletionCandidateState.SUPERSEDED,
        "parent-middle": CompletionCandidateState.SUPERSEDED,
        "parent-final": CompletionCandidateState.ELIGIBLE,
    }
    assert {candidate_id: reply.disposition for candidate_id, reply in replies.items()} == {
        "parent-old": LifecycleReplyDisposition.DEFERRED,
        "parent-middle": LifecycleReplyDisposition.DEFERRED,
        "parent-final": LifecycleReplyDisposition.ELIGIBLE,
    }
    assert all(candidate_id in replies for candidate_id in ("parent-old", "parent-middle"))

    middle_sightings = {
        sighting.source: sighting
        for sighting in result.sightings
        if sighting.native_uuid == "parent-middle"
    }
    assert set(middle_sightings) == {
        CompletionCandidateSource.CHANNEL_A,
        CompletionCandidateSource.CHANNEL_B,
    }
    b_sighting = middle_sightings[CompletionCandidateSource.CHANNEL_B]
    a_sighting = middle_sightings[CompletionCandidateSource.CHANNEL_A]
    assert b_sighting.channel_relative_byte_offset == expected_offsets[-2]
    assert b_sighting.backend_session_id == "middle-b-candidate-session"

    stdout_cursor = 0
    expected_a_offset = None
    for raw_line in result.stdout.encode().splitlines(keepends=True):
        stdout_cursor += len(raw_line)
        parsed = json.loads(raw_line)
        if parsed.get("uuid") == "parent-middle":
            expected_a_offset = stdout_cursor
            break
    assert expected_a_offset is not None
    assert a_sighting.channel_relative_byte_offset == expected_a_offset
    assert a_sighting.backend_session_id == "middle-a-candidate-session"
    assert a_sighting.channel_relative_byte_offset != b_sighting.channel_relative_byte_offset
    assert (
        len(
            {
                "marker-scope-session",
                result.session_id,
                result.channel_b_session_id,
                a_sighting.backend_session_id,
                b_sighting.backend_session_id,
                "result-envelope-session",
            }
        )
        == 6
    )


@pytest.mark.timeout(90)
@pytest.mark.anyio
async def test_first_post_exit_scan_detects_channel_b_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The marker normalizer runs only after the real process-exit callback."""
    import autoskillit.execution.process._lifecycle_actor as lifecycle_actor
    from autoskillit.execution.process._process_monitor import _ParsedSessionLogRecord

    marker = "%%POST_EXIT_CHANNEL_B%%"
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    probe_seen = tmp_path / "probe-seen"
    process_exit_seen = anyio.Event()
    marker_exit_observations: list[bool] = []

    real_watch_process = lifecycle_actor._watch_process

    async def observed_watch_process(*args: Any, **kwargs: Any) -> None:
        await real_watch_process(*args, **kwargs)
        process_exit_seen.set()

    monkeypatch.setattr(lifecycle_actor, "_watch_process", observed_watch_process)

    real_tail = lifecycle_actor._tail_session_log_events

    async def exit_ordered_tail(*args: Any, **kwargs: Any):
        async for event in real_tail(*args, **kwargs):
            if (
                isinstance(event, _ParsedSessionLogRecord)
                and event.value.get("uuid") == "parent-after-exit"
            ):
                with anyio.fail_after(10):
                    await process_exit_seen.wait()
            yield event

    monkeypatch.setattr(lifecycle_actor, "_tail_session_log_events", exit_ordered_tail)

    script = tmp_path / "post_exit_channel_b.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import json
            import sys
            import time
            from pathlib import Path

            log = Path(sys.argv[1]) / "post-exit-log-session.jsonl"
            probe_seen = Path(sys.argv[2])
            log.write_bytes(b"")
            print(json.dumps({{
                "type": "system", "subtype": "init", "session_id": "stdout-post-exit"
            }}), flush=True)
            time.sleep(0.5)
            with log.open("ab", buffering=0) as stream:
                stream.write((json.dumps({{
                    "type": "assistant", "uuid": "probe",
                    "message": {{"content": "working"}}
                }}) + "\\n").encode())
            deadline = time.monotonic() + 10
            while not probe_seen.exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("normalizer did not observe probe")
                time.sleep(0.01)
            record = {{
                "type": "assistant", "uuid": "parent-after-exit",
                "session_id": "candidate-after-exit",
                "message": {{"id": "message-after-exit", "content": [
                    {{"type": "text", "text": {marker!r}}}
                ]}}
            }}
            with log.open("ab", buffering=0) as stream:
                stream.write((json.dumps(record, separators=(",", ":")) + "\\n").encode())
            sys.exit(0)
            """
        ),
        encoding="utf-8",
    )
    normalizer = ClaudeCodeBackend().parent_candidate_normalizer(marker)

    def observe_probe(record: dict[str, Any], offset: int) -> Any:
        if record.get("uuid") == "probe":
            probe_seen.write_text("seen", encoding="utf-8")
        if record.get("uuid") == "parent-after-exit":
            marker_exit_observations.append(process_exit_seen.is_set())
        return normalizer(record, offset)

    result = await run_managed_async(
        [sys.executable, str(script), str(session_dir), str(probe_seen)],
        cwd=tmp_path,
        timeout=60,
        session_log_dir=session_dir,
        completion_marker=marker,
        stream_parser_factory=ClaudeCodeBackend().stream_parser_factory(marker),
        parent_candidate_normalizer=observe_probe,
        completion_drain_timeout=2.0,
        _phase1_timeout=120,
        _phase1_poll=0.005,
        _phase2_poll=0.5,
        _heartbeat_poll=0.005,
        _session_id_timeout=1.0,
    )

    assert result.termination is TerminationReason.COMPLETED
    assert result.lifecycle_decision is LifecycleDecision.ELIGIBLE
    assert result.eligible_source is CompletionCandidateSource.CHANNEL_B
    assert result.channel_confirmation is ChannelConfirmation.CHANNEL_B
    assert tuple(s.native_uuid for s in result.sightings) == ("parent-after-exit",)
    assert marker_exit_observations == [True]


@pytest.mark.timeout(90)
@pytest.mark.anyio
async def test_overlapping_channel_b_and_exit_requests_are_correlated_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B and exit requests coexist with distinct IDs and receive correlated replies."""
    import autoskillit.execution.process._lifecycle_actor as lifecycle_actor
    from autoskillit.execution.process._lifecycle_actor import (
        ChannelBProposal,
        LifecycleActorReply,
        ProcessExitFact,
    )

    marker = "%%INCOMPLETE_STDOUT_CHANNEL_B%%"
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    probe_seen = tmp_path / "probe-seen"
    requests: dict[str, Any] = {}
    replies: dict[str, LifecycleActorReply] = {}
    coexistence: list[tuple[str, str, int, int]] = []
    real_submit = lifecycle_actor.submit_actor_request_nowait

    class CorrelatedReplySend:
        def __init__(self, delegate: Any, producer: str) -> None:
            self._delegate = delegate
            self._producer = producer

        def send_nowait(self, reply: LifecycleActorReply) -> None:
            self._delegate.send_nowait(reply)
            replies[self._producer] = reply

        def close(self) -> None:
            self._delegate.close()

    def tracked_submit(
        producer: str,
        endpoint: Any,
        semaphore: Any,
        proposal: Any,
        reply_send: Any,
        deadline: float,
    ) -> Any:
        request = real_submit(
            endpoint,
            semaphore,
            proposal,
            CorrelatedReplySend(reply_send, producer),
            deadline,
        )
        requests[producer] = request
        if producer == "process_exit":
            channel_b_request = requests["channel_b"]
            assert channel_b_request.lease is not None
            assert request.lease is not None
            coexistence.append(
                (
                    channel_b_request.request_id,
                    request.request_id,
                    channel_b_request.required_byte_offset,
                    request.required_byte_offset,
                )
            )
            assert channel_b_request.lease.owner == "actor"
            assert request.lease.owner == "actor"
            assert not channel_b_request.lease.released
            assert not request.lease.released
            assert "channel_b" not in replies
            assert "process_exit" not in replies
        return request

    def observed_submit(
        endpoint: Any,
        semaphore: Any,
        proposal: Any,
        reply_send: Any,
        deadline: float,
    ) -> Any:
        if isinstance(proposal, ChannelBProposal):
            producer = "channel_b"
        elif isinstance(proposal, ProcessExitFact):
            producer = "process_exit"
        else:
            return real_submit(endpoint, semaphore, proposal, reply_send, deadline)
        return tracked_submit(producer, endpoint, semaphore, proposal, reply_send, deadline)

    monkeypatch.setattr(lifecycle_actor, "submit_actor_request_nowait", observed_submit)

    script = tmp_path / "incomplete_stdout.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import json
            import os
            import sys
            import time
            from pathlib import Path

            log = Path(sys.argv[1]) / "incomplete-log-session.jsonl"
            probe_seen = Path(sys.argv[2])
            log.write_bytes(b"")
            print(json.dumps({{
                "type": "system", "subtype": "init", "session_id": "stdout-incomplete"
            }}), flush=True)
            time.sleep(0.5)
            with log.open("ab", buffering=0) as stream:
                stream.write((json.dumps({{
                    "type": "assistant", "uuid": "probe",
                    "message": {{"content": "working"}}
                }}) + "\\n").encode())
            deadline = time.monotonic() + 10
            while not probe_seen.exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("normalizer did not observe probe")
                time.sleep(0.01)
            os.write(sys.stdout.fileno(), b'{{"type":"assistant"')
            record = {{
                "type": "assistant", "uuid": "parent-incomplete",
                "session_id": "candidate-incomplete",
                "message": {{"id": "message-incomplete", "content": [
                    {{"type": "text", "text": {marker!r}}}
                ]}}
            }}
            with log.open("ab", buffering=0) as stream:
                stream.write((json.dumps(record, separators=(",", ":")) + "\\n").encode())
            sys.exit(0)
            """
        ),
        encoding="utf-8",
    )
    normalizer = ClaudeCodeBackend().parent_candidate_normalizer(marker)

    def observe_probe(record: dict[str, Any], offset: int) -> Any:
        if record.get("uuid") == "probe":
            probe_seen.write_text("seen", encoding="utf-8")
        return normalizer(record, offset)

    result = await run_managed_async(
        [sys.executable, str(script), str(session_dir), str(probe_seen)],
        cwd=tmp_path,
        timeout=60,
        session_log_dir=session_dir,
        completion_marker=marker,
        stream_parser_factory=ClaudeCodeBackend().stream_parser_factory(marker),
        parent_candidate_normalizer=observe_probe,
        completion_drain_timeout=1.0,
        _phase1_timeout=120,
        _phase1_poll=0.005,
        _phase2_poll=0.05,
        _heartbeat_poll=0.005,
        _session_id_timeout=1.0,
    )

    assert result.termination is TerminationReason.HEALTH_INSPECTOR
    assert result.lifecycle_decision is LifecycleDecision.CATCH_UP_FAILED
    assert result.eligible_source is None
    assert result.channel_confirmation is ChannelConfirmation.UNMONITORED
    assert result.lifecycle_candidate is None
    assert len(coexistence) == 1
    channel_b_id, exit_id, channel_b_watermark, exit_watermark = coexistence[0]
    assert channel_b_id != exit_id
    assert channel_b_watermark == exit_watermark
    assert set(requests) == {"channel_b", "process_exit"}
    assert set(replies) == {"channel_b", "process_exit"}
    assert replies["channel_b"].request_id == channel_b_id
    assert replies["process_exit"].request_id == exit_id
    assert replies["channel_b"].decision is LifecycleDecision.CATCH_UP_FAILED
    assert replies["process_exit"].decision is LifecycleDecision.CATCH_UP_FAILED


@pytest.mark.timeout(90)
@pytest.mark.anyio
async def test_deleted_channel_b_log_fails_final_scan_closed(tmp_path: Path) -> None:
    """A failed cooperative final B scan becomes CATCH_UP_FAILED, never natural success."""
    marker = "%%MISSING_FINAL_SCAN%%"
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    probe_seen = tmp_path / "probe-seen"
    script = tmp_path / "deleted_channel_b.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            import sys
            import time
            from pathlib import Path

            log = Path(sys.argv[1]) / "deleted-log-session.jsonl"
            probe_seen = Path(sys.argv[2])
            log.write_bytes(b"")
            print(json.dumps({
                "type": "system", "subtype": "init", "session_id": "stdout-deleted"
            }), flush=True)
            time.sleep(0.5)
            with log.open("ab", buffering=0) as stream:
                stream.write((json.dumps({
                    "type": "assistant", "uuid": "probe",
                    "message": {"content": "working"}
                }) + "\\n").encode())
            deadline = time.monotonic() + 10
            while not probe_seen.exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("normalizer did not observe probe")
                time.sleep(0.01)
            log.unlink()
            sys.exit(0)
            """
        ),
        encoding="utf-8",
    )
    normalizer = ClaudeCodeBackend().parent_candidate_normalizer(marker)

    def observe_probe(record: dict[str, Any], offset: int) -> Any:
        if record.get("uuid") == "probe":
            probe_seen.write_text("seen", encoding="utf-8")
        return normalizer(record, offset)

    result = await run_managed_async(
        [sys.executable, str(script), str(session_dir), str(probe_seen)],
        cwd=tmp_path,
        timeout=60,
        session_log_dir=session_dir,
        completion_marker=marker,
        stream_parser_factory=ClaudeCodeBackend().stream_parser_factory(marker),
        parent_candidate_normalizer=observe_probe,
        completion_drain_timeout=1.0,
        _phase1_timeout=120,
        _phase1_poll=0.005,
        _phase2_poll=0.05,
        _heartbeat_poll=0.005,
        _session_id_timeout=1.0,
    )

    assert result.termination is TerminationReason.HEALTH_INSPECTOR
    assert result.lifecycle_decision is LifecycleDecision.CATCH_UP_FAILED
    assert result.eligible_source is None
    assert result.channel_confirmation is ChannelConfirmation.UNMONITORED
