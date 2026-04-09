"""Tests for RecordingSubprocessRunner and related helpers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock

import pytest

from autoskillit.core.types import SubprocessRunner, TerminationReason
from autoskillit.execution.commands import build_full_headless_cmd
from autoskillit.execution.recording import (
    RecordingSubprocessRunner,
    ScenarioReplayError,
    SequencingSubprocessRunner,
    _extract_env_and_args,
    _extract_model,
)
from tests.conftest import MockSubprocessRunner, _make_result


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


# --- T1: Protocol compliance ---


def test_recording_runner_satisfies_protocol():
    """RecordingSubprocessRunner is a valid SubprocessRunner."""
    mock_recorder = Mock()
    runner = RecordingSubprocessRunner(recorder=mock_recorder)
    assert isinstance(runner, SubprocessRunner)


# --- T2: Session call routes to record_step ---


@pytest.mark.anyio
async def test_session_call_routes_to_record_step(tmp_path):
    """pty_mode=True + SCENARIO_STEP_NAME → record_step(), not inner runner."""
    mock_recorder = Mock()
    mock_recorder.record_step.return_value = FakeStepResult(
        cassette_exit_code=0,
        cassette_path=str(tmp_path / "cassette"),
        cassette_duration_ms=5000,
    )
    inner = MockSubprocessRunner()
    runner = RecordingSubprocessRunner(recorder=mock_recorder, inner=inner)

    cmd = [
        "env",
        "AUTOSKILLIT_HEADLESS=1",
        "SCENARIO_STEP_NAME=investigate",
        "claude",
        "--model",
        "sonnet",
        "--print",
        "do stuff",
    ]

    result = await runner(cmd, cwd=Path("/tmp"), timeout=300, pty_mode=True)

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

    cmd = ["env", "SCENARIO_STEP_NAME=test-check", "pytest", "tests/"]

    result = await runner(cmd, cwd=Path("/tmp"), timeout=60, pty_mode=False)

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

    cmd = ["env", "AUTOSKILLIT_HEADLESS=1", "claude", "--print", "test"]

    await runner(cmd, cwd=Path("/tmp"), timeout=300, pty_mode=True)

    assert len(inner.call_args_list) == 1  # inner called (no recording intercept)
    mock_recorder.record_step.assert_not_called()
    mock_recorder.record_non_session_step.assert_not_called()


# --- T5: _extract_env_and_args parsing ---


def test_extract_env_and_args():
    cmd = ["env", "A=1", "B=hello", "claude", "--print", "do stuff"]
    env_dict, clean_args = _extract_env_and_args(cmd)
    assert env_dict == {"A": "1", "B": "hello"}
    assert clean_args == ["claude", "--print", "do stuff"]


def test_extract_env_and_args_no_env_prefix():
    cmd = ["claude", "--print", "do stuff"]
    env_dict, clean_args = _extract_env_and_args(cmd)
    assert env_dict == {}
    assert clean_args == ["claude", "--print", "do stuff"]


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
    cmd = build_full_headless_cmd(
        "/investigate foo",
        scenario_step_name="investigate",
        **_BASE_CMD_ARGS,
    )
    assert "SCENARIO_STEP_NAME=investigate" in cmd


# --- T8: build_full_headless_cmd without scenario_step_name ---


def test_build_full_headless_cmd_no_scenario_step_name():
    cmd = build_full_headless_cmd(
        "/investigate foo",
        **_BASE_CMD_ARGS,
    )
    assert not any("SCENARIO_STEP_NAME" in token for token in cmd)


# --- T9: run_headless_core passes scenario_step_name through ---


@pytest.mark.anyio
async def test_run_headless_core_injects_scenario_step_name(tmp_path):
    """run_headless_core passes step_name as scenario_step_name to cmd builder."""
    from autoskillit.config import AutomationConfig
    from autoskillit.execution.headless import run_headless_core
    from autoskillit.pipeline import DefaultGateState
    from autoskillit.server._factory import make_context

    mock_runner = MockSubprocessRunner()
    mock_runner.set_default(_make_result())
    ctx = make_context(AutomationConfig(), runner=mock_runner, plugin_dir=str(tmp_path))
    ctx.gate = DefaultGateState(enabled=True)

    await run_headless_core("/investigate foo", str(tmp_path), ctx, step_name="investigate")

    cmd = mock_runner.call_args_list[0][0]
    assert "SCENARIO_STEP_NAME=investigate" in cmd


# --- T10: make_context wraps runner when RECORD_SCENARIO set ---


def test_make_context_wraps_runner_when_record_scenario(monkeypatch, tmp_path):
    scenario_dir = tmp_path / "scenario"
    scenario_dir.mkdir()
    monkeypatch.setenv("RECORD_SCENARIO", "1")
    monkeypatch.setenv("RECORD_SCENARIO_DIR", str(scenario_dir))
    monkeypatch.setenv("RECORD_SCENARIO_RECIPE", "smoke-test")
    mock_recorder = Mock()
    import api_simulator.claude as _api_sim_claude

    monkeypatch.setattr(
        _api_sim_claude, "make_scenario_recorder", Mock(return_value=mock_recorder), raising=False
    )
    monkeypatch.setattr("atexit.register", Mock())

    from autoskillit.config import AutomationConfig
    from autoskillit.server._factory import make_context

    ctx = make_context(AutomationConfig(), plugin_dir=str(tmp_path))
    assert isinstance(ctx.runner, RecordingSubprocessRunner)


# --- T11: make_context default runner unchanged without env var ---


def test_make_context_default_runner_without_record_scenario(monkeypatch, tmp_path):
    monkeypatch.delenv("RECORD_SCENARIO", raising=False)
    monkeypatch.delenv("REPLAY_SCENARIO", raising=False)

    from autoskillit.config import AutomationConfig
    from autoskillit.execution.process import DefaultSubprocessRunner
    from autoskillit.server._factory import make_context

    ctx = make_context(AutomationConfig(), plugin_dir=str(tmp_path))
    assert isinstance(ctx.runner, DefaultSubprocessRunner)


# --- T12: Protocol conformance ---


def test_sequencing_runner_satisfies_protocol():
    """SequencingSubprocessRunner is a valid SubprocessRunner."""
    runner = SequencingSubprocessRunner({}, {})
    assert isinstance(runner, SubprocessRunner)


# --- T13: Session step dispatch via FakeClaudeCLI replay ---


@pytest.mark.anyio
async def test_sequencing_session_step_dispatch(tmp_path):
    """Step in session_map → popleft, cli.run(), return SubprocessResult from meta."""
    cli = FakeCLI(stdout="session output", returncode=0)
    meta = FakeMeta(exit_code=0, duration_ms=2000)
    session_map: dict[str, deque] = {"implement": deque([(cli, meta)])}
    runner = SequencingSubprocessRunner(session_map, {})

    cmd = ["env", "SCENARIO_STEP_NAME=implement", "claude", "--print", "do stuff"]
    result = await runner(cmd, cwd=tmp_path, timeout=60)

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
    runner = SequencingSubprocessRunner({}, non_session)

    cmd = ["env", "SCENARIO_STEP_NAME=test-check", "task", "test-check"]
    result = await runner(cmd, cwd=tmp_path, timeout=60)

    assert result.returncode == 1
    assert result.stdout == "FAILED"
    assert result.stderr == "error output"
    assert result.termination == TerminationReason.NATURAL_EXIT


# --- T15: Missing step name raises ValueError ---


@pytest.mark.anyio
async def test_sequencing_missing_step_name_raises(tmp_path):
    """No SCENARIO_STEP_NAME in cmd → ValueError."""
    runner = SequencingSubprocessRunner({}, {})
    cmd = ["claude", "--print", "test"]
    with pytest.raises(ValueError, match="SCENARIO_STEP_NAME"):
        await runner(cmd, cwd=tmp_path, timeout=60)


# --- T16: Unknown step raises ScenarioReplayError ---


@pytest.mark.anyio
async def test_sequencing_unknown_step_raises(tmp_path):
    """Step not in session_map or non_session → ScenarioReplayError with guidance."""
    runner = SequencingSubprocessRunner({"known": deque()}, {"other": {}})
    cmd = ["env", "SCENARIO_STEP_NAME=unknown-step", "claude", "--print", "test"]
    with pytest.raises(ScenarioReplayError) as exc_info:
        await runner(cmd, cwd=tmp_path, timeout=60)
    msg = str(exc_info.value)
    assert "unknown-step" in msg
    assert "add_fallback" in msg
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
    runner = SequencingSubprocessRunner(session_map, non_session)

    cmd1 = ["env", "SCENARIO_STEP_NAME=run", "claude", "--print", "go"]
    cmd2 = ["env", "SCENARIO_STEP_NAME=check", "task", "test"]

    await runner(cmd1, cwd=tmp_path, timeout=60)
    await runner(cmd2, cwd=tmp_path, timeout=60)

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
    runner = SequencingSubprocessRunner(session_map, {})

    cmd = ["env", "SCENARIO_STEP_NAME=implement", "claude", "--print", "go"]
    result1 = await runner(cmd, cwd=tmp_path, timeout=60)
    result2 = await runner(cmd, cwd=tmp_path, timeout=60)

    assert result1.stdout == "first"
    assert result2.stdout == "second"


# --- T19: Exhausted session deque falls through to non-session ---


@pytest.mark.anyio
async def test_sequencing_exhausted_session_falls_to_non_session(tmp_path):
    """When session deque is empty but non_session has entry, use non_session."""
    non_session = {"test": {"exit_code": 2, "stdout_head": "non-session result", "stderr": ""}}
    session_map: dict[str, deque] = {"test": deque()}
    runner = SequencingSubprocessRunner(session_map, non_session)

    cmd = ["env", "SCENARIO_STEP_NAME=test", "task", "test"]
    result = await runner(cmd, cwd=tmp_path, timeout=60)

    assert result.returncode == 2
    assert result.stdout == "non-session result"


# --- T20: make_context wires SequencingSubprocessRunner when REPLAY_SCENARIO set ---


def test_make_context_wires_sequencing_runner_when_replay_scenario(monkeypatch, tmp_path):
    """REPLAY_SCENARIO=1 + valid dir → ctx.runner is SequencingSubprocessRunner."""
    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    monkeypatch.setenv("REPLAY_SCENARIO", "1")
    monkeypatch.setenv("REPLAY_SCENARIO_DIR", str(replay_dir))
    monkeypatch.delenv("RECORD_SCENARIO", raising=False)

    mock_scenario = Mock()
    mock_scenario.step_sequence = []
    mock_player = Mock()
    mock_player.scenario.return_value = mock_scenario
    mock_player.build_session_map.return_value = {}

    import api_simulator.claude as _api_sim_claude

    monkeypatch.setattr(
        _api_sim_claude, "make_scenario_player", Mock(return_value=mock_player), raising=False
    )

    from autoskillit.config import AutomationConfig
    from autoskillit.server._factory import make_context

    ctx = make_context(AutomationConfig(), plugin_dir=str(tmp_path))
    assert isinstance(ctx.runner, SequencingSubprocessRunner)


# --- T21: REPLAY_SCENARIO takes precedence over RECORD_SCENARIO ---


def test_replay_takes_precedence_over_record(monkeypatch, tmp_path):
    """When both REPLAY and RECORD env vars set, REPLAY wins."""
    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    monkeypatch.setenv("REPLAY_SCENARIO", "1")
    monkeypatch.setenv("REPLAY_SCENARIO_DIR", str(replay_dir))
    monkeypatch.setenv("RECORD_SCENARIO", "1")
    monkeypatch.setenv("RECORD_SCENARIO_DIR", str(replay_dir))

    mock_scenario = Mock()
    mock_scenario.step_sequence = []
    mock_player = Mock()
    mock_player.scenario.return_value = mock_scenario
    mock_player.build_session_map.return_value = {}
    mock_recorder = Mock()

    import api_simulator.claude as _api_sim_claude

    monkeypatch.setattr(
        _api_sim_claude, "make_scenario_player", Mock(return_value=mock_player), raising=False
    )
    monkeypatch.setattr(
        _api_sim_claude, "make_scenario_recorder", Mock(return_value=mock_recorder), raising=False
    )
    monkeypatch.setattr("atexit.register", Mock())

    from autoskillit.config import AutomationConfig
    from autoskillit.server._factory import make_context

    ctx = make_context(AutomationConfig(), plugin_dir=str(tmp_path))
    assert isinstance(ctx.runner, SequencingSubprocessRunner)


# --- T22: Cross-scenario session override (integration, requires api-simulator) ---


@pytest.mark.anyio
async def test_cross_scenario_override(tmp_path):
    """ScenarioBuilder + cross-scenario override → SequencingSubprocessRunner replays override."""
    api_sim = pytest.importorskip("api_simulator.claude")

    b1 = api_sim.make_scenario_builder("recipe1")
    b1.add_synthetic_step("implement", exit_code=0, stdout_lines=["from-scenario1"])

    b2 = api_sim.make_scenario_builder("recipe2")
    b2.add_synthetic_step("implement", exit_code=0, stdout_lines=["from-scenario2"])

    scenario_dir1 = tmp_path / "s1"
    scenario_dir1.mkdir()
    scenario_dir2 = tmp_path / "s2"
    scenario_dir2.mkdir()
    binary = str(tmp_path / "claude")

    player1 = b1.build(output_dir=str(scenario_dir1), binary_path=binary)
    player2 = b2.build(output_dir=str(scenario_dir2), binary_path=binary)

    raw_map1 = player1.build_session_map()
    raw_map2 = player2.build_session_map()
    assert "implement" in raw_map1
    assert "implement" in raw_map2

    # Override: replace player1's "implement" with a controlled FakeCLI (cross-scenario injection)
    override_cli = FakeCLI(stdout="from-overridden-scenario2", returncode=0)
    override_meta = FakeMeta(exit_code=0, duration_ms=500)
    session_map: dict[str, deque] = {"implement": deque([(override_cli, override_meta)])}

    runner = SequencingSubprocessRunner(session_map, {})
    cmd = ["env", "SCENARIO_STEP_NAME=implement", "claude", "--print", "go"]
    result = await runner(cmd, cwd=tmp_path, timeout=60)

    assert result.stdout == "from-overridden-scenario2"
    assert result.returncode == 0
    assert result.elapsed_seconds == pytest.approx(0.5)
