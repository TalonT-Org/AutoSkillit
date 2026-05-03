"""Tests for make_context recording/replay runner wiring and related run_headless_core behavior."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import Mock

import pytest

from autoskillit.execution.recording import RecordingSubprocessRunner, ReplayingSubprocessRunner
from tests.conftest import _make_result
from tests.fakes import MockSubprocessRunner

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@dataclass
class FakeStepResult:
    cassette_exit_code: int
    cassette_path: str
    cassette_duration_ms: int


# --- T9: run_headless_core passes scenario_step_name through ---


@pytest.mark.anyio
async def test_run_headless_core_injects_scenario_step_name(tool_ctx, tmp_path):
    """run_headless_core passes step_name as scenario_step_name and routes it via env kwarg."""
    from autoskillit.execution.headless import run_headless_core

    await run_headless_core("/investigate foo", str(tmp_path), tool_ctx, step_name="investigate")

    assert tool_ctx.runner.call_args_list, "runner was never called"
    cmd, _cwd, _timeout, kwargs = tool_ctx.runner.call_args_list[0]
    assert cmd[0] != "env"
    env = kwargs.get("env")
    assert env is not None
    assert env["SCENARIO_STEP_NAME"] == "investigate"


# --- T-AUTO-DERIVE: run_headless_core auto-derives step name ---


@pytest.mark.anyio
async def test_run_headless_core_auto_derives_step_name_when_recording(tmp_path):
    """When runner is RecordingSubprocessRunner and step_name is empty,
    run_headless_core auto-derives step_name from the skill command."""
    from autoskillit.config import AutomationConfig
    from autoskillit.execution.headless import run_headless_core
    from autoskillit.pipeline import DefaultGateState
    from autoskillit.server._factory import make_context

    mock_recorder = Mock()
    mock_recorder.record_step.return_value = FakeStepResult(
        cassette_exit_code=0,
        cassette_path="",
        cassette_duration_ms=100,
    )
    inner = MockSubprocessRunner()
    inner.set_default(_make_result())
    recording_runner = RecordingSubprocessRunner(recorder=mock_recorder, inner=inner)

    ctx = make_context(AutomationConfig(), runner=recording_runner, plugin_dir=str(tmp_path))
    ctx.gate = DefaultGateState(enabled=True)
    ctx.config.linux_tracing.log_dir = str(tmp_path / "logs")

    # Call WITHOUT step_name — auto-derivation should kick in
    await run_headless_core("/autoskillit:smoke-task", str(tmp_path), ctx)

    # record_step must be called with the derived step name
    mock_recorder.record_step.assert_called_once()
    call_kwargs = mock_recorder.record_step.call_args.kwargs
    assert call_kwargs["step_name"] == "smoke-task", (
        f"Expected derived step_name 'smoke-task', got {call_kwargs['step_name']!r}"
    )


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
    mock_atexit = Mock()
    monkeypatch.setattr("atexit.register", mock_atexit)

    from autoskillit.config import AutomationConfig
    from autoskillit.server._factory import make_context

    ctx = make_context(AutomationConfig(), plugin_dir=str(tmp_path))
    assert isinstance(ctx.runner, RecordingSubprocessRunner)
    mock_atexit.assert_not_called()


# --- T11: make_context default runner unchanged without env var ---


def test_make_context_default_runner_without_record_scenario(monkeypatch, tmp_path):
    monkeypatch.delenv("RECORD_SCENARIO", raising=False)
    monkeypatch.delenv("REPLAY_SCENARIO", raising=False)

    from autoskillit.config import AutomationConfig
    from autoskillit.execution.process import DefaultSubprocessRunner
    from autoskillit.server._factory import make_context

    ctx = make_context(AutomationConfig(), plugin_dir=str(tmp_path))
    assert isinstance(ctx.runner, DefaultSubprocessRunner)


# --- T20: make_context wires ReplayingSubprocessRunner when REPLAY_SCENARIO set ---


def test_make_context_wires_sequencing_runner_when_replay_scenario(monkeypatch, tmp_path):
    """REPLAY_SCENARIO=1 + valid dir → ctx.runner is ReplayingSubprocessRunner."""
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
    mock_make_player = Mock(return_value=mock_player)

    import api_simulator.claude as _api_sim_claude

    monkeypatch.setattr(_api_sim_claude, "make_scenario_player", mock_make_player, raising=False)

    from autoskillit.config import AutomationConfig
    from autoskillit.server._factory import make_context

    ctx = make_context(AutomationConfig(), plugin_dir=str(tmp_path))
    assert isinstance(ctx.runner, ReplayingSubprocessRunner)
    mock_make_player.assert_called_once()
    call_kwargs = mock_make_player.call_args.kwargs
    assert call_kwargs.get("scenario_dir") == str(replay_dir)


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
    mock_make_recorder = Mock(return_value=mock_recorder)

    import api_simulator.claude as _api_sim_claude

    monkeypatch.setattr(
        _api_sim_claude, "make_scenario_player", Mock(return_value=mock_player), raising=False
    )
    monkeypatch.setattr(
        _api_sim_claude, "make_scenario_recorder", mock_make_recorder, raising=False
    )
    # Stabilize against xdist ordering: weakref.finalize auto-registers its _exitfunc
    # with atexit.register exactly once per process (guarded by _registered_with_atexit).
    # If this test runs first in an xdist worker, that registration slips through
    # mock_atexit. Force the flag True before mocking so no stray call occurs.
    import weakref as _wrf

    _wrf.finalize._registered_with_atexit = True

    mock_atexit = Mock()
    monkeypatch.setattr("atexit.register", mock_atexit)

    from autoskillit.config import AutomationConfig
    from autoskillit.server._factory import make_context

    ctx = make_context(AutomationConfig(), plugin_dir=str(tmp_path))
    assert isinstance(ctx.runner, ReplayingSubprocessRunner)
    mock_make_recorder.assert_not_called()  # REPLAY takes precedence over RECORD
    mock_atexit.assert_not_called()


# --- T-BUILD-REPLAY-SNAP: build_replay_runner scans and surfaces skill_snapshots ---


def test_build_replay_runner_scans_skill_snapshots(tmp_path, monkeypatch):
    """build_replay_runner() populates runner.skill_snapshots from scenario dir."""

    from autoskillit.execution.recording import build_replay_runner

    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    (replay_dir / "skill-snapshots" / "investigate").mkdir(parents=True)

    mock_scenario = Mock()
    mock_scenario.step_sequence = []
    mock_player = Mock()
    mock_player.scenario.return_value = mock_scenario
    mock_player.build_session_map.return_value = {}

    import api_simulator.claude as _api_sim_claude

    monkeypatch.setattr(
        _api_sim_claude, "make_scenario_player", Mock(return_value=mock_player), raising=False
    )

    runner = build_replay_runner(str(replay_dir))

    assert "investigate" in runner.skill_snapshots
    assert runner.skill_snapshots["investigate"] == replay_dir / "skill-snapshots" / "investigate"


# --- T-RUN-SKILL-REPLAY: snapshot present → init_session NOT called ---


@pytest.mark.anyio
async def test_run_skill_replay_uses_snapshot_over_init_session(tool_ctx, tmp_path, monkeypatch):
    """With a skill snapshot for the step, run_skill skips init_session."""
    from unittest.mock import MagicMock

    from autoskillit.execution.recording import ReplayingSubprocessRunner
    from autoskillit.server.tools.tools_execution import run_skill
    from tests.fakes import InMemoryHeadlessExecutor

    snap_dir = tmp_path / "snap" / "investigate"
    skill_md = snap_dir / ".claude" / "skills" / "investigate" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("# investigate\n", encoding="utf-8")

    replay_runner = ReplayingSubprocessRunner({}, {}, skill_snapshots={"investigate": snap_dir})
    tool_ctx.runner = replay_runner

    mock_ssm = MagicMock()
    tool_ctx.session_skill_manager = mock_ssm

    executor = InMemoryHeadlessExecutor()
    tool_ctx.executor = executor

    ephemeral_root = tmp_path / "sessions"
    tool_ctx.ephemeral_root = ephemeral_root
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    await run_skill("/investigate foo", str(tmp_path), step_name="investigate")

    mock_ssm.init_session.assert_not_called()
    assert len(executor.calls) == 1
    add_dir_paths = [d.path for d in executor.calls[0].add_dirs]
    assert any(p.startswith(str(ephemeral_root)) for p in add_dir_paths)


# --- T-RUN-SKILL-REPLAY-FALLBACK: no snapshot → init_session IS called ---


@pytest.mark.anyio
async def test_run_skill_replay_fallback_to_init_session(tool_ctx, tmp_path, monkeypatch):
    """With no snapshot for the step, run_skill falls back to init_session."""
    from unittest.mock import MagicMock

    from autoskillit.core import ValidatedAddDir
    from autoskillit.execution.recording import ReplayingSubprocessRunner
    from autoskillit.server.tools.tools_execution import run_skill
    from tests.fakes import InMemoryHeadlessExecutor

    replay_runner = ReplayingSubprocessRunner({}, {}, skill_snapshots={})
    tool_ctx.runner = replay_runner

    fake_validated = ValidatedAddDir(path="/fake/session")
    mock_ssm = MagicMock()
    mock_ssm.init_session.return_value = fake_validated
    tool_ctx.session_skill_manager = mock_ssm

    executor = InMemoryHeadlessExecutor()
    tool_ctx.executor = executor

    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    await run_skill("/investigate foo", str(tmp_path), step_name="investigate")

    mock_ssm.init_session.assert_called_once()
