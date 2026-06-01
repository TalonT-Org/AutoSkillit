"""Tests for RecordingSubprocessRunner and related helpers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from autoskillit.core import CLAUDE_CODE_CAPABILITIES, BackendCapabilities
from autoskillit.core.types import (
    DirectInstall,
    OutputFormat,
    SubprocessResult,
    SubprocessRunner,
    TerminationReason,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.recording import (
    RecordingSubprocessRunner,
    ReplayingSubprocessRunner,
    ScenarioReplayError,
    _detect_backend_format,
    _extract_model,
)
from tests.conftest import _make_result
from tests.fakes import MockSubprocessRunner

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_api_sim_claude = pytest.importorskip("api_simulator.claude")

_NON_PTY_CAPABILITIES = BackendCapabilities(
    pty_required=False,
    channel_b_capable=False,
    session_resume_capable=True,
    skill_injection_capable=True,
    supports_thinking_blocks=False,
    supports_claude_format_stdout=False,
    exit_code_is_terminal=True,
    mcp_config_capable=True,
    food_truck_capable=True,
    completion_record_types=frozenset({"turn.completed", "turn.failed", "error"}),
    session_record_types=frozenset({"item.completed"}),
)


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


# --- T7: SCENARIO_STEP_NAME in cmd from build_skill_session_cmd ---

_BASE_CMD_ARGS = dict(
    cwd="/tmp",
    completion_marker="DONE",
    model=None,
    plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
    output_format=OutputFormat.STREAM_JSON,
)


def test_build_skill_session_cmd_injects_scenario_step_name():
    spec = ClaudeCodeBackend().build_skill_session_cmd(
        "/investigate foo",
        scenario_step_name="investigate",
        **_BASE_CMD_ARGS,
    )
    assert spec.env["SCENARIO_STEP_NAME"] == "investigate"
    assert not any("SCENARIO_STEP_NAME" in tok for tok in spec.cmd)


# --- T8: build_skill_session_cmd without scenario_step_name ---


def test_build_skill_session_cmd_no_scenario_step_name():
    spec = ClaudeCodeBackend().build_skill_session_cmd(
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


# --- T-CAPABILITIES-STORED: capabilities stored on construction ---


def test_recording_runner_stores_capabilities():
    """RecordingSubprocessRunner stores capabilities as _capabilities."""
    mock_recorder = Mock()
    runner = RecordingSubprocessRunner(recorder=mock_recorder)
    assert runner._capabilities is CLAUDE_CODE_CAPABILITIES


# --- T-BACKEND-NAME-PTY: default capabilities derives 'claude-code' ---


def test_recording_runner_backend_name_pty():
    """Default CLAUDE_CODE_CAPABILITIES derives _backend_name='claude-code'."""
    mock_recorder = Mock()
    runner = RecordingSubprocessRunner(recorder=mock_recorder)
    assert runner._backend_name == "claude-code"


# --- T-BACKEND-NAME-NONPTY: non-PTY capabilities derives 'codex' ---


def test_recording_runner_backend_name_nonpty():
    """BackendCapabilities(pty_required=False) derives _backend_name='codex'."""
    mock_recorder = Mock()
    runner = RecordingSubprocessRunner(recorder=mock_recorder, capabilities=_NON_PTY_CAPABILITIES)
    assert runner._backend_name == "codex"


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


# --- T-REC-SNAP: RecordingSubprocessRunner snapshots skill dir after recording ---


@pytest.mark.anyio
async def test_recording_runner_snapshots_skill_dir(tmp_path):
    """After _record_session, skill-snapshots/{step_name}/ is written under scenario_dir."""
    ephemeral_dir = tmp_path / "autoskillit-sessions" / "headless-snap01"
    skill_file = ephemeral_dir / ".claude" / "skills" / "investigate" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# investigate\n", encoding="utf-8")

    cassette_path = tmp_path / "sessions" / "investigate"
    cassette_path.mkdir(parents=True)

    mock_recorder = Mock()
    mock_recorder.record_step.return_value = FakeStepResult(
        cassette_exit_code=0,
        cassette_path=str(cassette_path),
        cassette_duration_ms=1000,
    )

    runner = RecordingSubprocessRunner(recorder=mock_recorder, inner=Mock())
    cmd = [
        "claude",
        "--add-dir",
        str(ephemeral_dir),
        "--model",
        "sonnet",
        "--print",
        "go",
    ]
    env = {"SCENARIO_STEP_NAME": "investigate"}
    await runner(cmd, cwd=tmp_path, timeout=60, env=env, pty_mode=True)

    snapshot_dir = tmp_path / "skill-snapshots" / "investigate"
    assert snapshot_dir.exists(), "skill-snapshots/investigate/ not created"
    assert (snapshot_dir / ".claude" / "skills" / "investigate" / "SKILL.md").exists()
    assert (snapshot_dir / "manifest.json").exists()


# --- T-REC-SNAP-SKIP: no --add-dir → no skill_snapshots created ---


@pytest.mark.anyio
async def test_recording_runner_no_ephemeral_dir_skips_snapshot(tmp_path):
    """When cmd has no --add-dir, no skill-snapshots/ directory is created."""
    cassette_path = tmp_path / "sessions" / "investigate"
    cassette_path.mkdir(parents=True)

    mock_recorder = Mock()
    mock_recorder.record_step.return_value = FakeStepResult(
        cassette_exit_code=0,
        cassette_path=str(cassette_path),
        cassette_duration_ms=500,
    )

    runner = RecordingSubprocessRunner(recorder=mock_recorder, inner=Mock())
    cmd = ["claude", "--model", "sonnet", "--print", "go"]
    env = {"SCENARIO_STEP_NAME": "investigate"}
    await runner(cmd, cwd=tmp_path, timeout=60, env=env, pty_mode=True)

    assert not (tmp_path / "skill-snapshots").exists()


# --- T-REPLAY-SNAP: ReplayingSubprocessRunner stores skill_snapshots ---


def test_replaying_runner_stores_skill_snapshots(tmp_path):
    """ReplayingSubprocessRunner.skill_snapshots is populated when provided."""
    snap_path = tmp_path / "skill-snapshots" / "investigate"
    snap_path.mkdir(parents=True)
    skill_snapshots = {"investigate": snap_path}

    runner = ReplayingSubprocessRunner({}, {}, skill_snapshots=skill_snapshots)

    assert runner.skill_snapshots == {"investigate": snap_path}


# --- T-REPLAY-RESTORE: ReplayingSubprocessRunner.restore_skill_snapshot delegates correctly ---


def test_replaying_runner_restore_delegates_correctly(tmp_path):
    """restore_skill_snapshot returns ValidatedAddDir when snapshot exists and files are copied."""
    snap_dir = tmp_path / "snapshot"
    skill_md = snap_dir / ".claude" / "skills" / "investigate" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("# investigate\n", encoding="utf-8")

    runner = ReplayingSubprocessRunner({}, {}, skill_snapshots={"investigate": snap_dir})

    ephemeral_root = tmp_path / "sessions"
    result = runner.restore_skill_snapshot("investigate", ephemeral_root, "headless-xyz")

    assert result is not None
    session_dir = ephemeral_root / "headless-xyz"
    assert (session_dir / ".claude" / "skills" / "investigate" / "SKILL.md").exists()


def test_replaying_runner_restore_missing_step_returns_none(tmp_path):
    """restore_skill_snapshot returns None when no snapshot for step_name."""
    runner = ReplayingSubprocessRunner({}, {}, skill_snapshots={})
    result = runner.restore_skill_snapshot("missing", tmp_path / "sessions", "headless-abc")
    assert result is None


# --- T-MARKER-FWD: RecordingSubprocessRunner forwards marker_dir and session_id to inner ---


@pytest.mark.anyio
async def test_recording_runner_forwards_marker_dir_and_session_id(tmp_path):
    """Non-session branch passes marker_dir and session_id to self._inner()."""
    mock_recorder = Mock()
    inner = MockSubprocessRunner()
    inner.set_default(_make_result(returncode=0))
    runner = RecordingSubprocessRunner(recorder=mock_recorder, inner=inner)

    marker = tmp_path / "markers"
    cmd = ["pytest", "tests/"]
    env = {"SCENARIO_STEP_NAME": "test-check"}

    await runner(
        cmd,
        cwd=tmp_path,
        timeout=60,
        env=env,
        pty_mode=False,
        marker_dir=marker,
        session_id="sess-abc",
    )

    assert len(inner.call_args_list) == 1
    kwargs = inner.call_args_list[0][3]
    assert kwargs["marker_dir"] == marker
    assert kwargs["session_id"] == "sess-abc"


# --- T-MARKER-SESSION-BRANCH: session branch does not forward marker_dir/session_id ---


@pytest.mark.anyio
async def test_recording_runner_session_branch_ignores_marker_params(tmp_path):
    """pty_mode=True + step_name → _record_session; marker_dir/session_id not forwarded."""
    mock_recorder = Mock()
    mock_recorder.record_step.return_value = FakeStepResult(
        cassette_exit_code=0,
        cassette_path=str(tmp_path / "cassette"),
        cassette_duration_ms=5000,
    )
    inner = MockSubprocessRunner()
    runner = RecordingSubprocessRunner(recorder=mock_recorder, inner=inner)

    cmd = ["claude", "--model", "sonnet", "--print", "do stuff"]
    env = {"AUTOSKILLIT_HEADLESS": "1", "SCENARIO_STEP_NAME": "investigate"}

    result = await runner(
        cmd,
        cwd=tmp_path,
        timeout=300,
        env=env,
        pty_mode=True,
        marker_dir=tmp_path / "markers",
        session_id="sess-xyz",
    )

    assert inner.call_args_list == []  # inner NOT called
    assert result.returncode == 0


# --- T-MARKER-REPLAY-ACCEPTS: ReplayingSubprocessRunner accepts marker params ---


@pytest.mark.anyio
async def test_replaying_runner_accepts_marker_params(tmp_path):
    """ReplayingSubprocessRunner.__call__ accepts marker_dir and session_id without error."""
    non_session = {"check": {"exit_code": 0, "stdout_head": "ok", "stderr": ""}}
    runner = ReplayingSubprocessRunner({}, non_session)

    cmd = ["task", "test-check"]
    env = {"SCENARIO_STEP_NAME": "check"}
    result = await runner(
        cmd,
        cwd=tmp_path,
        timeout=60,
        env=env,
        marker_dir=tmp_path / "markers",
        session_id="sess-123",
    )

    assert result.returncode == 0
    assert result.stdout == "ok"


# --- T-DETECT-CODEX: _detect_backend_format returns 'codex' when sidecar exists ---


def test_detect_backend_format_codex(tmp_path):
    """_detect_backend_format returns 'codex' when any subdir contains codex_stdout.ndjson."""
    sidecar = tmp_path / "investigate" / "codex_stdout.ndjson"
    sidecar.parent.mkdir(parents=True)
    sidecar.touch()
    assert _detect_backend_format(tmp_path) == "codex"


# --- T-DETECT-CLAUDE: _detect_backend_format returns 'claude' when no sidecar ---


def test_detect_backend_format_claude(tmp_path):
    """_detect_backend_format returns 'claude' when no codex sidecar exists."""
    assert _detect_backend_format(tmp_path) == "claude"


# --- T-REPLAY-CODEX: build_replay_runner dispatches to CodexScenarioPlayer ---


def test_build_replay_runner_detects_codex_format(tmp_path, monkeypatch):
    """build_replay_runner() uses CodexScenarioPlayer when codex sidecar detected."""
    import sys
    import types
    import weakref

    from autoskillit.execution.recording import build_replay_runner

    replay_dir = tmp_path / "replay"
    sidecar = replay_dir / "investigate" / "codex_stdout.ndjson"
    sidecar.parent.mkdir(parents=True)
    sidecar.touch()

    mock_scenario = Mock()
    mock_scenario.step_sequence = []
    mock_codex_cls = Mock()
    mock_codex_instance = mock_codex_cls.return_value
    mock_codex_instance.scenario.return_value = mock_scenario
    mock_codex_instance.build_session_map.return_value = {}

    fake_codex_mod = types.ModuleType("api_simulator.codex")
    fake_codex_mod.CodexScenarioPlayer = mock_codex_cls  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "api_simulator.codex", fake_codex_mod)

    monkeypatch.setattr(weakref.finalize, "_registered_with_atexit", True)
    monkeypatch.setattr("atexit.register", Mock())

    result = build_replay_runner(str(replay_dir))
    assert result.player is mock_codex_instance


# --- T-REPLAY-CLAUDE: build_replay_runner dispatches to make_scenario_player ---


def test_build_replay_runner_detects_claude_format(tmp_path, monkeypatch):
    """build_replay_runner() uses make_scenario_player when no codex sidecar detected."""
    import weakref

    from autoskillit.execution.recording import build_replay_runner

    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()

    mock_scenario = Mock()
    mock_scenario.step_sequence = []
    mock_player = Mock()
    mock_player.scenario.return_value = mock_scenario
    mock_player.build_session_map.return_value = {}

    monkeypatch.setattr(
        _api_sim_claude, "make_scenario_player", Mock(return_value=mock_player), raising=False
    )
    monkeypatch.setattr(weakref.finalize, "_registered_with_atexit", True)
    monkeypatch.setattr("atexit.register", Mock())

    result = build_replay_runner(str(replay_dir))
    assert result.player is mock_player


# --- T-NONPTY-DISPATCH: Non-PTY Codex step routes to _record_non_pty_session ---


@pytest.mark.anyio
async def test_nonpty_dispatch_routes_to_record_non_pty_session(tmp_path):
    """pty_mode=False + step_name + AGENT_BACKEND=codex → _record_non_pty_session path."""
    mock_recorder = Mock()
    inner = MockSubprocessRunner()
    inner.set_default(_make_result(returncode=0, stdout="codex output"))
    runner = RecordingSubprocessRunner(
        recorder=mock_recorder,
        inner=inner,
        scenario_dir=tmp_path,
        capabilities=_NON_PTY_CAPABILITIES,
    )

    cmd = ["codex", "exec", "--model", "o3", "implement the thing"]
    env = {
        "SCENARIO_STEP_NAME": "codex-step",
    }

    result = await runner(cmd, cwd=Path("/tmp"), timeout=300, env=env, pty_mode=False)

    assert len(inner.call_args_list) == 1
    mock_recorder.record_step.assert_not_called()
    mock_recorder.record_non_session_step.assert_called_once_with(
        step_name="codex-step",
        tool="run_skill",
        result_summary={"exit_code": 0, "stdout_head": "codex output"[:500]},
    )
    assert result.returncode == 0


# --- T-NONPTY-CASSETTES: Cassette files written under scenario_dir/step_name ---


@pytest.mark.anyio
async def test_nonpty_cassettes_written(tmp_path):
    """_record_non_pty_session writes codex_stdout.ndjson and step_meta.json."""
    import json

    scenario_dir = tmp_path / "scenario"
    mock_recorder = Mock()
    expected = SubprocessResult(
        returncode=0,
        stdout="line1\nline2\n",
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=12345,
        elapsed_seconds=2.5,
    )
    inner = MockSubprocessRunner()
    inner.set_default(expected)
    runner = RecordingSubprocessRunner(
        recorder=mock_recorder,
        inner=inner,
        scenario_dir=scenario_dir,
        capabilities=_NON_PTY_CAPABILITIES,
    )

    cmd = ["codex", "exec", "--model", "o3", "go"]
    env = {"SCENARIO_STEP_NAME": "codex-step"}

    await runner(cmd, cwd=Path("/tmp"), timeout=300, env=env, pty_mode=False)

    cassette_dir = scenario_dir / "codex-step"
    assert (cassette_dir / "codex_stdout.ndjson").exists()
    assert (cassette_dir / "step_meta.json").exists()

    ndjson_text = (cassette_dir / "codex_stdout.ndjson").read_text().strip()
    ndjson_lines = ndjson_text.split("\n")
    assert len(ndjson_lines) == 2
    assert json.loads(ndjson_lines[0]) == "line1"
    assert json.loads(ndjson_lines[1]) == "line2"

    meta = json.loads((cassette_dir / "step_meta.json").read_text())
    assert meta == {
        "backend": "codex",
        "model": "o3",
        "exit_code": 0,
        "duration_ms": 2500,
    }


# --- T-NONPTY-RESULT-PASSTHROUGH: Result identity preserved ---


@pytest.mark.anyio
async def test_nonpty_result_passthrough(tmp_path):
    """SubprocessResult from runner is the exact same object from inner."""
    mock_recorder = Mock()
    expected = _make_result(returncode=42, stdout="raw codex out")
    inner = MockSubprocessRunner()
    inner.set_default(expected)
    runner = RecordingSubprocessRunner(
        recorder=mock_recorder,
        inner=inner,
        scenario_dir=tmp_path,
        capabilities=_NON_PTY_CAPABILITIES,
    )

    cmd = ["codex", "exec", "--model", "o3", "go"]
    env = {"SCENARIO_STEP_NAME": "step"}

    result = await runner(cmd, cwd=Path("/tmp"), timeout=300, env=env, pty_mode=False)

    assert result is expected
    assert result.returncode == 42
    assert result.stdout == "raw codex out"


# --- T-PTY-UNAFFECTED: PTY path unchanged by new non-PTY branch ---


@pytest.mark.anyio
async def test_pty_unaffected_by_nonpty_branch(tmp_path):
    """pty_mode=True + step_name → _record_session, not _record_non_pty_session."""
    mock_recorder = Mock()
    mock_recorder.record_step.return_value = FakeStepResult(
        cassette_exit_code=0,
        cassette_path=str(tmp_path / "cassette"),
        cassette_duration_ms=5000,
    )
    inner = MockSubprocessRunner()
    runner = RecordingSubprocessRunner(recorder=mock_recorder, inner=inner)

    cmd = ["claude", "--model", "sonnet", "--print", "do stuff"]
    env = {"SCENARIO_STEP_NAME": "investigate", "AUTOSKILLIT_HEADLESS": "1"}

    result = await runner(cmd, cwd=Path("/tmp"), timeout=300, env=env, pty_mode=True)

    mock_recorder.record_step.assert_called_once()
    assert inner.call_args_list == []
    mock_recorder.record_non_session_step.assert_not_called()
    assert result.returncode == 0


# --- T-NONPTY-NO-STEP-NAME: No step_name → pass-through, no recording ---


@pytest.mark.anyio
async def test_nonpty_no_step_name_skips_recording():
    """pty_mode=False + no SCENARIO_STEP_NAME → inner runner, no recording."""
    mock_recorder = Mock()
    inner = MockSubprocessRunner()
    inner.set_default(_make_result(returncode=0))
    runner = RecordingSubprocessRunner(
        recorder=mock_recorder,
        inner=inner,
        capabilities=_NON_PTY_CAPABILITIES,
    )

    cmd = ["codex", "exec", "do something"]
    env = {}

    await runner(cmd, cwd=Path("/tmp"), timeout=60, env=env, pty_mode=False)

    assert len(inner.call_args_list) == 1
    mock_recorder.record_step.assert_not_called()
    mock_recorder.record_non_session_step.assert_not_called()


# --- T-NONPTY-T3-BOUNDARY: Non-Codex non-PTY with step_name → run_cmd ---


@pytest.mark.anyio
async def test_nonpty_t3_boundary_non_codex_routes_to_run_cmd():
    """pty_mode=False + step_name + no AGENT_BACKEND → record_non_session_step(run_cmd)."""
    mock_recorder = Mock()
    inner = MockSubprocessRunner()
    inner.set_default(_make_result(returncode=0))
    runner = RecordingSubprocessRunner(recorder=mock_recorder, inner=inner)

    cmd = ["task", "test-check"]
    env = {"SCENARIO_STEP_NAME": "test-check"}

    await runner(cmd, cwd=Path("/tmp"), timeout=60, env=env, pty_mode=False)

    assert len(inner.call_args_list) == 1
    mock_recorder.record_non_session_step.assert_called_once_with(
        step_name="test-check",
        tool="run_cmd",
        result_summary={"exit_code": 0, "stdout_head": ""},
    )
