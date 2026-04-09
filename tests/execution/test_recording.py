"""Tests for RecordingSubprocessRunner and related helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock

import pytest

from autoskillit.core.types import SubprocessRunner, TerminationReason
from autoskillit.execution.commands import build_full_headless_cmd
from autoskillit.execution.recording import (
    RecordingSubprocessRunner,
    _extract_env_and_args,
    _extract_model,
)
from tests.conftest import MockSubprocessRunner, _make_result


@dataclass
class FakeStepResult:
    cassette_exit_code: int
    cassette_path: str
    cassette_duration_ms: int


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

    from autoskillit.config import AutomationConfig
    from autoskillit.execution.process import DefaultSubprocessRunner
    from autoskillit.server._factory import make_context

    ctx = make_context(AutomationConfig(), plugin_dir=str(tmp_path))
    assert isinstance(ctx.runner, DefaultSubprocessRunner)
