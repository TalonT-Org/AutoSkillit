"""Tests for RecordingSubprocessRunner and related helpers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from autoskillit.core.types import SubprocessRunner, TerminationReason
from autoskillit.execution.commands import build_full_headless_cmd
from autoskillit.execution.recording import (
    RecordingSubprocessRunner,
    ReplayingSubprocessRunner,
    ScenarioReplayError,
    _extract_model,
)
from tests.conftest import _make_result
from tests.fakes import MockSubprocessRunner

pytestmark = [pytest.mark.layer("execution")]


@dataclass
class FakeStepResult:
    cassette_exit_code: int
    cassette_path: str
    cassette_duration_ms: int


@dataclass
class FakeSessionResult:
    returncode: int
    stdout: str


class FakeCLI:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self._result = FakeSessionResult(returncode, stdout)

    def run(self, args: object = None, env: object = None) -> FakeSessionResult:
        return self._result


@dataclass
class FakeMeta:
    exit_code: int
    model: str = "test"
    duration_ms: int = 1000


# --- T0: RecordingSubprocessRunner must NOT call atexit.register ---


def test_recording_runner_does_not_register_atexit():
    """
    RecordingSubprocessRunner must not register atexit hooks.
    Teardown is owned by the FastMCP server lifespan, not the constructor.
    Regression guard for issue #745.
    """
    mock_recorder = Mock()
    with patch("atexit.register") as mock_atexit:
        RecordingSubprocessRunner(recorder=mock_recorder, inner=Mock())
    mock_atexit.assert_not_called()


# --- T1: Protocol compliance ---


def test_recording_runner_satisfies_protocol():
    """RecordingSubprocessRunner is a valid SubprocessRunner."""
    mock_recorder = Mock()
    runner = RecordingSubprocessRunner(recorder=mock_recorder)
    assert isinstance(runner, SubprocessRunner)


# --- T2: Session call routes to record_step ---


@pytest.mark.anyio
async def test_session_call_routes_to_record_step(tmp_path):
    """pty_mode=True + SCENARIO_STEP_NAME in env kwarg → record_step(), not inner runner."""
    mock_recorder = Mock()
    mock_recorder.record_step.return_value = FakeStepResult(
        cassette_exit_code=0,
        cassette_path=str(tmp_path / "cassette"),
        cassette_duration_ms=5000,
    )
    inner = MockSubprocessRunner()
    runner = RecordingSubprocessRunner(recorder=mock_recorder, inner=inner)

    cmd = ["claude", "--model", "sonnet", "--print", "do stuff"]
    env = {
        "AUTOSKILLIT_HEADLESS": "1",
        "SCENARIO_STEP_NAME": "investigate",
    }

    result = await runner(cmd, cwd=Path("/tmp"), timeout=300, env=env, pty_mode=True)

    mock_recorder.record_step.assert_called_once_with(
        step_name="investigate",
        tool="run_skill",
        args=["claude", "--model", "sonnet", "--print", "do stuff"],
        model="sonnet",
        session_log_dir=None,
    )
    assert inner.call_args_list == []  # inner NOT called
    assert result.returncode == 0
    assert result.termination == TerminationReason.NATURAL_EXIT


# --- T3: Non-session call delegates to inner runner + records summary ---


@pytest.mark.anyio
async def test_non_session_call_delegates_and_records():
    """pty_mode=False → inner runner called, then record_non_session_step()."""
    mock_recorder = Mock()
    inner = MockSubprocessRunner()
    inner.set_default(_make_result(returncode=0))
    runner = RecordingSubprocessRunner(recorder=mock_recorder, inner=inner)

    cmd = ["pytest", "tests/"]
    env = {"SCENARIO_STEP_NAME": "test-check"}

    result = await runner(cmd, cwd=Path("/tmp"), timeout=60, env=env, pty_mode=False)

    assert len(inner.call_args_list) == 1  # inner WAS called
    mock_recorder.record_non_session_step.assert_called_once_with(
        step_name="test-check",
        tool="run_cmd",
        result_summary={"exit_code": 0, "stdout_head": result.stdout[:500]},
    )


# --- T4: No step_name skips recording ---


@pytest.mark.anyio
async def test_no_step_name_skips_recording():
    """Calls without SCENARIO_STEP_NAME go through inner runner unrecorded."""
    mock_recorder = Mock()
    inner = MockSubprocessRunner()
    inner.set_default(_make_result(returncode=0))
    runner = RecordingSubprocessRunner(recorder=mock_recorder, inner=inner)

    cmd = ["claude", "--print", "test"]
    env = {"AUTOSKILLIT_HEADLESS": "1"}

    await runner(cmd, cwd=Path("/tmp"), timeout=300, env=env, pty_mode=True)

    assert len(inner.call_args_list) == 1  # inner called (no recording intercept)
    mock_recorder.record_step.assert_not_called()
    mock_recorder.record_non_session_step.assert_not_called()


# --- T6: _extract_model from args ---


def test_extract_model():
    args = ["claude", "--print", "hello", "--model", "sonnet"]
    assert _extract_model(args) == "sonnet"


def test_extract_model_missing():
    args = ["claude", "--print", "hello"]
    assert _extract_model(args) == ""


# --- T7: SCENARIO_STEP_NAME in cmd from build_full_headless_cmd ---

_BASE_CMD_ARGS = dict(
    cwd="/tmp",
    completion_marker="DONE",
    model=None,
    plugin_dir="/plugins",
    output_format_value="stream-json",
)


def test_build_full_headless_cmd_injects_scenario_step_name():
    spec = build_full_headless_cmd(
        "/investigate foo",
        scenario_step_name="investigate",
        **_BASE_CMD_ARGS,
    )
    assert spec.env["SCENARIO_STEP_NAME"] == "investigate"
    assert not any("SCENARIO_STEP_NAME" in tok for tok in spec.cmd)


# --- T8: build_full_headless_cmd without scenario_step_name ---


def test_build_full_headless_cmd_no_scenario_step_name():
    spec = build_full_headless_cmd(
        "/investigate foo",
        **_BASE_CMD_ARGS,
    )
    assert "SCENARIO_STEP_NAME" not in spec.env
    assert not any("SCENARIO_STEP_NAME" in tok for tok in spec.cmd)


# --- T-DERIVE: _derive_step_name_from_skill_command ---


def test_derive_step_name_from_namespaced_skill():
    """Extract skill name from /autoskillit:skill-name args form."""
    from autoskillit.execution.headless import _derive_step_name_from_skill_command

    assert (
        _derive_step_name_from_skill_command("/autoskillit:smoke-task arg1 arg2") == "smoke-task"
    )
    assert _derive_step_name_from_skill_command("/autoskillit:investigate foo") == "investigate"
    assert _derive_step_name_from_skill_command("  /autoskillit:make-plan  ") == "make-plan"


def test_derive_step_name_from_plain_skill():
    """Extract skill name from /skill-name args form (no namespace prefix)."""
    from autoskillit.execution.headless import _derive_step_name_from_skill_command

    assert _derive_step_name_from_skill_command("/investigate foo") == "investigate"
    assert _derive_step_name_from_skill_command("/smoke-task") == "smoke-task"
    assert _derive_step_name_from_skill_command("plain text no slash") == "plain"
    assert _derive_step_name_from_skill_command("") == ""


# --- T12: Protocol conformance ---


def test_sequencing_runner_satisfies_protocol():
    """ReplayingSubprocessRunner is a valid SubprocessRunner."""
    runner = ReplayingSubprocessRunner({}, {})
    assert isinstance(runner, SubprocessRunner)


# --- T13: Session step dispatch via FakeClaudeCLI replay ---


@pytest.mark.anyio
async def test_sequencing_session_step_dispatch(tmp_path):
    """Step in session_map → popleft, cli.run(), return SubprocessResult from meta."""
    cli = FakeCLI(stdout="session output", returncode=0)
    meta = FakeMeta(exit_code=0, duration_ms=2000)
    session_map: dict[str, deque] = {"implement": deque([(cli, meta)])}
    runner = ReplayingSubprocessRunner(session_map, {})

    cmd = ["claude", "--print", "do stuff"]
    env = {"SCENARIO_STEP_NAME": "implement"}
    result = await runner(cmd, cwd=tmp_path, timeout=60, env=env)

    assert result.returncode == meta.exit_code
    assert result.stdout == "session output"
    assert result.termination == TerminationReason.NATURAL_EXIT
    assert result.elapsed_seconds == meta.duration_ms / 1000.0


# --- T14: Non-session step dispatch via result stub ---


@pytest.mark.anyio
async def test_sequencing_non_session_step_dispatch(tmp_path):
    """Step in non_session_results → return SubprocessResult from summary."""
    non_session = {
        "test-check": {
            "exit_code": 1,
            "stdout_head": "FAILED",
            "stderr": "error output",
        }
    }
    runner = ReplayingSubprocessRunner({}, non_session)

    cmd = ["task", "test-check"]
    env = {"SCENARIO_STEP_NAME": "test-check"}
    result = await runner(cmd, cwd=tmp_path, timeout=60, env=env)

    assert result.returncode == 1
    assert result.stdout == "FAILED"
    assert result.stderr == "error output"
    assert result.termination == TerminationReason.NATURAL_EXIT


# --- T15: Missing step name raises ValueError ---


@pytest.mark.anyio
async def test_sequencing_missing_step_name_raises(tmp_path):
    """No SCENARIO_STEP_NAME in env kwarg → ValueError."""
    runner = ReplayingSubprocessRunner({}, {})
    cmd = ["claude", "--print", "test"]
    with pytest.raises(ValueError, match="SCENARIO_STEP_NAME"):
        await runner(cmd, cwd=tmp_path, timeout=60)


# --- T16: Unknown step raises ScenarioReplayError ---


@pytest.mark.anyio
async def test_sequencing_unknown_step_raises(tmp_path):
    """Step not in session_map or non_session → ScenarioReplayError with guidance."""
    runner = ReplayingSubprocessRunner(
        {"known": deque([(FakeCLI(), FakeMeta(exit_code=0))])}, {"other": {}}
    )
    cmd = ["claude", "--print", "test"]
    env = {"SCENARIO_STEP_NAME": "unknown-step"}
    with pytest.raises(ScenarioReplayError) as exc_info:
        await runner(cmd, cwd=tmp_path, timeout=60, env=env)
    msg = str(exc_info.value)
    assert "unknown-step" in msg
    assert "known" in msg
    assert "other" in msg


# --- T17: call_log records all dispatches ---


@pytest.mark.anyio
async def test_sequencing_call_log(tmp_path):
    """Each __call__ appends (step_name, cmd) to call_log."""
    cli = FakeCLI(stdout="session", returncode=0)
    meta = FakeMeta(exit_code=0, duration_ms=500)
    non_session = {"check": {"exit_code": 0, "stdout_head": "ok", "stderr": ""}}
    session_map: dict[str, deque] = {"run": deque([(cli, meta)])}
    runner = ReplayingSubprocessRunner(session_map, non_session)

    cmd1 = ["claude", "--print", "go"]
    env1 = {"SCENARIO_STEP_NAME": "run"}
    cmd2 = ["task", "test"]
    env2 = {"SCENARIO_STEP_NAME": "check"}

    await runner(cmd1, cwd=tmp_path, timeout=60, env=env1)
    await runner(cmd2, cwd=tmp_path, timeout=60, env=env2)

    assert len(runner.call_log) == 2
    assert runner.call_log[0] == ("run", cmd1)
    assert runner.call_log[1] == ("check", cmd2)


# --- T18: Multiple calls to same step advance the deque ---


@pytest.mark.anyio
async def test_sequencing_multiple_calls_advance_queue(tmp_path):
    """Successive calls to same step popleft through the deque."""
    cli1 = FakeCLI(stdout="first", returncode=0)
    cli2 = FakeCLI(stdout="second", returncode=0)
    meta1 = FakeMeta(exit_code=0, duration_ms=100)
    meta2 = FakeMeta(exit_code=0, duration_ms=200)
    session_map: dict[str, deque] = {"implement": deque([(cli1, meta1), (cli2, meta2)])}
    runner = ReplayingSubprocessRunner(session_map, {})

    cmd = ["claude", "--print", "go"]
    env = {"SCENARIO_STEP_NAME": "implement"}
    result1 = await runner(cmd, cwd=tmp_path, timeout=60, env=env)
    result2 = await runner(cmd, cwd=tmp_path, timeout=60, env=env)

    assert result1.stdout == "first"
    assert result2.stdout == "second"


# --- T19: Exhausted session deque falls through to non-session ---


@pytest.mark.anyio
async def test_sequencing_exhausted_session_falls_to_non_session(tmp_path):
    """When session deque is empty but non_session has entry, use non_session."""
    non_session = {"test": {"exit_code": 2, "stdout_head": "non-session result", "stderr": ""}}
    session_map: dict[str, deque] = {"test": deque()}
    runner = ReplayingSubprocessRunner(session_map, non_session)

    cmd = ["task", "test"]
    env = {"SCENARIO_STEP_NAME": "test"}
    result = await runner(cmd, cwd=tmp_path, timeout=60, env=env)

    assert result.returncode == 2
    assert result.stdout == "non-session result"


# --- T22: Cross-scenario session override (integration, requires api-simulator) ---


@pytest.mark.anyio
async def test_cross_scenario_override(tmp_path):
    """Cross-scenario session injection → ReplayingSubprocessRunner replays override."""
    # Simulate two scenarios providing the same step; override with a controlled FakeCLI
    # to verify that ReplayingSubprocessRunner uses whatever session_map it is given,
    # regardless of which scenario recorded a given step name.
    override_cli = FakeCLI(stdout="from-overridden-scenario2", returncode=0)
    override_meta = FakeMeta(exit_code=0, duration_ms=500)
    session_map: dict[str, deque] = {"implement": deque([(override_cli, override_meta)])}

    runner = ReplayingSubprocessRunner(session_map, {})
    cmd = ["claude", "--print", "go"]
    env = {"SCENARIO_STEP_NAME": "implement"}
    result = await runner(cmd, cwd=tmp_path, timeout=60, env=env)

    assert result.stdout == "from-overridden-scenario2"
    assert result.returncode == 0
    assert result.elapsed_seconds == pytest.approx(0.5)


# --- T-REC-PUBLIC: recorder is a public attribute ---


def test_recording_runner_recorder_is_public():
    """RecordingSubprocessRunner exposes recorder as a public attribute."""
    mock_recorder = Mock()
    runner = RecordingSubprocessRunner(recorder=mock_recorder)
    assert runner.recorder is mock_recorder


# --- T-REPLAY-PLAYER: player attribute stored ---


def test_replaying_runner_stores_player_attribute():
    """ReplayingSubprocessRunner stores player when provided."""
    mock_player = Mock()
    runner = ReplayingSubprocessRunner({}, {}, player=mock_player)
    assert runner.player is mock_player


# --- T-REPLAY-PLAYER-NONE: player defaults to None ---


def test_replaying_runner_player_defaults_to_none():
    """ReplayingSubprocessRunner.player is None when not provided."""
    runner = ReplayingSubprocessRunner({}, {})
    assert runner.player is None


# --- T-BUILD-REPLAY-PLAYER: build_replay_runner stores player on runner ---


def test_build_replay_runner_stores_player_on_runner(tmp_path, monkeypatch):
    """build_replay_runner() passes the ScenarioPlayer to ReplayingSubprocessRunner.player."""
    from autoskillit.execution.recording import build_replay_runner

    mock_scenario = Mock()
    mock_scenario.step_sequence = []
    mock_player = Mock()
    mock_player.scenario.return_value = mock_scenario
    mock_player.build_session_map.return_value = {}

    import api_simulator.claude as _api_sim_claude

    monkeypatch.setattr(
        _api_sim_claude, "make_scenario_player", Mock(return_value=mock_player), raising=False
    )
    import weakref

    # weakref.finalize registers _exitfunc with atexit on first use in a process.
    # Pre-set the class flag so that registration doesn't happen under the mock.
    monkeypatch.setattr(weakref.finalize, "_registered_with_atexit", True)
    mock_atexit = Mock()
    monkeypatch.setattr("atexit.register", mock_atexit)

    result = build_replay_runner(str(tmp_path))
    assert result.player is mock_player
    mock_atexit.assert_not_called()
