"""Integration tests for execute_termination_action (1b).

Uses real subprocesses to verify drain-window behavior. All tests here must
FAIL before Phase 2 implements execute_termination_action and the KillReason enum.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from signal import SIGKILL, SIGTERM
from unittest.mock import MagicMock

import anyio
import pytest

import autoskillit.execution.process._process_kill as process_kill_module
from autoskillit.core.types import (
    CleanupOutcome,
    KillReason,
    ProcessIdentity,
    TerminationAction,
    TerminationReason,
)
from autoskillit.execution.process import _OwnedProcessFinalizer, execute_termination_action
from autoskillit.execution.process._process_kill import (
    _finalize_owned_process_sync,
    _TerminationExecution,
)
from autoskillit.execution.process._process_ownership import (
    OwnedProcessIdentityTracker,
    _IdentityStatus,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

# ---------------------------------------------------------------------------
# Helper scripts
# ---------------------------------------------------------------------------

# Script that exits after sleeping N seconds (float arg)
_EXIT_AFTER_SLEEP = textwrap.dedent("""\
    import sys, time
    delay = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
    time.sleep(delay)
    sys.exit(0)
""")


class _RawProcess:
    pid = 123

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1

    async def wait(self) -> int:
        self.returncode = 0
        return 0


class _SyncRawProcess:
    pid = 123

    def __init__(
        self,
        *,
        terminate_lookup_error: bool = False,
        kill_lookup_error: bool = False,
        waits_before_reap: int = 0,
    ) -> None:
        self.returncode: int | None = None
        self.terminate_lookup_error = terminate_lookup_error
        self.kill_lookup_error = kill_lookup_error
        self.waits_before_reap = waits_before_reap
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.terminate_lookup_error:
            raise ProcessLookupError

    def kill(self) -> None:
        self.kill_calls += 1
        if self.kill_lookup_error:
            raise ProcessLookupError

    def wait(self, timeout: float) -> int:
        self.wait_calls += 1
        if self.wait_calls <= self.waits_before_reap:
            raise subprocess.TimeoutExpired("helper", timeout)
        self.returncode = 0
        return 0


def _known_tracker(*, include_unknown: bool = False) -> OwnedProcessIdentityTracker:
    tracker = OwnedProcessIdentityTracker()
    tracker.seed_root(123, process_group_id=123, session_id=123)
    tracker.register_root(
        123,
        1000,
        10.0,
        process_group_id=123,
        session_id=123,
    )
    if include_unknown:
        tracker.add_descendant(124, 0, 0.0)
    return tracker


async def _spawn_script(script_text: str, args: list[str], tmp_path) -> anyio.abc.Process:
    """Spawn a Python subprocess running script_text with args."""
    script = tmp_path / "helper.py"
    script.write_text(script_text)
    return await anyio.open_process(
        [sys.executable, str(script), *args],
        start_new_session=True,
    )


@pytest.mark.anyio
async def test_finalizer_construction_seeds_raw_process_without_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = OwnedProcessIdentityTracker()
    process = _RawProcess()

    def _unexpected(*_args, **_kwargs):
        raise AssertionError("finalizer construction performed identity I/O")

    monkeypatch.setattr(tracker, "enrich_root_identity", _unexpected)
    monkeypatch.setattr(tracker, "refresh_from_process_group", _unexpected)

    finalizer = _OwnedProcessFinalizer(
        tracker=tracker,
        budget_seconds=1.0,
        process=process,  # type: ignore[arg-type]
    )

    assert finalizer.owned_root_pid == process.pid
    assert tracker.root_pid == process.pid
    assert tracker.process_group_id == process.pid
    assert tracker.session_id == process.pid
    assert finalizer.cleanup_deadline is None


@pytest.mark.anyio
async def test_owned_finalizer_is_single_flight_same_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finalizer = _OwnedProcessFinalizer(tracker=_known_tracker(), budget_seconds=1.0)
    started = anyio.Event()
    release = anyio.Event()
    calls = 0
    expected = CleanupOutcome(succeeded=True, budget_exhausted=False)

    async def _run_once(_self: _OwnedProcessFinalizer) -> CleanupOutcome:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return expected

    monkeypatch.setattr(_OwnedProcessFinalizer, "_run_once", _run_once)
    outcomes: list[CleanupOutcome] = []

    async def _run() -> None:
        outcomes.append(await finalizer.run())

    async with anyio.create_task_group() as tg:
        tg.start_soon(_run)
        await started.wait()
        tg.start_soon(_run)
        release.set()

    assert calls == 1
    assert len(outcomes) == 2
    assert outcomes[0] is outcomes[1]
    assert outcomes[0] is expected


@pytest.mark.anyio
async def test_outcome_construction_failure_wakes_two_waiters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finalizer = _OwnedProcessFinalizer(tracker=_known_tracker(), budget_seconds=1.0)
    started = anyio.Event()
    release = anyio.Event()

    async def _fail_once(_self: _OwnedProcessFinalizer) -> CleanupOutcome:
        started.set()
        await release.wait()
        raise RuntimeError("cleanup-body-failed")

    def _fail_outcome(*_args, **_kwargs):
        raise RuntimeError("outcome-construction-failed")

    monkeypatch.setattr(_OwnedProcessFinalizer, "_run_once", _fail_once)
    monkeypatch.setattr(process_kill_module, "CleanupOutcome", _fail_outcome)
    outcomes: list[CleanupOutcome] = []

    async def _run() -> None:
        outcomes.append(await finalizer.run())

    async with anyio.create_task_group() as tg:
        tg.start_soon(_run)
        await started.wait()
        tg.start_soon(_run)
        await anyio.sleep(0)
        release.set()

    assert len(outcomes) == 2
    assert outcomes[0] is outcomes[1]
    assert outcomes[0] is finalizer.outcome
    assert not outcomes[0].succeeded
    assert finalizer._done.is_set()


@pytest.mark.anyio
async def test_owned_finalizer_reports_budget_exhaustion() -> None:
    tracker = _known_tracker()
    finalizer = _OwnedProcessFinalizer(
        tracker=tracker,
        budget_seconds=0.0,
    )

    outcome = await finalizer.run()

    assert not outcome.succeeded
    assert outcome.budget_exhausted
    assert outcome.retained_identities == ()
    assert outcome.unknown_identities == tracker.snapshot_known_identities()


@pytest.mark.anyio
async def test_raw_handle_only_fallback_never_uses_pid_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = OwnedProcessIdentityTracker()
    process = _RawProcess()
    monkeypatch.setattr(tracker, "enrich_root_identity", lambda: False)
    monkeypatch.setattr(tracker, "refresh_from_process_group", lambda: 0)
    monkeypatch.setattr(
        "autoskillit.execution.process._process_kill.signal_process_identity",
        lambda *_args: pytest.fail("PID-directed signal used for unknown root"),
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_kill.async_kill_process_tree",
        lambda *_args: pytest.fail("raw PID tree kill used for unknown root"),
    )

    outcome = await _OwnedProcessFinalizer(
        tracker=tracker,
        budget_seconds=1.0,
        process=process,  # type: ignore[arg-type]
    ).run()

    assert process.terminate_calls == 1
    assert process.kill_calls == 0
    assert outcome.succeeded
    assert outcome.unknown_identities == ()


@pytest.mark.anyio
async def test_unknown_async_root_still_discovers_and_signals_known_descendant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = OwnedProcessIdentityTracker()
    process = _RawProcess()
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(tracker, "enrich_root_identity", lambda: False)

    def _refresh() -> int:
        tracker.add_descendant(124, 2000, 20.0)
        return 1

    def _signal(identity: ProcessIdentity, signal_number: int) -> _IdentityStatus:
        signals.append((identity.root_pid, signal_number))
        return _IdentityStatus.ALIVE

    monkeypatch.setattr(tracker, "refresh_from_process_group", _refresh)
    monkeypatch.setattr(
        "autoskillit.execution.process._process_kill.signal_process_identity", _signal
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_kill.inspect_pid_identity",
        lambda _identity: _IdentityStatus.ABSENT,
    )

    outcome = await _OwnedProcessFinalizer(
        tracker=tracker,
        budget_seconds=2.0,
        process=process,  # type: ignore[arg-type]
    ).run()

    assert process.terminate_calls == 1
    assert process.kill_calls == 0
    assert signals == [(124, SIGTERM), (124, SIGKILL)]
    assert outcome.succeeded
    assert outcome.unknown_identities == ()


@pytest.mark.anyio
async def test_unknown_async_descendant_prevents_cleanup_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = OwnedProcessIdentityTracker()
    process = _RawProcess()
    monkeypatch.setattr(tracker, "enrich_root_identity", lambda: False)

    def _refresh() -> int:
        tracker.add_descendant(124, 0, 0.0)
        return 1

    monkeypatch.setattr(tracker, "refresh_from_process_group", _refresh)

    outcome = await _OwnedProcessFinalizer(
        tracker=tracker,
        budget_seconds=1.0,
        process=process,  # type: ignore[arg-type]
    ).run()

    assert process.returncode == 0
    assert not outcome.succeeded
    assert [identity.root_pid for identity in outcome.unknown_identities] == [124]


@pytest.mark.anyio
async def test_deadline_starts_once_and_is_shared_with_grace() -> None:
    finalizer = _OwnedProcessFinalizer(tracker=_known_tracker(), budget_seconds=5.0)

    assert finalizer.start_deadline(now=10.0) == 15.0
    assert finalizer.start_deadline(now=100.0) == 15.0
    assert finalizer.remaining_time(limit=10.0, now=12.0) == 3.0
    assert finalizer.remaining_time(limit=1.0, now=12.0) == 1.0


@pytest.mark.anyio
async def test_preflight_refreshes_and_classifies_retained_descendant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _known_tracker()
    process = _RawProcess()
    process.returncode = 0

    def _refresh() -> int:
        tracker.add_descendant(124, 2000, 20.0)
        return 1

    monkeypatch.setattr(tracker, "refresh_from_process_group", _refresh)
    monkeypatch.setattr(
        "autoskillit.execution.process._process_kill.inspect_pid_identity",
        lambda identity: (
            _IdentityStatus.ALIVE if identity.root_pid == 124 else _IdentityStatus.ABSENT
        ),
    )
    finalizer = _OwnedProcessFinalizer(
        tracker=tracker,
        budget_seconds=1.0,
        process=process,  # type: ignore[arg-type]
    )

    preflight = await finalizer.preflight()

    assert [identity.root_pid for identity in preflight.live_identities] == [124]
    assert preflight.has_live_or_unknown
    assert finalizer.cleanup_deadline is not None
    assert not finalizer.signaled


@pytest.mark.anyio
@pytest.mark.parametrize("failure_phase", ["refresh", "signal", "verify"])
async def test_internal_failure_is_cached_for_all_waiters(
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    tracker = _known_tracker(include_unknown=True)
    monkeypatch.setattr(tracker, "refresh_from_process_group", lambda: 0)
    monkeypatch.setattr(
        "autoskillit.execution.process._process_kill.signal_process_identity",
        lambda *_args: _IdentityStatus.ABSENT,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_kill.inspect_pid_identity",
        lambda *_args: _IdentityStatus.ABSENT,
    )

    def _fail() -> int:
        raise RuntimeError(failure_phase)

    if failure_phase == "refresh":
        monkeypatch.setattr(tracker, "refresh_from_process_group", _fail)
    elif failure_phase == "signal":
        monkeypatch.setattr(
            "autoskillit.execution.process._process_kill.signal_process_identity",
            lambda *_args: _fail(),
        )
    else:
        monkeypatch.setattr(
            "autoskillit.execution.process._process_kill.inspect_pid_identity",
            lambda *_args: _fail(),
        )

    finalizer = _OwnedProcessFinalizer(tracker=tracker, budget_seconds=1.0)
    outcomes: list[CleanupOutcome] = []

    async def _run() -> None:
        outcomes.append(await finalizer.run())

    async with anyio.create_task_group() as tg:
        tg.start_soon(_run)
        tg.start_soon(_run)

    assert len(outcomes) == 2
    assert outcomes[0] is outcomes[1]
    assert not outcomes[0].succeeded
    assert outcomes[0].retained_identities == ()
    assert outcomes[0].unknown_identities == tracker.snapshot_identities()
    assert isinstance(finalizer.failure, RuntimeError)


@pytest.mark.anyio
async def test_conservative_failure_retains_only_identity_proved_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _known_tracker()
    tracker.add_descendant(124, 2000, 20.0)
    monkeypatch.setattr(tracker, "refresh_from_process_group", lambda: 0)
    monkeypatch.setattr(
        "autoskillit.execution.process._process_kill.signal_process_identity",
        lambda *_args: _IdentityStatus.ABSENT,
    )
    inspections = 0

    def _inspect(_identity: ProcessIdentity) -> _IdentityStatus:
        nonlocal inspections
        inspections += 1
        if inspections == 1:
            return _IdentityStatus.ALIVE
        raise RuntimeError("second-verification-failed")

    monkeypatch.setattr(
        "autoskillit.execution.process._process_kill.inspect_pid_identity", _inspect
    )

    outcome = await _OwnedProcessFinalizer(tracker=tracker, budget_seconds=1.0).run()

    assert [identity.root_pid for identity in outcome.retained_identities] == [123]
    assert [identity.root_pid for identity in outcome.unknown_identities] == [124]


@pytest.mark.anyio
async def test_owned_finalizer_fails_closed_on_live_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _known_tracker()
    signals: list[int] = []

    def _mismatch(_identity: ProcessIdentity, signal_number: int) -> _IdentityStatus:
        signals.append(signal_number)
        return _IdentityStatus.UNKNOWN

    monkeypatch.setattr(tracker, "refresh_from_process_group", lambda: 0)
    monkeypatch.setattr(
        "autoskillit.execution.process._process_kill.signal_process_identity", _mismatch
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_kill.inspect_pid_identity",
        lambda _identity: _IdentityStatus.UNKNOWN,
    )

    outcome = await _OwnedProcessFinalizer(tracker=tracker, budget_seconds=1.0).run()

    assert signals == [SIGTERM, SIGKILL]
    assert not outcome.succeeded
    assert outcome.retained_identities == ()
    assert outcome.unknown_identities == tracker.snapshot_known_identities()


def test_sync_finalizer_signals_only_through_identity_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _known_tracker()
    process = MagicMock(pid=123, returncode=0)
    signals: list[tuple[ProcessIdentity, int]] = []

    def _validated_signal(identity: ProcessIdentity, signal_number: int) -> _IdentityStatus:
        signals.append((identity, signal_number))
        return _IdentityStatus.ABSENT

    monkeypatch.setattr(tracker, "refresh_from_process_group", lambda: 0)
    monkeypatch.setattr(
        "autoskillit.execution.process._process_kill.signal_process_identity",
        _validated_signal,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_kill.inspect_pid_identity",
        lambda _identity: _IdentityStatus.ABSENT,
    )

    outcome = _finalize_owned_process_sync(
        process=process,
        tracker=tracker,
        budget_seconds=1.0,
    )

    identity = tracker.snapshot_known_identities()[0]
    assert signals == [(identity, SIGTERM), (identity, SIGKILL)]
    assert outcome.succeeded


def test_sync_failure_outcome_construction_returns_emergency_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _known_tracker()
    process = _SyncRawProcess()

    def _fail_refresh() -> int:
        raise RuntimeError("refresh-failed")

    def _fail_outcome(*_args, **_kwargs):
        raise RuntimeError("outcome-construction-failed")

    monkeypatch.setattr(tracker, "refresh_from_process_group", _fail_refresh)
    monkeypatch.setattr(process_kill_module, "CleanupOutcome", _fail_outcome)

    outcome = _finalize_owned_process_sync(
        process=process,  # type: ignore[arg-type]
        tracker=tracker,
        budget_seconds=1.0,
    )

    assert not outcome.succeeded
    assert outcome.budget_exhausted
    assert outcome.retained_identities == ()
    assert outcome.unknown_identities == ()


def test_unknown_sync_root_discovers_and_signals_known_descendant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = OwnedProcessIdentityTracker()
    process = _SyncRawProcess(terminate_lookup_error=True)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(tracker, "enrich_root_identity", lambda: False)

    def _refresh() -> int:
        tracker.add_descendant(124, 2000, 20.0)
        return 1

    def _signal(identity: ProcessIdentity, signal_number: int) -> _IdentityStatus:
        signals.append((identity.root_pid, signal_number))
        return _IdentityStatus.ALIVE

    monkeypatch.setattr(tracker, "refresh_from_process_group", _refresh)
    monkeypatch.setattr(
        "autoskillit.execution.process._process_kill.signal_process_identity", _signal
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_kill.inspect_pid_identity",
        lambda _identity: _IdentityStatus.ABSENT,
    )

    outcome = _finalize_owned_process_sync(
        process=process,  # type: ignore[arg-type]
        tracker=tracker,
        budget_seconds=2.0,
    )

    assert process.terminate_calls == 1
    assert process.kill_calls == 0
    assert signals == [(124, SIGTERM), (124, SIGKILL)]
    assert outcome.succeeded


def test_unknown_sync_descendant_prevents_cleanup_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = OwnedProcessIdentityTracker()
    process = _SyncRawProcess()
    monkeypatch.setattr(tracker, "enrich_root_identity", lambda: False)

    def _refresh() -> int:
        tracker.add_descendant(124, 0, 0.0)
        return 1

    monkeypatch.setattr(tracker, "refresh_from_process_group", _refresh)

    outcome = _finalize_owned_process_sync(
        process=process,  # type: ignore[arg-type]
        tracker=tracker,
        budget_seconds=1.0,
    )

    assert process.returncode == 0
    assert not outcome.succeeded
    assert [identity.root_pid for identity in outcome.unknown_identities] == [124]


def test_unknown_sync_root_catches_kill_process_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = OwnedProcessIdentityTracker()
    process = _SyncRawProcess(kill_lookup_error=True, waits_before_reap=1)
    monkeypatch.setattr(tracker, "enrich_root_identity", lambda: False)
    monkeypatch.setattr(tracker, "refresh_from_process_group", lambda: 0)

    outcome = _finalize_owned_process_sync(
        process=process,  # type: ignore[arg-type]
        tracker=tracker,
        budget_seconds=1.0,
    )

    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert process.returncode == 0
    assert outcome.succeeded


def test_private_termination_execution_carries_cached_cleanup() -> None:
    cleanup = CleanupOutcome(succeeded=True, budget_exhausted=False)

    execution = _TerminationExecution(KillReason.NATURAL_EXIT, cleanup)

    assert execution.kill_reason is KillReason.NATURAL_EXIT
    assert execution.cleanup_outcome is cleanup


@pytest.mark.anyio
async def test_termination_action_delegates_to_finalizer() -> None:
    class _Finalizer:
        calls = 0
        signaled = False
        cleanup = CleanupOutcome(succeeded=True, budget_exhausted=False)

        def start_deadline(self) -> float:
            return 1.0

        async def run(self) -> CleanupOutcome:
            self.calls += 1
            return self.cleanup

    proc = MagicMock(pid=123, returncode=None)
    finalizer = _Finalizer()
    execution = await execute_termination_action(
        TerminationAction.IMMEDIATE_KILL,
        termination=TerminationReason.TIMED_OUT,
        proc=proc,
        process_exited_event=anyio.Event(),
        grace_seconds=1.0,
        proc_log=MagicMock(),
        finalizer=finalizer,  # type: ignore[arg-type]
    )

    assert execution.kill_reason is KillReason.INFRA_KILL
    assert execution.cleanup_outcome is finalizer.cleanup
    assert finalizer.calls == 1


class _StubFinalizer:
    def __init__(
        self,
        callback=None,
        *,
        remaining: float = 10.0,
        signaled: bool = False,
    ) -> None:
        self.callback = callback
        self.remaining = remaining
        self.calls = 0
        self.deadline_starts = 0
        self.signaled = signaled
        self.cleanup = CleanupOutcome(succeeded=True, budget_exhausted=False)

    def start_deadline(self) -> float:
        self.deadline_starts += 1
        return 1.0

    def remaining_time(self, *, limit: float | None = None) -> float:
        if limit is None:
            return self.remaining
        return min(limit, self.remaining)

    async def run(self) -> CleanupOutcome:
        self.calls += 1
        if self.callback is not None:
            await self.callback()
        return self.cleanup


class TestDrainWindowPermitsNaturalExit:
    """DRAIN_THEN_KILL_IF_ALIVE: process exits inside window → natural_exit."""

    @pytest.mark.anyio
    async def test_drain_window_permits_natural_exit_when_process_exits_inside_window(
        self, tmp_path
    ) -> None:
        """Process exits at 0.5s; grace=3.0s → kill_reason=NATURAL_EXIT, no kill called."""
        proc = await _spawn_script(_EXIT_AFTER_SLEEP, ["0.5"], tmp_path)
        proc_exited_event = anyio.Event()

        # Start a task that waits for the process and sets the event
        async def _watch() -> None:
            await proc.wait()
            proc_exited_event.set()

        import structlog

        finalizer = _StubFinalizer()
        proc_log = structlog.get_logger().bind(pid=proc.pid)
        async with anyio.create_task_group() as tg:
            tg.start_soon(_watch)
            execution = await execute_termination_action(
                TerminationAction.DRAIN_THEN_KILL_IF_ALIVE,
                termination=TerminationReason.COMPLETED,
                proc=proc,
                process_exited_event=proc_exited_event,
                grace_seconds=3.0,
                proc_log=proc_log,
                finalizer=finalizer,  # type: ignore[arg-type]
            )
            tg.cancel_scope.cancel()

        assert execution.kill_reason == KillReason.NATURAL_EXIT
        assert execution.cleanup_outcome is finalizer.cleanup
        assert finalizer.calls == 1
        assert finalizer.deadline_starts == 1


class TestDrainWindowEscalatesToKill:
    """DRAIN_THEN_KILL_IF_ALIVE: process survives grace window → KILL_AFTER_COMPLETION."""

    @pytest.mark.anyio
    async def test_drain_window_escalates_to_kill_when_process_survives(self, tmp_path) -> None:
        """Process sleeps 10s; grace=0.3s → kill_reason=KILL_AFTER_COMPLETION, kill called once."""
        proc = await _spawn_script(_EXIT_AFTER_SLEEP, ["10.0"], tmp_path)
        proc_exited_event = anyio.Event()

        async def _finalize() -> None:
            proc.terminate()
            await proc.wait()

        import structlog

        proc_log = structlog.get_logger().bind(pid=proc.pid)

        finalizer = _StubFinalizer(_finalize, signaled=True)
        try:
            execution = await execute_termination_action(
                TerminationAction.DRAIN_THEN_KILL_IF_ALIVE,
                termination=TerminationReason.COMPLETED,
                proc=proc,
                process_exited_event=proc_exited_event,
                grace_seconds=0.3,
                proc_log=proc_log,
                finalizer=finalizer,  # type: ignore[arg-type]
            )
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

        assert execution.kill_reason == KillReason.KILL_AFTER_COMPLETION
        assert execution.cleanup_outcome is finalizer.cleanup
        assert finalizer.calls == 1


class TestImmediateKillSkipsDrain:
    """IMMEDIATE_KILL: no drain delay, kill called within milliseconds."""

    @pytest.mark.anyio
    async def test_immediate_kill_skips_drain(self, tmp_path) -> None:
        """IMMEDIATE_KILL must call kill without waiting for drain window."""
        proc = await _spawn_script(_EXIT_AFTER_SLEEP, ["10.0"], tmp_path)
        proc_exited_event = anyio.Event()

        call_time: list[float] = []

        async def _finalize() -> None:
            call_time.append(time.monotonic())
            proc.terminate()
            await proc.wait()

        import structlog

        proc_log = structlog.get_logger().bind(pid=proc.pid)
        start = time.monotonic()

        finalizer = _StubFinalizer(_finalize)
        try:
            execution = await execute_termination_action(
                TerminationAction.IMMEDIATE_KILL,
                termination=TerminationReason.TIMED_OUT,
                proc=proc,
                process_exited_event=proc_exited_event,
                grace_seconds=3.0,  # large grace, should be ignored
                proc_log=proc_log,
                finalizer=finalizer,  # type: ignore[arg-type]
            )
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

        elapsed = (call_time[0] - start) if call_time else 999.0
        assert execution.kill_reason == KillReason.INFRA_KILL
        assert execution.cleanup_outcome is finalizer.cleanup
        assert finalizer.calls == 1
        assert elapsed < 0.5, f"IMMEDIATE_KILL took {elapsed:.3f}s — should be near-instant"


class TestNoKillFinalizesOwnedState:
    """NO_KILL still verifies the invocation-owned process set."""

    @pytest.mark.anyio
    async def test_no_kill_action_never_touches_kill_helper(self, tmp_path) -> None:
        """NO_KILL returns NATURAL_EXIT with the finalizer's cached cleanup."""
        proc = await _spawn_script(_EXIT_AFTER_SLEEP, ["0.1"], tmp_path)
        await proc.wait()  # let it exit first
        proc_exited_event = anyio.Event()
        proc_exited_event.set()

        import structlog

        proc_log = structlog.get_logger().bind(pid=proc.pid)
        finalizer = _StubFinalizer()
        execution = await execute_termination_action(
            TerminationAction.NO_KILL,
            termination=TerminationReason.NATURAL_EXIT,
            proc=proc,
            process_exited_event=proc_exited_event,
            grace_seconds=3.0,
            proc_log=proc_log,
            finalizer=finalizer,  # type: ignore[arg-type]
        )

        assert execution.kill_reason == KillReason.NATURAL_EXIT
        assert execution.cleanup_outcome is finalizer.cleanup
        assert finalizer.calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "termination,action,signaled,cleanup_succeeded,retained_at_exit,expected_reason",
    [
        (
            TerminationReason.COMPLETED,
            TerminationAction.DRAIN_THEN_KILL_IF_ALIVE,
            True,
            True,
            False,
            KillReason.KILL_AFTER_COMPLETION,
        ),
        (
            TerminationReason.COMPLETED,
            TerminationAction.DRAIN_THEN_KILL_IF_ALIVE,
            False,
            False,
            False,
            KillReason.KILL_AFTER_COMPLETION,
        ),
        (
            TerminationReason.NATURAL_EXIT,
            TerminationAction.NO_KILL,
            True,
            True,
            False,
            KillReason.INFRA_KILL,
        ),
        (
            TerminationReason.COMPLETED,
            TerminationAction.DRAIN_THEN_KILL_IF_ALIVE,
            False,
            True,
            True,
            KillReason.KILL_AFTER_COMPLETION,
        ),
    ],
)
async def test_cleanup_cannot_report_natural_after_signal_or_failure(
    termination: TerminationReason,
    action: TerminationAction,
    signaled: bool,
    cleanup_succeeded: bool,
    retained_at_exit: bool,
    expected_reason: KillReason,
) -> None:
    proc = MagicMock(pid=123, returncode=0)
    exited = anyio.Event()
    exited.set()
    finalizer = _StubFinalizer(signaled=signaled)
    finalizer.cleanup = CleanupOutcome(
        succeeded=cleanup_succeeded,
        budget_exhausted=False,
    )

    execution = await execute_termination_action(
        action,
        termination=termination,
        proc=proc,
        process_exited_event=exited,
        grace_seconds=1.0,
        proc_log=MagicMock(),
        finalizer=finalizer,  # type: ignore[arg-type]
        retained_ownership_at_exit=retained_at_exit,
    )

    assert execution.kill_reason is expected_reason
    assert execution.cleanup_outcome is finalizer.cleanup
    assert finalizer.calls == 1
