"""Integration tests for Channel B drain-race and COMPLETED pipeline adjudication."""

from __future__ import annotations

import sys
import textwrap

import pytest

from autoskillit.core.types import ChannelConfirmation, SubprocessResult, TerminationReason
from autoskillit.execution.process import run_managed_async
from tests.conftest import TimeoutTier
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
    sys.stdout.write(json.dumps({"type": "system", "session_id": "session"}) + "\\n")
    sys.stdout.flush()
    # Small delay to ensure file ctime > spawn_time recorded in run_managed_async
    time.sleep(0.1)
    with open(os.path.join(session_dir, "session.jsonl"), "w") as f:
        record = {"type": "assistant", "message": {"role": "assistant",
                  "content": "%%ORDER_UP%%"}}
        f.write(json.dumps(record) + "\\n")
        f.flush()
    # Wait until after Channel B fires (phase1_poll + phase2_poll), then write stdout.
    # Callers pass this delay as sys.argv[2]; default 4.0 matches production poll defaults.
    time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 4.0)
    result = {"type": "result", "subtype": "success", "is_error": False,
              "result": "done", "session_id": "s1"}
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
    with open(os.path.join(session_dir, "session.jsonl"), "w") as f:
        record = {"type": "assistant", "message": {"role": "assistant",
                  "content": "%%ORDER_UP%%"}}
        f.write(json.dumps(record) + "\\n")
        f.flush()
    time.sleep(300)
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
    with open(os.path.join(session_dir, "session.jsonl"), "w") as f:
        record = {"type": "assistant", "message": {"role": "assistant",
                  "content": "%%ORDER_UP%%"}}
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
    with open(os.path.join(session_dir, "session.jsonl"), "w") as f:
        record = {"type": "assistant", "message": {"role": "assistant",
                  "content": "%%ORDER_UP%%"}}
        f.write(json.dumps(record) + "\\n")
        f.flush()
    payload = {"type": "result", "subtype": "success", "is_error": False,
               "result": "", "session_id": "test-drain"}
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\\n")
    sys.stdout.flush()
    sys.exit(0)
""")


@pytest.mark.timeout(180)
class TestChannelBDrainWait:
    """Channel B (session monitor) winning before Channel A triggers bounded drain wait."""

    @pytest.mark.anyio
    async def test_channel_b_wins_then_channel_a_confirms_within_drain(self, tmp_path):
        """Channel B fires first; drain wait allows Channel A to confirm stdout data.

        Sequence (fast poll params):
          t=0.00s  subprocess starts
          t=0.10s  script writes %%ORDER_UP%% to session JSONL (Channel B target)
          t=0.11s  Phase 1 poll fires → session file found
          t=0.16s  Phase 2 poll fires → marker detected → Channel B fires → drain starts
          t=0.25s  script writes type=result to stdout (0.15s after JSONL write)
          t=0.30s  heartbeat fires → Channel A confirms → drain completes
          t~0.30s  process killed with confirmed stdout
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "channel_b_then_a.py"
        script.write_text(CHANNEL_B_THEN_A_CONFIRM_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir), "0.15"],
            cwd=tmp_path,
            timeout=TimeoutTier.CHANNEL_B,
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            completion_drain_timeout=5.0,
            _phase1_timeout=120,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
            _heartbeat_poll=0.05,
            _session_id_timeout=0.01,
        )

        assert result.termination == TerminationReason.COMPLETED
        # Drain wait confirmed Channel A fired: stdout is non-empty
        assert result.stdout.strip()

    @pytest.mark.anyio
    async def test_channel_b_wins_drain_timeout_still_kills(self, tmp_path):
        """Channel B fires; Channel A never fires; drain times out and process is killed.

        Sequence (fast poll params):
          t=0.10s  script writes %%ORDER_UP%% to session JSONL
          t=0.16s  Channel B fires → drain wait starts with 0.5s timeout
          t=0.66s  drain times out (script never wrote to stdout)
          t=0.66s  process killed with empty stdout

        timeout=TimeoutTier.CHANNEL_B (60s): guards against the outer wall-clock expiring under xdist -n 4 load.
        _watch_session_log waits up to _session_id_timeout (default 1.0s, tests pass 0.01s)
        for stdout_session_id_ready before Phase 1 starts;
        under CI load both the preamble and Phase 1 polls can overrun, so the outer
        timeout must exceed _session_id_timeout + _phase1_timeout (30s default) + drain (0.5s) = 31.5s.
        _phase1_timeout=120: must exceed outer timeout (60s) so that Phase 1 never fires
        first with STALE when subprocess startup is slow under WSL2 + xdist load; the
        outer 60s guard cancels all tasks before Phase 1 can timeout independently.
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "channel_b_no_stdout.py"
        script.write_text(CHANNEL_B_NO_STDOUT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=TimeoutTier.CHANNEL_B,
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            completion_drain_timeout=0.5,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
            _session_id_timeout=0.01,
            _phase1_timeout=120,
        )

        assert result.termination == TerminationReason.COMPLETED
        # Drain timed out: CLI hung and never flushed its result record
        assert not result.stdout.strip()

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
        assert result.stdout.strip()  # Channel A confirmed: stdout is non-empty

    @pytest.mark.anyio
    async def test_data_confirmed_false_set_on_drain_timeout(self, tmp_path):
        """Channel B wins the race; drain timeout expires without Channel A confirming.

        Verifies that SubprocessResult.data_confirmed is False when the bounded
        drain wait times out — i.e. Channel A never confirmed stdout data.
        _phase1_timeout=120: must exceed outer timeout (60s) to prevent Phase 1 from
        firing STALE before the outer guard when subprocess startup is slow under load.
        completion_drain_timeout=0.5: 0.1s was too tight under xdist -n 4 load; the
        event loop may not process the drain callback before pytest-timeout fires.
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
            timeout=TimeoutTier.CHANNEL_B,
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            completion_drain_timeout=0.5,
            natural_exit_grace_seconds=0.1,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
            _session_id_timeout=0.01,
            _phase1_timeout=120,
        )

        assert result.termination == TerminationReason.COMPLETED
        assert result.channel_confirmation == ChannelConfirmation.CHANNEL_B

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

    @pytest.mark.anyio
    async def test_channel_b_then_a_empty_result_data_confirmed_is_false(self, tmp_path):
        """Channel B fires (%%ORDER_UP%% in JSONL).

        Within the drain window, Claude CLI writes a type=result record with
        result="". Channel A must NOT confirm on this — data_confirmed must
        remain False so the provenance bypass can fire.
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "channel_b_empty.py"
        script.write_text(CHANNEL_B_THEN_A_EMPTY_RESULT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=TimeoutTier.CHANNEL_B,
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            completion_drain_timeout=2.0,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
            _heartbeat_poll=0.05,
            _session_id_timeout=0.01,
            _phase1_timeout=120,
        )
        assert result.termination == TerminationReason.COMPLETED
        assert (
            result.channel_confirmation == ChannelConfirmation.CHANNEL_B
        )  # FAILS before fix: True


@pytest.mark.timeout(180)
class TestChannelBFullPipelineAdjudication:
    """Full end-to-end adjudication for Channel B drain-race scenarios."""

    @pytest.mark.anyio
    async def test_channel_b_then_a_empty_result_produces_success(self, tmp_path):
        """Full end-to-end: Channel B fires, CLI writes type=result with result="".

        With strengthened Channel A, data_confirmed=False, provenance bypass fires.
        Result: success=True, needs_retry=False (no wasteful retry of completed session).

        Timing notes:
        - completion_drain_timeout=0.5s: the heartbeat has already seen the empty result
          and failed to confirm by the time Channel B fires (~1s after task group start),
          so 0.5s of additional drain time is more than sufficient semantically.
        - timeout=TimeoutTier.CHANNEL_B (60s): guards against the outer wall-clock expiring under xdist -n 4 load.
          Under heavy load the stdout_session_id_ready wait (_session_id_timeout, default 1.0s, tests pass 0.01s)
          and inner drain (0.5s) can each overrun 10x, giving a worst-case total of ~15s well inside 60s.
        """
        from autoskillit.execution.headless import _build_skill_result

        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "channel_b_empty.py"
        script.write_text(CHANNEL_B_THEN_A_EMPTY_RESULT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=TimeoutTier.CHANNEL_B,
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            completion_drain_timeout=0.5,
            natural_exit_grace_seconds=0.1,
            _phase1_timeout=120,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
            _heartbeat_poll=0.05,
            _session_id_timeout=0.01,
        )
        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="test-command",
            audit=None,
        )
        assert skill_result.success is True  # FAILS before fix: False
        assert skill_result.needs_retry is False  # FAILS before fix: True


class TestChannelBDrainRacePipelineAdjudication:
    """Integration: COMPLETED (Channel B drain timeout) flows through _build_skill_result.

    Uses the existing CHANNEL_B_NO_STDOUT_SCRIPT: session monitor fires, drain expires,
    process is killed with empty stdout. _build_skill_result must apply the Channel B
    provenance bypass (data_confirmed=False → success=True without calling _compute_success).
    """

    @pytest.mark.anyio
    async def test_channel_b_drain_timeout_produces_success_skill_result(self, tmp_path):
        """COMPLETED + data_confirmed=False + empty stdout → success=True, needs_retry=False.

        Channel B provenance bypass: when session monitor wins and drain expires,
        _build_skill_result returns success=True immediately, bypassing _compute_success.
        """
        from autoskillit.execution.headless import _build_skill_result

        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "channel_b_no_stdout.py"
        script.write_text(CHANNEL_B_NO_STDOUT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=TimeoutTier.CHANNEL_B,
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            completion_drain_timeout=0.5,
            _phase1_timeout=120,
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
            result, completion_marker="", skill_command="test", audit=None
        )
        assert skill_result.success is True
        assert skill_result.needs_retry is False


@pytest.mark.timeout(180)
class TestPostExitDrainWindow:
    """Symmetric drain window: process exits first, Channel B gets a bounded window to deposit."""

    @pytest.mark.anyio
    async def test_drain_window_allows_channel_b_to_deposit(self, tmp_path):
        """Process exits before Phase 1 polls; drain window lets Channel B detect marker.

        Uses _phase1_poll=1.0 to guarantee the process exits (~100ms) before the
        first Phase 1 poll fires. The drain window (completion_drain_timeout=30.0)
        gives the session monitor enough time to complete its poll and detect the
        marker in the JSONL file, producing CHANNEL_B confirmation.

        Timing rationale for completion_drain_timeout=30.0:
        - Before channel_b_ready can be set, _watch_session_log must:
            1. Wait up to _session_id_timeout for stdout_session_id_ready (default 1.0s, tests pass 0.01s)
            2. Sleep _phase1_poll=1.0s before Phase 1's first check
            3. Sleep _phase2_poll=0.05s before Phase 2's first check
          Total minimum: ~2.05s under normal conditions.
        - Under xdist -n 4 load, asyncio.sleep() can overrun significantly.
          With 10x jitter on Phase 1 alone (1.0s → 10s) the total exceeds 5.0s.
          30.0s provides ~15x headroom against Phase 1 jitter alone.
        - The test does NOT take 30s: channel_b_ready is set within ~2s normally
          and move_on_after exits as soon as the event fires.
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "process_exit_then_channel_b.py"
        script.write_text(PROCESS_EXIT_THEN_CHANNEL_B_FIRES_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=TimeoutTier.CHANNEL_B,
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            completion_drain_timeout=30.0,
            _phase1_timeout=120,
            _phase1_poll=1.0,
            _phase2_poll=0.05,
            _heartbeat_poll=0.05,
            _session_id_timeout=0.01,
        )

        assert result.channel_confirmation == ChannelConfirmation.CHANNEL_B

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
    with open(os.path.join(session_dir, "session.jsonl"), "w") as f:
        # Sub-skill emits static marker — should NOT trigger completion
        sub_skill_record = {"type": "assistant", "message": {"role": "assistant",
                  "content": "%%ORDER_UP%%"}}
        f.write(json.dumps(sub_skill_record) + "\\n")
        f.flush()
        time.sleep(0.3)
        # Parent emits its unique marker — SHOULD trigger completion
        parent_record = {"type": "assistant", "message": {"role": "assistant",
                  "content": unique_marker}}
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

    @pytest.mark.anyio
    async def test_channel_b_ignores_sub_skill_marker(self, tmp_path):
        """Channel B must not trigger on a sub-skill's static %%ORDER_UP%% marker.

        timeout=TimeoutTier.CHANNEL_B (60s): guards against the outer wall-clock
        expiring under xdist -n 4 load.
        _session_id_timeout=2.0 gives the stdout reader enough headroom under
        heavy parallel load so Channel B monitoring always starts before the
        JSONL markers are written.
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        unique_marker = "%%ORDER_UP::test1234%%"
        script = tmp_path / "sub_skill_collision.py"
        script.write_text(CHANNEL_B_SUB_SKILL_COLLISION_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir), unique_marker],
            cwd=tmp_path,
            timeout=TimeoutTier.CHANNEL_B,
            session_log_dir=session_dir,
            completion_marker=unique_marker,
            completion_drain_timeout=2.0,
            _phase1_timeout=120,
            _phase1_poll=0.05,
            _phase2_poll=0.05,
            _heartbeat_poll=0.05,
            _session_id_timeout=2.0,
        )

        assert result.termination == TerminationReason.COMPLETED
        assert result.channel_confirmation == ChannelConfirmation.CHANNEL_B
