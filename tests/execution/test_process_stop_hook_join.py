"""Real Stop-guard decisions must survive Channel B completion markers."""

from __future__ import annotations

import json
import shutil
import sys
import textwrap
import time
from pathlib import Path
from uuid import uuid4

import anyio
import psutil
import pytest

from autoskillit.core.types import (
    ChannelBStatus,
    ChannelConfirmation,
    KillReason,
    TerminationReason,
)
from autoskillit.execution.backends import ClaudeStreamParser
from autoskillit.execution.process import run_managed_async
from autoskillit.execution.process._process_race import RaceAccumulator
from autoskillit.hook_registry import HOOKS_DIR
from autoskillit.hooks._join_ledger import resolve_flag_dir
from autoskillit.hooks._session_binding import read_binding, resolve_binding_path
from tests.conftest import production_interpreter_env
from tests.execution._process_helpers import coordinate_phase2_start
from tests.hooks._session_binding_helpers import (
    JOIN_HOOK_UNSET,
    complete_one_assignment_wave,
    join_hook_env,
    load_join_bearing_skill,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_PTY_CASES = [
    False,
    pytest.param(
        True, marks=pytest.mark.skipif(shutil.which("script") is None, reason="needs script")
    ),
]
_CHILD_PRELUDE = r"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

config = json.loads(sys.argv[1])
root = Path(config["root"])
session_dir = Path(config["session_dir"])
sid = config["sid"]
marker = config["marker"]
hook_argv = config["hook_argv"]
hook_env = dict(os.environ)
stop_payload = {
    "session_id": sid, "cwd": os.getcwd(),
    "hook_event_name": "Stop", "stop_hook_active": False,
}
transcript = session_dir / (sid + ".jsonl")


def emit_stdout(record):
    print(json.dumps(record), flush=True)


def append_transcript(record):
    with transcript.open("a") as stream:
        stream.write(json.dumps(record) + "\n")


def write_marker(text="plan_path = x.md\n"):
    append_transcript({
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text + marker}]},
    })


def run_hook(argv, payload, env, name):
    result = subprocess.run(
        argv, input=json.dumps(payload), env=env,
        capture_output=True, text=True, timeout=10,
    )
    with (root / "hook_results.jsonl").open("a") as stream:
        record = {"name": name, "rc": result.returncode, "stderr": result.stderr}
        stream.write(json.dumps(record) + "\n")
    return result


def stop_feedback(reason):
    text = "Stop hook feedback:\n[" + " ".join(hook_argv) + "]: " + reason
    user = {"role": "user", "content": [{"type": "text", "text": text}]}
    append_transcript({"type": "user", "isMeta": True, "message": user})
    append_transcript({
        "type": "system", "subtype": "stop_hook_summary",
        "hookErrors": [reason], "preventedContinuation": False, "level": "suggestion",
    })
    emit_stdout({"type": "user", "isSynthetic": True, "message": user})
    emit_stdout({"type": "system", "subtype": "stop-hook-error", "content": reason})


def touch(name):
    (root / name).write_text(str(time.time()))


def wait_for(name):
    deadline = time.monotonic() + 10
    while not (root / name).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(name)
        time.sleep(0.01)


def write_pid():
    (root / "workload.pid").write_text(str(os.getpid()))


emit_stdout({"type": "system", "subtype": "init", "session_id": sid})
append_transcript({"type": "assistant", "message": {"role": "assistant", "content": "working"}})
deadline = time.monotonic() + 10
while not (session_dir / "phase2.ready").exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("phase2.ready")
    time.sleep(0.01)
"""


@pytest.fixture
def stop_case(tmp_path, monkeypatch):
    sid = str(uuid4())
    worktree = load_join_bearing_skill(tmp_path, session_id=sid)
    binding = read_binding(resolve_binding_path(str(worktree), sid))
    assert binding is not None and binding.join_required
    assert resolve_flag_dir(worktree).is_relative_to(worktree)
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    coordinate_phase2_start(monkeypatch, session_dir)
    marker = "%%ORDER_UP%%"

    original_deposit = RaceAccumulator.deposit_session_log_result

    def observe_deposit(self, monitor_result, *args, **kwargs):
        original_deposit(self, monitor_result, *args, **kwargs)
        if monitor_result.status is ChannelBStatus.COMPLETION:
            (tmp_path / "b_deposited").touch()

    monkeypatch.setattr(RaceAccumulator, "deposit_session_log_result", observe_deposit)
    env = production_interpreter_env()
    env.update(join_hook_env(tmp_path))
    for name in JOIN_HOOK_UNSET:
        env.pop(name, None)
    config = {
        "root": str(tmp_path),
        "session_dir": str(session_dir),
        "sid": sid,
        "marker": marker,
        "hook_argv": [
            sys.executable,
            "-B",
            str(HOOKS_DIR / "_dispatch.py"),
            "guards/join_stop_guard",
        ],
    }

    async def run(body, **overrides):
        script = tmp_path / "child.py"
        script.write_text(_CHILD_PRELUDE + textwrap.dedent(body))
        options = {
            "lifecycle_observation_enabled": True,
            "stream_parser": ClaudeStreamParser(completion_marker=marker),
            "session_log_dir": session_dir,
            "completion_marker": marker,
            "_phase1_poll": 0.05,
            "_phase2_poll": 0.05,
            "_heartbeat_poll": 0.05,
            "_session_id_timeout": 0.01,
            "_phase1_timeout": 120,
            "timeout": 30,
            "completion_drain_timeout": 10.0,
            "natural_exit_grace_seconds": 3.0,
            "child_deferral_ceiling": 5.0,
            **overrides,
        }
        result = await run_managed_async(
            [sys.executable, str(script), json.dumps(config)], cwd=worktree, env=env, **options
        )
        assert result.session_id == sid
        assert result.channel_b_session_id == sid
        assert (tmp_path / "b_deposited").exists()
        return result

    return worktree, sid, run


def _hook_results(tmp_path: Path) -> list[dict]:
    return [
        json.loads(line) for line in (tmp_path / "hook_results.jsonl").read_text().splitlines()
    ]


def _guard_statuses(tmp_path: Path) -> list[str]:
    return [
        record["status"]
        for line in (tmp_path / "logs" / "join_diagnostics.jsonl").read_text().splitlines()
        if (record := json.loads(line)).get("gate") == "join_stop_guard"
    ]


async def _wait_for_file(path: Path) -> None:
    while not path.exists():
        await anyio.sleep(0.02)


async def _assert_pid_stopped(pid: int) -> None:
    with anyio.fail_after(5):
        while psutil.pid_exists(pid):
            try:
                if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                    return
            except psutil.NoSuchProcess:
                return
            await anyio.sleep(0.05)


@pytest.mark.anyio
@pytest.mark.timeout(120)
@pytest.mark.parametrize("pty_mode", _PTY_CASES)
async def test_blocked_stop_continuation_completes_required_work_without_preemption(
    tmp_path, stop_case, pty_mode
):
    worktree, sid, run = stop_case

    async def finish_wave():
        with anyio.move_on_after(30):
            await _wait_for_file(tmp_path / "first_stop_done")
            complete_one_assignment_wave(worktree, session_id=sid)
            (tmp_path / "wave_done").touch()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(finish_wave)
        try:
            result = await run(
                """
                write_marker()
                wait_for("b_deposited")
                stop_1 = run_hook(hook_argv, stop_payload, hook_env, "stop_1")
                stop_feedback(stop_1.stderr)
                touch("first_stop_done")
                wait_for("wave_done")
                time.sleep(0.5)
                touch("continuation_ran")
                write_marker()
                run_hook(hook_argv, {**stop_payload, "stop_hook_active": True}, hook_env, "stop_2")
                emit_stdout({"type": "result", "subtype": "success", "is_error": False,
                             "result": "done\\n" + marker, "session_id": sid})
                time.sleep(0.5)
                """,
                pty_mode=pty_mode,
            )
        finally:
            task_group.cancel_scope.cancel()

    first, second = _hook_results(tmp_path)
    assert first["rc"] == 2
    assert "join-bearing skill loaded but no declared wave for this parent" in first["stderr"]
    assert second["rc"] == 0
    assert _guard_statuses(tmp_path) == ["block", "allow"]
    assert (tmp_path / "continuation_ran").exists()
    assert result.returncode == 0
    assert result.channel_confirmation is ChannelConfirmation.CHANNEL_A
    assert "Session terminated" not in result.stdout + result.stderr
    # F-1: the drain's cancelled exit watcher can still label this KILL_AFTER_COMPLETION.


@pytest.mark.anyio
@pytest.mark.timeout(120)
async def test_approved_completion_stops_without_preemption(tmp_path, stop_case):
    worktree, sid, run = stop_case
    complete_one_assignment_wave(worktree, session_id=sid)

    result = await run(
        """
        write_marker()
        wait_for("b_deposited")
        run_hook(hook_argv, stop_payload, hook_env, "stop_1")
        emit_stdout({"type": "result", "subtype": "success", "is_error": False,
                     "result": "done\\n" + marker, "session_id": sid})
        time.sleep(0.5)
        """
    )

    assert _hook_results(tmp_path)[0]["rc"] == 0
    assert _guard_statuses(tmp_path) == ["allow"]
    assert result.returncode == 0
    assert result.channel_confirmation is ChannelConfirmation.CHANNEL_A
    assert "Session terminated" not in result.stdout + result.stderr


@pytest.mark.anyio
@pytest.mark.timeout(120)
async def test_natural_exit_after_blocked_stop_is_reported_natural(tmp_path, stop_case):
    _, _, run = stop_case
    result = await run(
        """
        write_marker()
        wait_for("b_deposited")
        stop_1 = run_hook(hook_argv, stop_payload, hook_env, "stop_1")
        stop_feedback(stop_1.stderr)
        time.sleep(1.0)
        """
    )

    assert _hook_results(tmp_path)[0]["rc"] == 2
    assert _guard_statuses(tmp_path) == ["block"]
    assert result.termination is TerminationReason.NATURAL_EXIT
    assert result.channel_confirmation is ChannelConfirmation.CHANNEL_B
    assert result.returncode == 0
    assert result.kill_reason is KillReason.NATURAL_EXIT


@pytest.mark.anyio
@pytest.mark.timeout(120)
@pytest.mark.parametrize(
    ("ends_by", "pty_mode"),
    [
        ("timeout", False),
        pytest.param(
            "timeout",
            True,
            marks=pytest.mark.skipif(shutil.which("script") is None, reason="needs script"),
        ),
        ("cancel", False),
    ],
)
async def test_repeated_stop_blocks_are_bounded(tmp_path, stop_case, ends_by, pty_mode):
    _, _, run = stop_case
    body = """
        write_pid()
        for index in range(100):
            write_marker()
            if index == 0:
                wait_for("b_deposited")
            stopped = run_hook(
                hook_argv, {**stop_payload, "stop_hook_active": index > 0}, hook_env,
                "stop_" + str(index),
            )
            stop_feedback(stopped.stderr)
            if index == 0:
                touch("first_stop_done")
            time.sleep(0.2)
    """
    if ends_by == "cancel":
        cancelled = False

        async def cancel_after_stop(scope):
            with anyio.move_on_after(30):
                await _wait_for_file(tmp_path / "first_stop_done")
                scope.cancel()

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(cancel_after_stop, task_group.cancel_scope)
            try:
                await run(body)
            except anyio.get_cancelled_exc_class():
                cancelled = True
                raise
            finally:
                task_group.cancel_scope.cancel()
        assert cancelled
    else:
        result = await run(body, timeout=10, pty_mode=pty_mode)
        assert result.termination is TerminationReason.TIMED_OUT
        assert result.kill_reason is KillReason.INFRA_KILL
        assert result.cleanup_evidence.complete is True

    records = _hook_results(tmp_path)
    assert records
    assert all(record["rc"] == 2 for record in records)
    assert "block" in _guard_statuses(tmp_path)
    assert (tmp_path / "b_deposited").exists()
    await _assert_pid_stopped(int((tmp_path / "workload.pid").read_text()))


@pytest.mark.anyio
@pytest.mark.timeout(120)
async def test_exit_before_b_detection_retains_channel_b_fallback(stop_case):
    _, _, run = stop_case
    result = await run("write_marker()\n", _phase2_poll=0.5)

    assert result.termination is TerminationReason.NATURAL_EXIT
    assert result.channel_confirmation is ChannelConfirmation.CHANNEL_B
    assert result.returncode == 0
    assert result.kill_reason is KillReason.NATURAL_EXIT


@pytest.mark.anyio
@pytest.mark.timeout(120)
async def test_schedule_wakeup_defers_release_from_native_terminal(tmp_path, stop_case):
    _, _, run = stop_case
    result = await run(
        """
        write_marker()
        wait_for("b_deposited")
        time.sleep(1.5)
        emit_stdout({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "wake", "name": "ScheduleWakeup", "input": {}}
        ]}})
        touch("a_at")
        emit_stdout({"type": "result", "result": "done\\n" + marker, "session_id": sid})
        time.sleep(30)
        """,
        child_deferral_ceiling=2.0,
        natural_exit_grace_seconds=0.2,
    )
    ended = time.time()

    assert result.schedule_wakeup_violation is True
    assert result.completion_ceiling_expired is True
    assert result.channel_confirmation is ChannelConfirmation.CHANNEL_A
    assert result.kill_reason is KillReason.KILL_AFTER_COMPLETION
    assert ended - float((tmp_path / "a_at").read_text()) >= 2.0


@pytest.mark.anyio
@pytest.mark.timeout(120)
async def test_pending_child_defers_release_until_task_terminal(tmp_path, stop_case):
    _, _, run = stop_case
    result = await run(
        """
        emit_stdout({"type": "task_started", "task_id": "t1"})
        write_marker()
        wait_for("b_deposited")
        emit_stdout({"type": "result", "result": "done\\n" + marker, "session_id": sid})
        time.sleep(1.5)
        touch("task_done_at")
        emit_stdout({"type": "task_notification", "task_id": "t1", "status": "completed"})
        time.sleep(0.5)
        """
    )
    ended = time.time()

    assert result.pending_task_ids == ()
    assert result.completion_ceiling_expired is False
    assert result.returncode == 0
    assert ended >= float((tmp_path / "task_done_at").read_text())
